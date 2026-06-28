# lifeboard

A self-hosted personal dashboard, habit tracker, and second brain. Built to run in a
Proxmox LXC and be reached from anywhere over Tailscale. One Python process, one SQLite
file, zero external services, works offline as a phone-installable PWA.

This is **v2.6**. The core is a habit/goal tracker with a forgiving-streak philosophy
(today isn't a "miss" until it's over, and yesterday still counts) plus a lightweight
second brain. On top of that it has grown a **75 Hard** challenge, a **kaizen "light mode"**
daily ritual, and a **to-do list** tab that talks to a companion voicetodo-server for text
and voice capture. Data entry is manual, except the todos tab, which syncs with voicetodo
when configured. The remaining v3 items (embeddings-based related-notes, health/sleep
import, optional auth) are still to come.

## What's inside

- **Dashboard** — at-a-glance stats, an aggregate 26-week activity heatmap, current/longest
  streaks, and a "needs attention" list of goals that have fallen behind pace.
- **Today** — every trackable widget across all tabs on one screen for fast logging.
- **Kaizen** — a "light mode" daily ritual: one **highlight**, a few 2-minute
  **micro-commitments**, and a **brain dump**, with a weekly reflection (added in v2.59).
- **Goal tabs** — make a tab per goal (Gym, Studying, Diet…) and add widgets:
  - `habit` — daily yes/no with streaks + its own heatmap
  - `counter` — count up per day against an optional daily target
  - `metric` — log a value (weight, kcal) and see the trend
  - `goal / pace` — track toward a target by a date; shows the daily rate you need and
    whether you're ahead/behind, or projects a finish date if there's no end date
  - `checklist`, `note` (pinned markdown), `timer` (logs minutes)
  - `75 Hard` — an all-or-nothing daily challenge with auto-reset (added in v2.51)
- **Todos** — a to-do list backed by a companion voicetodo-server; add by text or voice,
  set reminders, and edit them after (added in v2.52).
- **Second brain** — create or import `.md`/`.txt` notes, fuzzy full-text search (SQLite
  FTS5) across notes **and** journal entries with a filter toggle.
- **Journal** — morning/evening entries with a rotating prompt library (editable in
  settings); journaling entries are searchable alongside notes.
- **Settings** — journal prompts, ntfy reminders, accent color, **show/hide built-in tabs**,
  and config/data backup & export.

## Feature history

### v2 — structure & review

- **Edit mode** (per goal tab) — drag the ⠿ handle to reorder widgets, rename tabs/widgets
  inline, and open a ⚙ to change a widget's settings, including switching a goal between
  cumulative and metric (reach-a-value) pace modes.
- **Goal templates** — the "+ goal" picker offers Blank or prebuilt tabs (Daily Essentials,
  Cut, Run, Strength, Study sprint, Ship a project, 75 Hard).
- **Review tab** — 7 / 30 / 365-day summary: habit completion rate, a daily-completion
  strip, and per-widget trend charts.
- **Config import/export** — share or restore your board structure (tabs, widgets, prompts)
  as a JSON file, separate from full data backups. Import can merge or replace.
- **Related notes** — keyword (TF-IDF) similarity surfaced inside each note.
- **Journal scheduling + history** — assign prompts to specific weekdays, and search past
  journal entries from the journal tab.
- **Reminders (ntfy)** — opt-in daily push listing habits you haven't logged (and any
  outstanding 75 Hard tasks). Configure server, topic, and time in settings; works with
  ntfy.sh or a self-hosted server over your tailnet. No extra dependency.

### v2.5 — theming & milestones

- **Custom accent color** — pick from a preset palette or any custom color in settings →
  appearance. The whole UI (heatmaps, progress bars, pills, highlights) recolors to match,
  and the choice syncs across your devices. The ↺ swatch resets to the default phosphor
  green. The "attention/behind" amber stays independent so warnings remain legible.
