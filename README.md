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

Double-click `launch_kiosk.bat`. It starts the server in the background and
opens **http://127.0.0.1:5000** in Chrome's `--kiosk` mode (true full-screen,
no browser chrome). If Chrome isn't installed/on your PATH, edit the `.bat`
file to point at Chrome's full path, or switch to the Edge line included in
the file.

To auto-launch on boot: press **Win+R**, type `shell:startup`, and drop a
shortcut to `launch_kiosk.bat` into the folder that opens.

If you'd rather not use kiosk mode, just visit the site in any browser and
tap the fullscreen icon in the header.

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
- **Wrong day boundaries for all-day events**: the app uses the host
  computer's local timezone for all date math — make sure the kiosk
  machine's system clock/timezone is set correctly.
