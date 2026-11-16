# 🎧 Audible Watcher

A small self-hosted app that watches Audible for **new releases from your
favorite authors and series** and **emails you** when something new shows up
(including pre-orders, as soon as they're listed).

It uses Audible's public catalog API — no Audible login required, no
credentials, no scraping of web pages. Everything runs on your own computer;
nothing is sent anywhere except the Audible catalog lookups and your own
email server.

Optionally, you can connect your Audible account (Account page) to see
OWNED badges on books you already have and PRE-ORDERED tags on pre-orders.
You sign in on amazon.com in your own browser; the app never sees your
password and stores only revocable device tokens (`audible_auth.json` —
treat it like a key and don't share it; revoke it anytime from Amazon's
"Manage Your Content and Devices").

> Audible Watcher is an independent open-source project. It is not
> affiliated with or endorsed by Audible or Amazon.

## Features

- **Dashboard** showing everything you watch, with a "new books" section,
  latest release dates, pre-orders and a check history log.
- **Watch authors or series** — search Audible from the app and add them.
  Watching a series also watches its author.
- **Daily automatic check** (time configurable) plus a "Check Audible now"
  button.
- **Email digest** via Gmail (or any SMTP server) when new titles appear.
- **No false alarms**: when you add a watch, everything currently on Audible
  is recorded as a baseline. Only titles that appear *after* that are flagged.
  Foreign-language and other-region editions are filtered out.

## Install

### Windows (installer)

1. Download **AudibleWatcher-Setup-x.y.z.exe** from the
   [latest release](https://github.com/Andrew911tt/audible-watcher/releases/latest).
2. Run it. No admin rights or Python needed. Leave **"Start Audible Watcher
   when I sign in"** ticked — the app has to be running for the daily check.
3. Audible Watcher opens in your browser at **http://localhost:5055**. It
   keeps running in the background with an icon in the system tray
   (right-click it to open the app or quit).

Windows SmartScreen may warn about an unrecognized app because the installer
isn't code-signed. Click **More info → Run anyway**, or build it yourself
from source (below).

Your data is kept in `%LOCALAPPDATA%\Audible Watcher` (tray menu → *Open
data folder*). Uninstalling leaves it in place.

### Docker (NAS / home server)

```
git clone https://github.com/Andrew911tt/audible-watcher.git
cd audible-watcher
cp docker-compose.example.yml docker-compose.yml   # set TZ to your time zone
docker compose up -d
```

Then open **http://your-server:5055**. Data is kept in `./data`.

### From source (Windows, macOS, Linux)

Requires Python 3.10+ ([python.org](https://www.python.org/downloads/) —
on Windows, check "Add Python to PATH" during install).

```
git clone https://github.com/Andrew911tt/audible-watcher.git
cd audible-watcher
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5055**. When run from source, data is kept next
to `app.py` (`watcher.db`, `audible_auth.json`).

- Windows: double-click `start.bat` (after the one-time `pip install`).
- macOS / Linux: `./start.sh`
- Prefer the tray app? `pip install -r requirements-desktop.txt` then
  `python desktop.py`.

## Set up email (Gmail)

Gmail no longer allows plain passwords for apps, so you need an
**app password** (a one-time, 16-character password just for this app):

1. Go to https://myaccount.google.com → **Security**.
2. Make sure **2-Step Verification** is turned on (app passwords require it).
3. Search for **App passwords** (or go to
   https://myaccount.google.com/apppasswords), create one named
   "Audible Watcher", and copy the 16-character code.
4. In Audible Watcher → **Settings**: enter your Gmail address, paste the app
   password, set "Send notifications to", tick **Send me an email**, save,
   then click **Send test email**.

Any other SMTP server works too — just change host/port/security.

## Run it automatically (source / server installs)

The Windows installer and Docker handle this for you. Otherwise:

**Windows** — Task Scheduler → Create Basic Task → trigger *When I log on* →
*Start a program*: `pythonw.exe`, arguments `app.py`, start in the
`audible-watcher` folder.

**Linux** — systemd (edit the user and paths in the file first):
```
sudo cp audible-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now audible-watcher
```

**macOS** — add `start.sh` as a Login Item, or use `launchd`.

## Settings via environment variables

| Variable | Default | Meaning |
|---|---|---|
| `AW_HOST` | `127.0.0.1` | Address to listen on. `0.0.0.0` makes it reachable from other devices on your network (there is no login, so only do this on a trusted network). |
| `AW_PORT` | `5055` | Port. |
| `AW_DATA_DIR` | see above | Folder for `watcher.db` and `audible_auth.json`. |

## Notes

- The daily check runs at the hour set in Settings (default 8 AM, local time
  of the machine running the app).
- Back up `watcher.db` to keep your watch list; delete it to start fresh.
- Your Gmail app password is stored in that local database in plain text —
  fine for a personal machine, but don't share the file. You can revoke the
  app password anytime from your Google account.
- Marketplace: defaults to Audible US (audible.com). Change it in Settings if
  you shop on a different regional store.
- A book that matches two watches (e.g. the author *and* the series) shows
  once on the dashboard, pre-orders and in emails, credited to the series.
  Each watch's own page still lists it. Marking it seen clears it everywhere.

## Development

```
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests use a mocked Audible API, so no network is needed.

**Building the Windows installer yourself:** install Python 3.12 and
[Inno Setup 6](https://jrsoftware.org/isinfo.php), then run
`powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Version 1.0.0`.
The installer lands in `packaging\Output\`.

**Releases:** pushing a tag like `v1.0.0` makes GitHub Actions run the tests,
build the installer and attach it to a new release.

Pull requests and forks are welcome.

## License

[MIT](LICENSE)