- **Sub-goals (milestones)** — give a goal/pace widget named checkpoints (e.g. "halfway",
  "150 club"). They appear as ticks on the progress bar and a list that marks reached ones,
  and the next unreached milestone gets an ETA projected from your current pace.

### v2.51 — 75 Hard

- A one-click challenge template with a dedicated daily-resetting widget. Each day you tick
  off the required tasks (diet, two workouts, a gallon of water, 10 pages) and upload a
  progress photo; a day "passes" only when everything's done, shown on a day 1 → 75 heatmap.
  **Miss any day and the run auto-resets to day 1** — completion pops a congrats, a broken
  streak pops a reset notice. Retroactive logging heals the streak, the tasks/duration/photo
  requirement are configurable via the ⚙, and the ntfy reminder lists outstanding tasks.

### v2.52 — to-do list (voicetodo)

- A `todos` tab that integrates with a companion
  [voicetodo-server](https://github.com/haarisaltaf/voicetodo-server) running alongside
  Lifeboard. Set its URL (and optional API key) in the tab, then:
  - add to-dos by **text**, with a priority and a natural-language reminder
    (`tomorrow 9am`, `in 2h`, `fri 5pm`, `2026-05-01 17:00`);
  - add by **voice** — record from the mic, or upload an audio file, and voicetodo
    transcribes and decomposes it into individual todos;
  - **edit** text / priority / reminder, complete / reopen / delete, and browse the
    voice-note history with the todos each note produced.

  Lifeboard proxies every call through its own backend, so the voicetodo API key stays
  server-side and there are no cross-origin (CORS) problems talking to a service on another
  port. (Mic capture needs HTTPS or localhost; over plain-HTTP tailnet, use file upload.)

### v2.59 — kaizen "light mode"

- A `kaizen` tab framing the day as continuous tiny improvement (改善):
  - **today's highlight** — name the one win, mark it landed;
  - **micro-commitments** — tiny 2-minute "just show up" habits, each with a forgiving
    streak (a single slip never resets it);
  - **brain dump** — free-text "empty the buffer", saved per day;
  - **this week** — highlights landed (x/7), micro-commitment consistency, a 7-day strip,
    and a friendly, non-judgmental nudge.
  - A **simple / full** toggle strips it down to just the three rituals on survival days.

### v2.6 — show/hide tabs

- Settings → **tabs** has a toggle for each built-in tab (dashboard, today, kaizen, todos,
  review, notes, journal). Hide the ones you don't use; default is all visible. The choice
  is stored server-side, so it syncs across devices. Hidden tabs stay reachable by URL, and
  settings is always available, so it's never a dead end. Goal tabs are unaffected.

## Run it locally first

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                      # serves on http://0.0.0.0:8800
```

Data is stored in `./data/lifeboard.db`. Override the location with `LIFEBOARD_DATA`
and the port with `LIFEBOARD_PORT`. The schema is created (and forward-migrated) on
startup, so dropping in a database from an older version Just Works — no migration step.

## Deploy in a Proxmox LXC

1. **Create the container** (Debian/Ubuntu template). An **unprivileged** LXC is fine.
   2 GB disk and 512 MB RAM are plenty.

2. **Install dependencies** inside the container:
   ```bash
   apt update && apt install -y python3 python3-venv python3-pip git
   ```

3. **Clone the app** to `/opt/lifeboard`, then create the venv:
   ```bash
   git clone https://github.com/haarisaltaf/lifeboard-server /opt/lifeboard
   cd /opt/lifeboard
   python3 -m venv venv && . venv/bin/activate
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
   ExecStart=/opt/lifeboard/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8800
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   ```
   Then:
   ```bash
   systemctl daemon-reload && systemctl enable --now lifeboard
   curl -s http://localhost:8800/api/health    # -> {"ok":true,...}
   ```

## Updating

Pull the latest code and redeploy without touching your data or venv. The pattern: keep a
git mirror, sync everything **except** `data/`, `venv/`, `.git/`, and `__pycache__/` into the
live install, reinstall deps, and restart. A self-contained `lifeboard-update` script that
does this (with a DB backup, change-detection, and a health check) is the recommended way —
run it manually after a push, or on a cron timer to track the latest automatically.

The minimal manual version:

```bash
SRC=/opt/lifeboard-src ; DST=/opt/lifeboard
[ -d "$SRC/.git" ] && git -C "$SRC" pull || git clone https://github.com/haarisaltaf/lifeboard-server "$SRC"
cp -a "$DST/data/lifeboard.db" "$DST/data/lifeboard.backup-$(date +%F-%H%M).db"
systemctl stop lifeboard
rm -rf "$DST/static" "$DST/__pycache__"
for f in "$SRC"/*; do case "$(basename "$f")" in data|venv|.venv|.git|__pycache__) ;; *) cp -a "$f" "$DST"/;; esac; done
"$DST/venv/bin/pip" install -q -r "$DST/requirements.txt"
systemctl start lifeboard && sleep 2 && curl -s http://localhost:8800/api/health; echo
```

New DB tables (e.g. the kaizen tables) are created automatically on the next start, and
settings like hidden tabs are plain rows — so updates never need a manual migration.

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

## Companion: voicetodo

The **Todos** tab is optional and only lights up if you point it at a
[voicetodo-server](https://github.com/haarisaltaf/voicetodo-server) — a small daemon that
transcribes voice notes (faster-whisper) and decomposes them into todos. Run it on the same
box and set its URL (`http://localhost:8765` by default) in the Todos tab. The URL is
resolved server-side by Lifeboard, so use `localhost`, not the Tailscale IP.

## Back up your data

Everything is one file: `data/lifeboard.db` (plus `data/uploads/` for 75 Hard progress
photos). Back it up however you like (a cron `cp`, Proxmox container backup, or the
**settings → backup & export** buttons). The JSON export is a complete, human-readable dump.

## Project layout

```
app.py            FastAPI app + all API routes
db.py             SQLite schema, FTS5 search index, migrations, seed data
pace.py           pace/streak engine (pure functions)
extras.py         goal templates, review stats, TF-IDF related-notes
reminders.py      ntfy daily reminder background task (habits + 75 Hard status)
hard.py           75 Hard challenge logic (day counting, auto-reset, grid)
kaizen.py         kaizen daily-ritual state, weekly summary + nudge
vtd.py            voicetodo-server client (proxied todos + audio upload)
static/           index.html, style.css, app.js, sw.js, manifest.json, icon.svg
test_app.py       v1 integration tests   (python3 test_app.py)
test_v2.py        v2 integration tests   (python3 test_v2.py)
test_v25.py       v2.5 integration tests (python3 test_v25.py)
requirements.txt
run.sh
```

## Roadmap

- **v2 (shipped)** — edit-mode widget reorder + inline rename, goal templates, review/trends
  tab, metric pace mode, config JSON import/export, keyword related-notes, journal prompt
  scheduling + searchable history, ntfy reminders.
- **v2.5 (shipped)** — customizable accent color + accent-driven theming, sub-goals
  (milestones with per-milestone ETAs).
- **v2.51 (shipped)** — 75 Hard challenge template with a daily-resetting widget
  (all-or-nothing, auto-reset to day 1, progress photos, day 1 → 75 heatmap).
- **v2.52 (shipped)** — to-do list tab integrating with a companion voicetodo-server
  (text + voice entry, reminders, editing), proxied server-side.
- **v2.59 (shipped)** — kaizen "light mode": daily highlight, micro-commitments, brain dump,
  weekly reflection, simple/full toggle.
- **v2.6 (shipped)** — show/hide built-in tabs from settings (synced across devices).
- **v3** — semantic related-notes (embeddings), health/sleep import (CSV / Apple Health /
  Google Fit), more theme presets, optional auth.
```
