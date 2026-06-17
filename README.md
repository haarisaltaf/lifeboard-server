# lifeboard

A self-hosted personal dashboard, habit tracker, and second brain. Built to run in a
Proxmox LXC and be reached from anywhere over Tailscale. One Python process, one SQLite
file, zero external services, works offline as a phone-installable PWA.

This is **v2**. On top of the v1 foundation (terminal-themed responsive dashboard,
GitHub-style habit heatmaps + streaks, a "today" check-in, user-defined goal tabs, a
daily pace engine, fuzzy-searchable notes, and journaling), v2 adds edit-mode widget
reordering, goal templates, a review/trends tab, config import/export, keyword
related-notes, journal prompt scheduling, and optional ntfy reminders. Manual data entry
only (health/wearable import and embeddings-based related-notes come in v3).

## What's inside

- **Dashboard** — at-a-glance stats, an aggregate 26-week activity heatmap, current/longest
  streaks, and a "needs attention" list of goals that have fallen behind pace.
- **Today** — every trackable widget across all tabs on one screen for fast logging.
- **Goal tabs** — make a tab per goal (Gym, Studying, Diet…) and add widgets:
  - `habit` — daily yes/no with streaks + its own heatmap
  - `counter` — count up per day against an optional daily target
  - `metric` — log a value (weight, kcal) and see the trend
  - `goal / pace` — track toward a target by a date; shows the daily rate you need and
    whether you're ahead/behind, or projects a finish date if there's no end date
  - `checklist`, `note` (pinned markdown), `timer` (logs minutes)
- **Second brain** — create or import `.md`/`.txt` notes, fuzzy full-text search (SQLite
  FTS5) across notes **and** journal entries with a filter toggle.
- **Journal** — morning/evening entries with a rotating prompt library (editable in
  settings); journaling entries are searchable alongside notes.
- **Backup/export** — full JSON, logs as CSV, or the raw `.db` file, from settings.

### New in v2

- **Edit mode** (per goal tab) — drag the ⠿ handle to reorder widgets, rename tabs/widgets
  inline, and open a ⚙ to change a widget's settings, including switching a goal between
  cumulative and metric (reach-a-value) pace modes.
- **Goal templates** — the "+ goal" picker offers Blank or prebuilt tabs (Daily Essentials,
  Cut, Run, Strength, Study sprint, Ship a project).
- **Review tab** — 7 / 30 / 365-day summary: habit completion rate, a daily-completion
  strip, and per-widget trend charts.
- **Config import/export** — share or restore your board structure (tabs, widgets, prompts)
  as a JSON file, separate from full data backups. Import can merge or replace.
- **Related notes** — keyword (TF-IDF) similarity surfaced inside each note.
- **Journal scheduling + history** — assign prompts to specific weekdays, and search past
  journal entries from the journal tab.
- **Reminders (ntfy)** — opt-in daily push listing habits you haven't logged. Configure the
  server, topic, and time in settings; works with ntfy.sh or a self-hosted server over your
  tailnet. No extra dependency.

## Run it locally first

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                      # serves on http://0.0.0.0:8800
```

Data is stored in `./data/lifeboard.db`. Override the location with `LIFEBOARD_DATA`
and the port with `LIFEBOARD_PORT`.

## Deploy in a Proxmox LXC

1. **Create the container** (Debian/Ubuntu template). An **unprivileged** LXC is fine.
   2 GB disk and 512 MB RAM are plenty.

2. **Install dependencies** inside the container:
   ```bash
   apt update && apt install -y python3 python3-venv python3-pip
   ```

3. **Copy the app** to `/opt/lifeboard` (scp, git, or a bind mount), then:
   ```bash
   cd /opt/lifeboard
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Run it as a service** so it starts on boot. Create
   `/etc/systemd/system/lifeboard.service`:
   ```ini
   [Unit]
   Description=lifeboard
   After=network-online.target
   Wants=network-online.target

   [Service]
   WorkingDirectory=/opt/lifeboard
   Environment=LIFEBOARD_PORT=8800
   Environment=LIFEBOARD_DATA=/opt/lifeboard/data
   ExecStart=/opt/lifeboard/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8800
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   ```
   Then:
   ```bash
   systemctl daemon-reload && systemctl enable --now lifeboard
   ```

## Reach it over Tailscale

There's no login (by design — it's meant to live on your private tailnet). Two options:

- **Simplest:** install Tailscale in the LXC and browse to
  `http://<tailscale-ip>:8800` from any device on your tailnet.
  (Unprivileged LXC needs the `tun` device — on the Proxmox host, add to the container
  config `/etc/pve/lxc/<id>.conf`:
  `lxc.cgroup2.devices.allow: c 10:200 rwm` and
  `lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file`.)

- **Nicer URL + HTTPS:** put it behind Tailscale Serve so it's a clean hostname with a
  cert:
  ```bash
  tailscale serve --bg 8800
  ```
  Then open `https://<machine-name>.<tailnet>.ts.net` and "Add to Home Screen" on your
  phone to install the PWA.

> Because there's no auth yet, keep this off any public/port-forwarded interface. Bind it
> to the tailnet only if you want to be strict (e.g. `--host <tailscale-ip>`).

## Back up your data

Everything is one file: `data/lifeboard.db`. Back it up however you like (a cron `cp`,
Proxmox container backup, or the **settings → backup & export** buttons). The JSON export
is a complete, human-readable dump.

## Project layout

```
app.py            FastAPI app + all API routes
db.py             SQLite schema, FTS5 search index, migrations, seed data
pace.py           pace/streak engine (pure functions)
extras.py         v2: goal templates, review stats, TF-IDF related-notes
reminders.py      v2: ntfy daily reminder background task
static/           index.html, style.css, app.js, sw.js, manifest.json, icon.svg
test_app.py       v1 integration tests   (python3 test_app.py)
test_v2.py        v2 integration tests   (python3 test_v2.py)
requirements.txt
run.sh
```

## Roadmap (agreed)

- **v2 (shipped)** — edit-mode widget reorder + inline rename, goal templates, review/trends
  tab, metric pace mode in the UI, config JSON import/export, keyword related-notes, journal
  prompt scheduling + searchable history, ntfy reminders.
- **v3** — semantic related-notes (embeddings), health/sleep import (CSV / Apple Health /
  Google Fit), sub-goals, customizable accent/tint color in settings (phosphor-green by
  default), deeper theming, optional auth.
