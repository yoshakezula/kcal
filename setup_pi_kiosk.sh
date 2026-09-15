#!/bin/sh
# Sets up this Pi as a cage/Chromium kiosk for the Kcal app, per the
# "Raspberry Pi OS Lite (no desktop) — cage kiosk" section of README.md.
#
# Run as root on a freshly flashed Raspberry Pi OS Lite install, from
# inside the cloned repo:
#   sudo sh setup_pi_kiosk.sh
#
# This account is who the autologin/kiosk session and kcal.service run as,
# and it varies per machine, so the script requires it — there's no
# guessed default, since a wrong guess would silently misconfigure the
# kiosk. Either pass it as the first argument:
#   sudo sh setup_pi_kiosk.sh yoshakezula
# or leave it off and answer the interactive prompt:
#   Username to run the kiosk as:
# Leaving that prompt blank (just pressing Enter) is treated the same as
# not specifying one at all — the script errors out rather than guessing.
#
# Safe to re-run — every step is idempotent (checks before writing).

set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo (needs root for apt/systemd/boot config)." >&2
    exit 1
fi

REPO_DIR=$(cd "$(dirname "$0")" && pwd)
if [ ! -f "$REPO_DIR/app.py" ]; then
    echo "Couldn't find app.py next to this script — run it from inside the kcal repo." >&2
    exit 1
fi

if [ -n "$1" ]; then
    USERNAME=$1
else
    printf 'Username to run the kiosk as: '
    read -r USERNAME
fi

if [ -z "$USERNAME" ]; then
    echo "Error: no username specified. Pass it as an argument (sudo sh setup_pi_kiosk.sh <username>) or enter one at the prompt." >&2
    exit 1
fi

if ! id "$USERNAME" >/dev/null 2>&1; then
    echo "No such user: $USERNAME" >&2
    exit 1
fi
USER_HOME=$(eval echo "~$USERNAME")

echo "==> Using user '$USERNAME' (home: $USER_HOME), repo at $REPO_DIR"

# Grants $USERNAME passwordless sudo for one exact command, via the shared
# /etc/sudoers.d/kcal-restart file. Idempotent — checks before appending, so
# both the auto-deploy and scheduled-restart blocks below can each ensure
# just the line(s) they need without clobbering the other's.
ensure_sudoers_line() {
    cmd=$1
    touch /etc/sudoers.d/kcal-restart
    if grep -qF "$cmd" /etc/sudoers.d/kcal-restart 2>/dev/null; then
        return
    fi
    echo "$USERNAME ALL=(ALL) NOPASSWD: $cmd" >> /etc/sudoers.d/kcal-restart
    chmod 440 /etc/sudoers.d/kcal-restart
}

# ---------- Packages ----------

echo "==> Installing cage, Chromium, and a color emoji font"
apt update
# The UI font (Inter) is served by the app itself from static/fonts, so
# there's no font package to install here beyond the emoji face the
# weather icons need.
apt install --no-install-recommends -y cage chromium-browser fonts-noto-color-emoji
fc-cache -f

# ---------- Console autologin on tty1 ----------

echo "==> Enabling console autologin for $USERNAME on tty1"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $USERNAME --noclear %I \$TERM
EOF
systemctl daemon-reload

# ---------- kcal.service ----------

PYTHON_BIN="/usr/bin/python3"
for venv_name in venv myenv .venv env; do
    for base in "$REPO_DIR" "$USER_HOME"; do
        if [ -x "$base/$venv_name/bin/python" ]; then
            PYTHON_BIN="$base/$venv_name/bin/python"
        fi
    done
done
echo "==> Using Python interpreter: $PYTHON_BIN"
if [ "$PYTHON_BIN" = "/usr/bin/python3" ]; then
    echo "    WARNING: no virtualenv found under $REPO_DIR or $USER_HOME."
    echo "    If your dependencies are installed in a differently-named venv,"
    echo "    edit ExecStart in /etc/systemd/system/kcal.service afterward."
fi

