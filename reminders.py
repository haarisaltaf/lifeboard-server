"""Optional daily reminder via ntfy (https://ntfy.sh or a self-hosted server).

Opt-in: configure server + topic + time in Settings. A lightweight asyncio loop
checks once a minute and, at the chosen local time, pushes a single notification
listing the habits you haven't logged today. No extra dependencies — plain urllib.
"""
import asyncio
import json
import urllib.request
from datetime import date, datetime

import db
import hard


def _post_ntfy(server, topic, title, message):
    server = (server or "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    data = message.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Title", title)
    req.add_header("Tags", "white_check_mark")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def unlogged_habits(conn, day):
    rows = conn.execute("SELECT id, title FROM widgets WHERE type='habit'").fetchall()
    missing = []
    for r in rows:
        log = conn.execute("SELECT value FROM logs WHERE widget_id=? AND day=?",
                           (r["id"], day)).fetchone()
        if not log or not log["value"]:
            missing.append(r["title"])
    return missing


def hard75_lines(conn, day):
    """One status line per active 75 Hard widget: the current day number and
    whatever's still outstanding today (or a 'complete' tick)."""
    lines = []
    rows = conn.execute("SELECT id, title, config FROM widgets WHERE type='hard75'").fetchall()
    for r in rows:
        cfg = json.loads(r["config"] or "{}")
        if not cfg.get("start_date"):
            continue
        recs = {}
        for x in conn.execute("SELECT day, tasks, photo FROM hard75 WHERE widget_id=?", (r["id"],)).fetchall():
            recs[x["day"]] = {"tasks": json.loads(x["tasks"] or "{}"), "photo": x["photo"]}
        st = hard.compute(cfg, recs, day)
        if st["won"]:
            continue
        title = r["title"] or "75 Hard"
        rec = recs.get(day, {})
        td = rec.get("tasks") or {}
        remaining = [t["label"] for t in st["tasks"] if not td.get(t["key"])]
        if st["require_photo"] and not rec.get("photo"):
            remaining.append("Progress photo")
        head = f"{title} — Day {st['day']}/{st['duration']}"
        if remaining:
            lines.append(head + ", still to do:\n  • " + "\n  • ".join(remaining))
        else:
            lines.append(head + " complete ✅")
    return lines


def send_reminder(force=False):
    """Returns (sent: bool, detail: str)."""
    conn = db.get_conn()
    enabled = db.get_setting(conn, "ntfy_enabled", "0") == "1"
    topic = db.get_setting(conn, "ntfy_topic", "")
    server = db.get_setting(conn, "ntfy_server", "https://ntfy.sh")
    if not (enabled and topic):
        conn.close()
        return False, "reminders disabled or no topic set"
    day = date.today().isoformat()
    missing = unlogged_habits(conn, day)
    hlines = hard75_lines(conn, day)
    conn.close()
    if not missing and not hlines and not force:
        return False, "nothing outstanding"
    parts = []
    if missing:
        parts.append("Not logged yet:\n\u2022 " + "\n\u2022 ".join(missing))
    if hlines:
        parts.append("\n\n".join(hlines))
    body = "\n\n".join(parts) if parts else "All habits logged \u2014 nice."
    try:
        _post_ntfy(server, topic, "lifeboard", body)
        return True, "sent"
    except Exception as e:
        return False, f"ntfy error: {e}"


async def reminder_loop():
    """Fires once per day at the configured HH:MM (local)."""
    while True:
        try:
            conn = db.get_conn()
            enabled = db.get_setting(conn, "ntfy_enabled", "0") == "1"
            rtime = db.get_setting(conn, "ntfy_time", "20:00")
            last = db.get_setting(conn, "ntfy_last_sent", "")
            conn.close()
            now = datetime.now()
            today = now.date().isoformat()
            if enabled and now.strftime("%H:%M") == rtime and last != today:
                send_reminder()
                conn = db.get_conn()
                db.set_setting(conn, "ntfy_last_sent", today)
                conn.commit(); conn.close()
        except Exception:
            pass
        await asyncio.sleep(60)
