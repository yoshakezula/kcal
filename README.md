# Kcal — Google Calendar Kiosk

A touch-first, full-screen wrapper around your Google Calendar and Google
Tasks. Runs as a small local web app; open it in a browser in kiosk mode on
a dedicated display. It never creates, edits, or deletes calendar events —
the only write it makes is checking/unchecking tasks off in Google Tasks.

- **Month, 2-week, and 3-week views** — a grid, tap a day to see its full
  event list.
- **Week view** — a simple list of each day's events with time + duration
  (not a proportional hour-by-hour grid).
- **7-day weather forecast** — a bar strip at the top of every view showing
  each day's high/low temperature and high/low humidity.
- **Tasks** — a blue **Tasks** button next to the view switcher opens a
  popup listing your Google Tasks, with a checkbox to mark each one done.
  Switch between task lists from a dropdown at the top of the popup, and
  pick a default list to open in Settings.
- **Touch** — swipe left/right to move between periods, tap a day/event for
  details.

## 1. Get Google Calendar API credentials

Google Calendar doesn't hand out a simple API key for reading your private
calendar data — you need an **OAuth client ID**, which lets you (and only
you) grant this app permission to read your calendars. This is a one-time
setup in Google's own developer console; nothing is shared with anyone else.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   sign in with the Google account whose calendar you want to display.
2. **Create a project**: top-left project dropdown → **New Project** → give
   it any name (e.g. "Kcal Kiosk") → **Create**. Make sure it's selected in
   the dropdown afterward.