echo "==> Writing /etc/systemd/system/kcal.service"
cat > /etc/systemd/system/kcal.service << EOF
[Unit]
Description=Kcal calendar kiosk
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USERNAME
WorkingDirectory=$REPO_DIR
ExecStartPre=-/usr/bin/git -C $REPO_DIR pull origin main
ExecStartPre=-$PYTHON_BIN -m pip install -q -r $REPO_DIR/requirements.txt
ExecStart=$PYTHON_BIN $REPO_DIR/app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now kcal.service


# ---------- Blank cursor theme ----------

# cage draws a pointer at the centre of the screen as soon as it starts, and
# on a touch-only kiosk nothing ever moves it: touch events don't drive the
# Wayland pointer, so Chromium never receives a pointer-enter and never gets
# the chance to apply the page's own `cursor: none`. The arrow just sits
# there forever. Giving cage a cursor theme whose glyphs are fully
# transparent removes it at the compositor level, before any of that
# matters. cage has no flag for this and ignores XCURSOR_SIZE.
echo "==> Installing a transparent cursor theme for the kiosk"
CURSOR_DIR="$USER_HOME/.icons/blank/cursors"
mkdir -p "$CURSOR_DIR"
python3 - "$CURSOR_DIR" << 'PYEOF'
import os, struct, sys

# Minimal Xcursor file, same layout a real theme uses: file header, a TOC
# with one entry per nominal size, then an image chunk per size. Every
# pixel is transparent, so there is nothing to draw.
IMAGE_TYPE = 0xfffd0002
SIZES = (24, 32, 48, 64)  # what stock themes ship; wlroots asks for 24

out = bytearray(b"Xcur" + struct.pack("<III", 16, 0x00010000, len(SIZES)))
chunks, offsets = bytearray(), []
base = 16 + 12 * len(SIZES)
for s in SIZES:
    offsets.append(base + len(chunks))
    chunks += struct.pack("<IIII", 36, IMAGE_TYPE, s, 1)
    chunks += struct.pack("<IIIII", s, s, 0, 0, 0)  # w, h, xhot, yhot, delay
    chunks += b"\x00\x00\x00\x00" * (s * s)
for s, off in zip(SIZES, offsets):
    out += struct.pack("<III", IMAGE_TYPE, s, off)
out += chunks

d = sys.argv[1]
with open(os.path.join(d, "left_ptr"), "wb") as f:
    f.write(bytes(out))
# Point every name the compositor might ask for at the same blank glyph.
for name in ("default", "arrow", "top_left_arrow", "pointer", "hand1",
             "hand2", "xterm", "text", "watch", "left_ptr_watch", "progress"):
    p = os.path.join(d, name)
    if os.path.lexists(p):
        os.remove(p)
    os.symlink("left_ptr", p)
PYEOF
cat > "$USER_HOME/.icons/blank/index.theme" << 'EOF'
[Icon Theme]
Name=blank
Comment=Fully transparent cursor, so the kiosk never shows a pointer
EOF
chown -R "$USERNAME:$USERNAME" "$USER_HOME/.icons"
# ---------- cage + Chromium autostart ----------

BASH_PROFILE="$USER_HOME/.bash_profile"
MARKER="# --- kcal kiosk autostart ---"
END_MARKER="# --- end kcal kiosk autostart ---"
# The block is rewritten rather than skipped, so a re-run actually updates
# the Chromium flags on a Pi that was set up before they changed. Blocks
# written before END_MARKER existed end at the first column-0 `fi` instead.
if [ -f "$BASH_PROFILE" ] && grep -qF "$MARKER" "$BASH_PROFILE"; then
    echo "==> Replacing existing kiosk autostart block in $BASH_PROFILE"
    cp "$BASH_PROFILE" "$BASH_PROFILE.bak"
    if grep -qF "$END_MARKER" "$BASH_PROFILE"; then
        sed -i "\|^$MARKER$|,\|^$END_MARKER$|d" "$BASH_PROFILE"
    else
        sed -i "\|^$MARKER$|,\|^fi$|d" "$BASH_PROFILE"
    fi
else
    echo "==> Adding kiosk autostart to $BASH_PROFILE"
