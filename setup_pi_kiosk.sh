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

# ---------- Packages ----------

echo "==> Installing cage, Chromium, and a color emoji font"
apt update
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
ExecStart=$PYTHON_BIN $REPO_DIR/app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now kcal.service

# ---------- cage + Chromium autostart ----------

BASH_PROFILE="$USER_HOME/.bash_profile"
MARKER="# --- kcal kiosk autostart ---"
if [ -f "$BASH_PROFILE" ] && grep -qF "$MARKER" "$BASH_PROFILE"; then
    echo "==> Kiosk autostart block already present in $BASH_PROFILE, skipping"
else
    echo "==> Adding kiosk autostart to $BASH_PROFILE"
    cat >> "$BASH_PROFILE" << 'EOF'
# --- kcal kiosk autostart ---
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  until curl -s http://127.0.0.1:5000 > /dev/null; do sleep 1; done
  while true; do
    cage -- chromium-browser --kiosk --noerrdialogs --disable-infobars \
      --disable-session-crashed-bubble --check-for-update-interval=31536000 \
      --no-memcheck --password-store=basic --incognito http://127.0.0.1:5000
    sleep 2
  done
fi
EOF
    chown "$USERNAME:$USERNAME" "$BASH_PROFILE"
fi

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
    echo "==> Allowing $USERNAME to restart kcal.service without a password"
    echo "$USERNAME ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart kcal.service" \
        > "/etc/sudoers.d/kcal-restart"
    chmod 440 "/etc/sudoers.d/kcal-restart"

    chmod +x "$REPO_DIR/update.sh"
    CRON_LINE="*/5 * * * * $REPO_DIR/update.sh >> $USER_HOME/kcal-update.log 2>&1"
    ( sudo -u "$USERNAME" crontab -l 2>/dev/null | grep -vF "$REPO_DIR/update.sh"
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