3. **Enable the APIs**: go to
   [APIs & Services → Library](https://console.cloud.google.com/apis/library),
   search for **Google Calendar API**, open it, click **Enable** — then do
   the same for **Google Tasks API**.
4. **Configure the OAuth consent screen**: go to
   [Google Auth Platform → Overview](https://console.cloud.google.com/auth/overview)
   (Google has renamed/reorganized this area recently — it may also show up
   in the sidebar as **APIs & Services → OAuth consent screen**. Look for
   tabs named **Overview / Branding / Audience / Clients / Data Access**).
   - User type: **External** (fine for personal use — no Workspace account
     needed).
   - Fill in the required fields (app name, your email as support/contact
     email). Skip optional fields.
   - You don't need to add scopes manually — the app requests the
     read-only calendar scope and the Tasks scope itself the first time you
     authorize it.
   - You do **not** need to submit this for Google's verification — that's
     only required for public-facing apps; this stays in "Testing" mode
     indefinitely, which is fine for personal use.
5. **(Only if needed) Add a test user**: while in "Testing" mode, go to the
   **[Audience](https://console.cloud.google.com/auth/audience)** tab and
   add your Google account's email under **Test users**.
   - If you're signed in with the *same account that owns this Cloud
     project*, try skipping this and going straight to step 8
     (`python authorize.py`) — project owners often don't need to be
     listed at all.
   - If adding yourself here gives *"not eligible... to be designated as a
     test user"*, that's expected for the project owner's own account —
     it's not an error to fix, just move on to authorizing directly.
6. **Create the OAuth client ID**: go to
   [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients)
   (or **APIs & Services → Credentials**) → **Create Client**.
   - Application type: **Desktop app**.
   - Name it anything (e.g. "Kcal Desktop Client").
   - Click **Create**, then **Download JSON** on the client you just created.
7. Rename the downloaded file to `client_secret.json` and place it in this
   project's folder (same folder as `app.py`).

`client_secret.json` and the `token.json` it produces are both already
listed in `.gitignore` — keep them out of any repo you push.

## 2. Install dependencies

From this folder:

```
pip install -r requirements.txt
```

(Using a virtual environment first — `python -m venv venv` then activating
it — is recommended but not required.)

## 3. Authorize (one-time)

```
python authorize.py
```

This opens your browser to Google's sign-in/consent screen. Sign in with the
same account from step 1, and approve read-only calendar access plus Tasks
access. Credentials are saved to `token.json` and refresh themselves
automatically after this — you shouldn't need to run this again unless you
delete `token.json` or revoke access.

If you already had a `token.json` from before Tasks support was added,
delete it and re-run `python authorize.py` so the new consent screen grants
the Tasks scope too.

If you see **"Access blocked: has not completed the Google verification
process"** the first time, just run `python authorize.py` again — this
commonly clears up on retry for the project's own owner account with no
other change needed. If it persists, double check the **Audience** tab
(step 5 above) and that the Calendar API is enabled (step 3 above) on the
same project your `client_secret.json` came from.

## 4. Choose which calendars to show

```
python app.py
```

Then visit **http://127.0.0.1:5000/settings** in a browser and check off
whichever calendars you want merged into the kiosk view (your primary
calendar is selected by default). Save, then go back to the main view.

On the same Settings page, pick a **default task list** under Tasks — that's
the list the Tasks popup opens to on the kiosk view (you can still switch
lists from the popup's dropdown).

Also on the same page, set a **zip code** under Weather Location to control
the 7-day forecast strip at the top of the kiosk view (defaults to `90008`).
This uses [Open-Meteo](https://open-meteo.com/), which needs no API key.

## 5. Run it full-screen on the kiosk computer

### Windows

Double-click `launch_kiosk.bat`. It starts the server in the background and
opens **http://127.0.0.1:5000** in Chrome's `--kiosk` mode (true full-screen,
no browser chrome). If Chrome isn't installed/on your PATH, edit the `.bat`
file to point at Chrome's full path, or switch to the Edge line included in
the file.

To auto-launch on boot: press **Win+R**, type `shell:startup`, and drop a
shortcut to `launch_kiosk.bat` into the folder that opens.

If you'd rather not use kiosk mode, just visit the site in any browser and
tap the fullscreen icon in the header.

### Raspberry Pi (Linux)

This assumes the **Raspberry Pi OS with desktop** image (not Lite) and an
HDMI display connected to the Pi.

If the Pi is headless over SSH, `python authorize.py` (step 3) can't open a
real graphical browser there, and Google's sign-in page will reject the
text-mode browser Python falls back to ("doesn't support JavaScript"). The
easiest fix: run `authorize.py` once on a different machine that has a
normal browser (same `client_secret.json`), then copy the `client_secret.json`
and the `token.json` it produces into this project's folder on the Pi.
`token.json` refreshes itself from then on, so this is a one-time transfer.

1. **Enable desktop autologin** so the Pi boots straight into a desktop
   session without needing a keyboard to log in:
   ```
   sudo raspi-config nonint do_boot_behaviour B4
   ```
2. **Run the Flask app as a systemd service**, so it starts on boot and
   restarts automatically if it ever crashes. Create
   `/etc/systemd/system/kcal.service`:
   ```ini
   [Unit]
   Description=Kcal calendar kiosk
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=username
   WorkingDirectory=/home/username/src/kcal
   ExecStartPre=-/usr/bin/git -C /home/username/src/kcal pull origin main
   ExecStart=/usr/bin/python3 /home/username/src/kcal/app.py
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```
   The `ExecStartPre` line pulls the latest commit every time the service
   starts — on boot, and on any manual/cron restart — so you don't have to
   pull by hand. The leading `-` tells systemd to ignore a failed pull
   (e.g. no network yet, or a merge conflict) rather than treat it as a
   failure that blocks the app from starting at all; worst case it just
   starts with whatever code was already on disk.

   Adjust `User`/`WorkingDirectory`/`ExecStart` to match wherever you put the
   project and whichever Python/venv you're using — **if you installed
   dependencies into a virtualenv** (e.g. via `python -m venv myenv` from
   step 2 of setup), `ExecStart` needs to point at that venv's interpreter
   (e.g. `/home/username/myenv/bin/python`), not `/usr/bin/python3` —
   otherwise the service runs with the system Python, which won't have any
   of the packages from `requirements.txt` installed, and will crash-loop
   with `ModuleNotFoundError` until systemd gives up and marks it failed.
   Then:
   ```
   sudo systemctl daemon-reload
   sudo systemctl enable --now kcal.service
   ```
3. **Launch Chromium in kiosk mode** on desktop login. Recent Raspberry Pi OS
   (Bookworm) uses the **labwc** Wayland compositor by default — create
   `~/.config/labwc/autostart`:
   ```sh
   #!/bin/sh
   until curl -s http://127.0.0.1:5000 > /dev/null; do sleep 1; done
   chromium-browser --kiosk --noerrdialogs --disable-infobars \
     --disable-session-crashed-bubble --check-for-update-interval=31536000 \
     --incognito http://127.0.0.1:5000 &
   ```
   ```
   chmod +x ~/.config/labwc/autostart
   ```
   The `curl` wait loop keeps Chromium from launching before the systemd
   service is ready. (If your image still defaults to the older Wayfire
   compositor instead of labwc, add the same `chromium-browser ...` line
   under an `[autostart]` section in `~/.config/wayfire.ini` instead — check
   which one is active with `dpkg -l | grep -E '^ii.*(labwc|wayfire)'`.)
4. **Disable screen blanking** so the display doesn't sleep:
   ```
   sudo raspi-config nonint do_blanking 1
   ```
5. Reboot. It should come up directly into the kiosk view.

### Raspberry Pi OS Lite (no desktop) — cage kiosk

For lower-power boards like the Pi Zero 2 W, skip the desktop image entirely
and use **cage** — a minimal Wayland compositor that just runs one
fullscreen app, with no desktop/panel/window-manager overhead — instead of
labwc/Wayfire.

1. **Flash Raspberry Pi OS Lite** (64-bit) instead of the desktop image.
2. **Enable console autologin** so it boots straight to a logged-in shell on
   tty1:
   ```
   sudo raspi-config nonint do_boot_behaviour B2
   ```
3. **Install cage and Chromium**:
   ```
   sudo apt update
   sudo apt install --no-install-recommends -y cage chromium-browser
   ```
   (`--no-install-recommends` skips each package's optional extras — smaller
   install, faster on a Zero 2 W's SD card.)
4. **Run the Flask app as a systemd service** — same `kcal.service` as
   above.
5. **Launch cage + Chromium on login** by appending this to
   `~/.bash_profile` (the `>>` redirect creates the file if it doesn't
   already exist — run this as the autologin user, not root):
   ```sh
   cat << 'EOF' >> ~/.bash_profile
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
   ```
   `.bash_profile` is only read for login shells, which is exactly what the
   tty1 autologin creates — but note it means `~/.bashrc` won't get sourced
   the way it would in a normal interactive shell (doesn't matter for this
   kiosk use case).

   cage grabs the display directly via DRM/KMS as soon as this runs on the
   tty1 autologin shell, so there's no desktop session to load first. The
   `while true` loop relaunches Chromium/cage automatically if it ever
   crashes.
6. **Disable console blanking** (framebuffer blanking still applies before
   cage takes the display) by appending `consoleblank=0` to the single line
   in `/boot/firmware/cmdline.txt`:
   ```
   grep -q consoleblank= /boot/firmware/cmdline.txt || sudo sed -i 's/$/ consoleblank=0/' /boot/firmware/cmdline.txt
   ```
7. **Force the screen resolution**, if the display comes up at the wrong
   one (e.g. a small touchscreen defaulting to a much higher resolution
   than its native size, making everything look tiny/zoomed out). On
   Raspberry Pi OS Bookworm this is set via a kernel command-line
   parameter in `cmdline.txt`, not the old `config.txt` HDMI settings
   (those have no effect under full KMS).

   Find your connector name and its supported modes:
   ```
   for f in /sys/class/drm/card*-HDMI-A-*; do echo "$f:"; cat "$f/status"; cat "$f/modes"; echo; done
   ```
   Whichever one reports `connected` is your active port (usually
   `HDMI-A-1`). Then force your panel's native resolution (adjust the
   connector name and resolution to match):
   ```
   grep -q 'video=' /boot/firmware/cmdline.txt || sudo sed -i 's/$/ video=HDMI-A-1:1024x600@60D/' /boot/firmware/cmdline.txt
   ```
8. Reboot. It should come up directly into the kiosk view with no desktop
   loaded at all.

The app's own stylesheet (`static/css/style.css`) sets `cursor: none` on
every element, so no mouse pointer is drawn on a touchscreen setup — cage
itself has no reliable built-in way to force-hide it (its maintainers have
said as much upstream), so this is handled at the page level instead and
needs no extra configuration on the Pi.

### Auto-deploy changes pushed from your dev machine

`update.sh` (in this repo) checks GitHub for new commits and, if there are
any, pulls and restarts `kcal.service`. Run it on a schedule with cron so the
Pi picks up whatever you push without any manual step:

1. **Let the service user restart it without a password prompt** (cron runs
   non-interactively, so `sudo systemctl restart` needs to not ask for one).
   Run `sudo visudo -f /etc/sudoers.d/kcal-restart` and add:
   ```
   username ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart kcal.service
   ```
2. **Make the script executable**:
   ```
   chmod +x /home/username/src/kcal/update.sh
   ```
3. **Add a cron entry** — `crontab -e`, then add (checks every 5 minutes;
   adjust to taste):
   ```
   */5 * * * * /home/username/src/kcal/update.sh >> /home/username/kcal-update.log 2>&1
   ```

Push to `main` from your dev machine as usual — within one interval, the Pi
pulls the change and restarts the service.

### Manually pulling an update and refreshing the kiosk view

If you don't want to wait for the cron interval, or haven't set up
`update.sh` at all, pull and apply a change by hand:

1. **SSH into the Pi** and pull the latest commit:
   ```
   cd /home/username/src/kcal
   git pull origin main
   ```
2. **Restart the Flask service** so it picks up any Python/backend changes:
   ```
   sudo systemctl restart kcal.service
   ```
3. **Refresh the browser view.** Restarting `kcal.service` alone doesn't
   reload the page already open in Chromium — for that, restart the tty1
   session, which relaunches cage + Chromium and loads the page fresh
   (picking up any HTML/CSS/JS changes too, not just backend ones):
   ```
   sudo systemctl restart getty@tty1.service
   ```

## 6. Access it from other devices on your network

By default `app.py` binds to `0.0.0.0`, so it's reachable from other devices
on the same Wi-Fi/LAN (phones, tablets, laptops) — not just the host
machine. Note the app itself has no login, so anyone on your network can
open it and change your calendar selection via `/settings`; fine for a
trusted home network, worth knowing on a shared/guest one.

1. **Find the host machine's LAN IP**:
   - Windows: `ipconfig` → "IPv4 Address" under the active adapter.
   - Linux: `ip addr` or `hostname -I`.
2. **Allow the port through the firewall**:
   - Windows (elevated PowerShell/cmd):
     `netsh advfirewall firewall add rule name="Kcal" dir=in action=allow protocol=TCP localport=5000`
   - Linux (ufw): `sudo ufw allow 5000/tcp`
3. **From your phone/tablet**, browse to `http://<that-IP>:5000` while on
   the same network.

### Keep the IP from changing

Home routers hand out IPs via DHCP, which can reassign the host machine a
different address later (e.g. after a reboot). To keep it stable, reserve
it in your router:

1. Find the host machine's MAC address (Windows: `ipconfig /all`, look for
   "Physical Address" under the active adapter).
2. Log into your router admin page (usually `http://<gateway-IP>`, e.g.
   `http://192.168.1.1`).
3. Find the **DHCP reservation / address reservation / static lease**
   section and add an entry mapping that MAC address to a fixed IP.
4. On the host machine, renew its lease so it picks up the reserved IP:
   - Windows: `ipconfig /release "Wi-Fi"` then `ipconfig /renew "Wi-Fi"`
     (swap `"Wi-Fi"` for your adapter's name if different).
   - Linux: restart the network service, or `sudo dhclient -r && sudo dhclient`.

Alternatively, most phones/tablets and modern OSes support mDNS, so
`http://<hostname>.local:5000` may work without any router configuration —
worth trying first if you'd rather skip the reservation step.

## Troubleshooting

- **"Access blocked: has not completed the Google verification process"**
  during `python authorize.py`: see step 3's note above — retry first, then
  check the Audience/test-users tab.
- **"not eligible... to be designated as a test user"** when adding
  yourself in the Audience tab: expected if you're the project owner —
  skip it and run `python authorize.py` directly.
- **"Calendar disconnected" screen** in the app: your token expired or was
  revoked. Re-run `python authorize.py`.
- **A calendar you checked in Settings shows no events**: make sure it's
  actually shared with/owned by the account you authorized in step 3.
- **Internal Server Error / `PermissionError` on `token.json`** (common on
  Linux/Raspberry Pi after copying `token.json` over with `scp` as a
  different user, e.g. root): the app couldn't write the refreshed token
  back to disk because the file isn't owned by whichever user is running
  `app.py`. Fix ownership to match the current user and re-run:
  ```
  sudo chown "$(whoami):$(whoami)" token.json config.json
  chmod 600 token.json
  sudo systemctl restart kcal.service   # if running as a systemd service
  ```
- **Wrong day boundaries for all-day events**: the app uses the host
  computer's local timezone for all date math — make sure the kiosk
  machine's system clock/timezone is set correctly.