fi
cat >> "$BASH_PROFILE" << 'EOF'
# --- kcal kiosk autostart ---
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  # Blank cursor theme, installed above - cage picks this up at startup.
  export XCURSOR_THEME=blank
  until curl -s http://127.0.0.1:5000 > /dev/null; do sleep 1; done
  while true; do
    # The memory flags matter on a 1GB Pi, where Chromium is the biggest
    # consumer. --process-per-site with --renderer-process-limit=1 and Site
    # Isolation off collapse what is otherwise a second renderer process --
    # isolation buys nothing for a kiosk showing one trusted local origin,
    # though it would matter if this ever browsed the open web. The
    # --disable-* group drops background machinery a kiosk never uses, and
    # the V8 cap keeps a runaway page from eating the whole box.
    cage -- chromium-browser --kiosk --noerrdialogs --disable-infobars \
      --disable-session-crashed-bubble --check-for-update-interval=31536000 \
      --password-store=basic --incognito \
      --process-per-site --renderer-process-limit=1 \
      --disable-features=site-per-process,IsolateOrigins,BackForwardCache,Translate \
      --disable-background-networking --disable-sync --disable-component-update \
      --disable-breakpad --disable-domain-reliability \
      --js-flags=--max-old-space-size=64 \
      http://127.0.0.1:5000
    sleep 2
  done
fi
# --- end kcal kiosk autostart ---
EOF
chown "$USERNAME:$USERNAME" "$BASH_PROFILE"


# ---------- Memory tuning ----------

# On a 1GB Pi, Chromium spends most of its life in swap, so how well swap
# compresses decides how much headroom there is. zram-tools defaults to
# lz4; zstd compresses noticeably better for a little CPU, and this box is
# otherwise idle.
echo "==> Tuning zram and swappiness"
if [ -f /etc/default/zramswap ]; then
    if grep -q '^ALGO=' /etc/default/zramswap; then
        sed -i 's/^ALGO=.*/ALGO=zstd/' /etc/default/zramswap
    else
        echo 'ALGO=zstd' >> /etc/default/zramswap
    fi
    # Restarting zramswap means swapoff, which pulls everything compressed
    # in zram back into real RAM. If that is more than the box currently
    # has free, the restart is what finally triggers the OOM killer. Only
    # do it when there is clearly room; otherwise it waits for the reboot.
    zram_used_kb=$(awk '$1 == "/dev/zram0" {print $4}' /proc/swaps 2>/dev/null)
    mem_avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    if [ "${zram_used_kb:-0}" -lt "$(( ${mem_avail_kb:-0} / 2 ))" ]; then
        systemctl restart zramswap.service
        echo "    zram now using zstd"
    else
        echo "    zram holds ${zram_used_kb}kB with only ${mem_avail_kb}kB available,"
        echo "    so it wasn't restarted - zstd takes effect on the next reboot."
    fi
else
    echo "    /etc/default/zramswap not found (zram-tools not installed?) - skipped."
fi

# The stock swappiness of 60 assumes swap is a slow disk. Backed by zram it
# is compressed RAM, so leaning on it beats evicting page cache that then
# has to be re-read off the SD card. page-cluster=0 turns off swap
# readahead, which only wastes work when each read is this cheap.
cat > /etc/sysctl.d/99-kcal-memory.conf << 'EOF'
# Written by setup_pi_kiosk.sh - tuned for a zram-backed kiosk.
vm.swappiness=100
vm.page-cluster=0
EOF
sysctl -q --load=/etc/sysctl.d/99-kcal-memory.conf
echo "    vm.swappiness=100, vm.page-cluster=0"
# ---------- Console blanking ----------

echo "==> Disabling console blanking"
CMDLINE=/boot/firmware/cmdline.txt
grep -q 'consoleblank=' "$CMDLINE" || sed -i 's/$/ consoleblank=0/' "$CMDLINE"

# ---------- Screen resolution (optional, interactive) ----------

echo "==> Detected display connectors:"
for f in /sys/class/drm/card*-HDMI-A-*; do
    [ -e "$f" ] || continue
    echo "  $f: $(cat "$f/status" 2>/dev/null)"
done

printf 'Force a specific screen resolution? (y/N): '
read -r force_res
if [ "$force_res" = "y" ] || [ "$force_res" = "Y" ]; then
    printf 'Connector (e.g. HDMI-A-1): '
    read -r connector
    printf 'Resolution (e.g. 1024x600@60): '
    read -r resolution
    if grep -q 'video=' "$CMDLINE"; then
        echo "    'video=' already set in $CMDLINE — edit it by hand if you want to change it."
    else
        sed -i "s/\$/ video=${connector}:${resolution}D/" "$CMDLINE"
        echo "    Set video=${connector}:${resolution}D"
    fi
