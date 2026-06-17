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
    conn.close()
    if not missing and not force:
        return False, "nothing outstanding"
    body = ("All habits logged \u2014 nice." if not missing
            else "Not logged yet:\n\u2022 " + "\n\u2022 ".join(missing))
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