fi

# ---------- Auto-deploy via cron (optional) ----------

printf 'Set up automatic git-pull-and-restart via cron too (in addition to the pull-on-start already configured)? (y/N): '
read -r setup_cron
if [ "$setup_cron" = "y" ] || [ "$setup_cron" = "Y" ]; then
    echo "==> Allowing $USERNAME to restart kcal.service and getty@tty1.service without a password"
    # update.sh restarts both: kcal.service picks up backend changes, and
    # getty@tty1.service refreshes the browser view itself (see "Manually
    # pulling an update and refreshing the kiosk view" in README.md).
    ensure_sudoers_line "/usr/bin/systemctl restart kcal.service"
    ensure_sudoers_line "/usr/bin/systemctl restart getty@tty1.service"

    chmod +x "$REPO_DIR/update.sh"
    CRON_LINE="*/5 * * * * $REPO_DIR/update.sh >> $USER_HOME/kcal-update.log 2>&1"
    ( sudo -u "$USERNAME" crontab -l 2>/dev/null | grep -vF "$REPO_DIR/update.sh"
      echo "$CRON_LINE" ) | sudo -u "$USERNAME" crontab -
    echo "    Cron entry added: $CRON_LINE"
fi

# ---------- Scheduled kiosk restart (optional) ----------

printf 'Set up a scheduled kiosk restart every 6 hours, to clear any Chromium GPU/memory buildup before it makes the display sluggish (y/N): '
read -r setup_restart_cron
if [ "$setup_restart_cron" = "y" ] || [ "$setup_restart_cron" = "Y" ]; then
    echo "==> Allowing $USERNAME to restart getty@tty1.service and reboot without a password"
    ensure_sudoers_line "/usr/bin/systemctl restart getty@tty1.service"
    ensure_sudoers_line "/usr/sbin/reboot"

    KIOSK_RESTART_SCRIPT="$USER_HOME/kiosk-restart.sh"
    echo "==> Writing watchdog restart script to $KIOSK_RESTART_SCRIPT"
    cat > "$KIOSK_RESTART_SCRIPT" << 'EOF'
#!/bin/sh
# Restarts the kiosk display and verifies Chromium actually comes back.
# A restart that leaves Chromium dead can otherwise go unnoticed until the
# whole Pi locks up hours later, requiring a physical power-cycle to
# recover. Retry once, then reboot as a last resort.

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

chromium_rss() {
    ps -C chromium -o rss= 2>/dev/null | awk '{s+=$1} END {print s+0}'
}

restart_and_check() {
    sudo systemctl restart getty@tty1.service
    sleep 20
    chromium_rss
}

log "Restarting kiosk"
rss=$(restart_and_check)
if [ "$rss" -gt 0 ]; then
    log "Chromium back up (rss=${rss}kB)"
    exit 0
fi

log "Chromium did not come back (rss=0kB), retrying"
rss=$(restart_and_check)
if [ "$rss" -gt 0 ]; then
    log "Chromium back up after retry (rss=${rss}kB)"
    exit 0
fi

log "Chromium still not up after retry, rebooting"
sudo reboot
EOF
    chmod +x "$KIOSK_RESTART_SCRIPT"
    chown "$USERNAME:$USERNAME" "$KIOSK_RESTART_SCRIPT"

    CRON_LINE="0 0,6,12,18 * * * $KIOSK_RESTART_SCRIPT >> $USER_HOME/kiosk-restart.log 2>&1"
    ( sudo -u "$USERNAME" crontab -l 2>/dev/null | grep -vF "restart getty@tty1.service" | grep -vF "kiosk-restart.sh"
      echo "$CRON_LINE" ) | sudo -u "$USERNAME" crontab -
    echo "    Cron entry added: $CRON_LINE"
fi

echo
echo "==> Done. Reboot to launch the kiosk."
printf 'Reboot now? (y/N): '
read -r do_reboot
if [ "$do_reboot" = "y" ] || [ "$do_reboot" = "Y" ]; then
    reboot
fi
