"""lifeboard — a self-hosted personal dashboard + second brain.

Runs as a single FastAPI process backed by one SQLite file.
No auth (designed to sit behind Tailscale on a private tailnet).
"""
import json
import io
import csv
import re
import glob
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import (
    JSONResponse, HTMLResponse, FileResponse, StreamingResponse, PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

import db
import pace
import extras
import reminders
import hard
import vtd
import kaizen
import gym
import asyncio

app = FastAPI(title="lifeboard")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.on_event("startup")
def _startup():
    db.init_db()
    c = db.get_conn()
    gym.seed(c)
    c.commit()
    c.close()


@app.on_event("startup")
async def _start_reminder_loop():
    asyncio.create_task(reminders.reminder_loop())


def now_iso():
    return datetime.utcnow().isoformat()


def today_str():
    return date.today().isoformat()


# ----------------------------------------------------------------------------
# Tabs
# ----------------------------------------------------------------------------
class TabIn(BaseModel):
    name: str


@app.get("/api/tabs")
def list_tabs():
    c = db.get_conn()
    rows = c.execute("SELECT * FROM tabs ORDER BY position, id").fetchall()
    c.close()
    return [dict(r) for r in rows]


@app.post("/api/tabs")
def create_tab(t: TabIn):
    c = db.get_conn()
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM tabs").fetchone()[0]
    cur = c.execute("INSERT INTO tabs(name, position, created_at) VALUES (?,?,?)",
                    (t.name.strip() or "Untitled", pos, now_iso()))
    c.commit()
    row = c.execute("SELECT * FROM tabs WHERE id=?", (cur.lastrowid,)).fetchone()
    c.close()
    return dict(row)


@app.patch("/api/tabs/{tab_id}")
def rename_tab(tab_id: int, t: TabIn):
    c = db.get_conn()
    c.execute("UPDATE tabs SET name=? WHERE id=?", (t.name.strip(), tab_id))
    c.commit()
    c.close()
    return {"ok": True}


@app.delete("/api/tabs/{tab_id}")
def delete_tab(tab_id: int):
    c = db.get_conn()
    c.execute("DELETE FROM tabs WHERE id=?", (tab_id,))
    c.commit()
    c.close()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------
class WidgetIn(BaseModel):
    tab_id: int
    type: str
    title: str
    config: dict = {}


class WidgetPatch(BaseModel):
    title: Optional[str] = None
    config: Optional[dict] = None
    position: Optional[int] = None


VALID_TYPES = {"habit", "counter", "number", "progress", "todo", "note", "timer", "hard75"}


def _hard75_records(c, wid):
    """Per-day records for a 75 Hard widget, keyed by day. Includes the raw
    photo path (for completeness math) and a client-facing photo URL."""
    recs = {}
    for r in c.execute("SELECT day, tasks, photo FROM hard75 WHERE widget_id=? ORDER BY day", (wid,)).fetchall():
        recs[r["day"]] = {"tasks": json.loads(r["tasks"] or "{}"), "photo": r["photo"]}
    return recs


def _widget_dict(c, row):
    w = dict(row)
    w["config"] = json.loads(w.get("config") or "{}")
    wid, wtype = w["id"], w["type"]
    if wtype in ("habit", "counter", "number", "progress", "timer"):
        logs = c.execute("SELECT day, value FROM logs WHERE widget_id=? ORDER BY day", (wid,)).fetchall()
        w["logs"] = [dict(l) for l in logs]
        if wtype == "habit":
            done = [l["day"] for l in logs if l["value"]]
            cur, longest = pace.streaks(done)
            w["streak"] = {"current": cur, "longest": longest, "total": len(done)}
        if wtype == "progress":
            w["pace"] = pace.pace(w["config"], w["logs"])
    if wtype == "todo":
        items = c.execute("SELECT * FROM todos WHERE widget_id=? ORDER BY position, id", (wid,)).fetchall()
        w["todos"] = [dict(i) for i in items]
    if wtype == "hard75":
        recs = _hard75_records(c, wid)
        w["records"] = {
            d: {"tasks": v["tasks"],
                "photo_url": f"/api/widgets/{wid}/hard75/photo/{d}" if v["photo"] else None}
            for d, v in recs.items()
        }
        w["hard"] = hard.compute(w["config"], recs, today_str())
    return w


@app.get("/api/tabs/{tab_id}/widgets")
def list_widgets(tab_id: int):
    c = db.get_conn()
    rows = c.execute("SELECT * FROM widgets WHERE tab_id=? ORDER BY position, id", (tab_id,)).fetchall()
    out = [_widget_dict(c, r) for r in rows]
    c.close()
    return out


@app.post("/api/widgets")
def create_widget(w: WidgetIn):
    if w.type not in VALID_TYPES:
        raise HTTPException(400, f"unknown widget type: {w.type}")
    c = db.get_conn()
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM widgets WHERE tab_id=?",
                    (w.tab_id,)).fetchone()[0]
    cur = c.execute(
        "INSERT INTO widgets(tab_id, type, title, config, position, created_at) VALUES (?,?,?,?,?,?)",
        (w.tab_id, w.type, w.title.strip() or w.type, json.dumps(w.config), pos, now_iso()),
    )
    c.commit()
    row = c.execute("SELECT * FROM widgets WHERE id=?", (cur.lastrowid,)).fetchone()
    out = _widget_dict(c, row)
    c.close()
    return out


@app.patch("/api/widgets/{wid}")
def patch_widget(wid: int, p: WidgetPatch):
    c = db.get_conn()
    row = c.execute("SELECT * FROM widgets WHERE id=?", (wid,)).fetchone()
    if not row:
        raise HTTPException(404, "no such widget")
    if p.title is not None:
        c.execute("UPDATE widgets SET title=? WHERE id=?", (p.title.strip(), wid))
    if p.config is not None:
        c.execute("UPDATE widgets SET config=? WHERE id=?", (json.dumps(p.config), wid))
    if p.position is not None:
        c.execute("UPDATE widgets SET position=? WHERE id=?", (p.position, wid))
    c.commit()
    row = c.execute("SELECT * FROM widgets WHERE id=?", (wid,)).fetchone()
    out = _widget_dict(c, row)
    c.close()
    return out


@app.delete("/api/widgets/{wid}")
def delete_widget(wid: int):
    c = db.get_conn()
    c.execute("DELETE FROM widgets WHERE id=?", (wid,))
    c.commit()
    c.close()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Logging daily values
# ----------------------------------------------------------------------------
class LogIn(BaseModel):
    day: Optional[str] = None
    value: float


@app.put("/api/widgets/{wid}/log")
def set_log(wid: int, l: LogIn):
    day = l.day or today_str()
    c = db.get_conn()
    c.execute(
        "INSERT INTO logs(widget_id, day, value) VALUES (?,?,?) "
        "ON CONFLICT(widget_id, day) DO UPDATE SET value=excluded.value",
        (wid, day, l.value),
    )
    c.commit()
    row = c.execute("SELECT * FROM widgets WHERE id=?", (wid,)).fetchone()
    out = _widget_dict(c, row)
    c.close()
    return out


@app.delete("/api/widgets/{wid}/log/{day}")
def clear_log(wid: int, day: str):
    c = db.get_conn()
    c.execute("DELETE FROM logs WHERE widget_id=? AND day=?", (wid, day))
    c.commit()
    c.close()
    return {"ok": True}


# ----------------------------------------------------------------------------
# 75 Hard: per-day task check-off + progress photos
# ----------------------------------------------------------------------------
HARD75_DIR = os.path.join(db.DATA_DIR, "uploads", "hard75")
HARD75_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}


def _require_hard75(c, wid):
    row = c.execute("SELECT type FROM widgets WHERE id=?", (wid,)).fetchone()
    if not row or row["type"] != "hard75":
        c.close()
        raise HTTPException(404, "no such 75 hard widget")


class Hard75DayIn(BaseModel):
    day: Optional[str] = None
    tasks: dict = {}


@app.put("/api/widgets/{wid}/hard75/day")
def hard75_set_day(wid: int, d: Hard75DayIn):
    """Merge a partial set of task toggles into a day's record (upsert)."""
    day = d.day or today_str()
    c = db.get_conn()
    _require_hard75(c, wid)
    existing = c.execute("SELECT tasks FROM hard75 WHERE widget_id=? AND day=?", (wid, day)).fetchone()
    tasks = json.loads(existing["tasks"] or "{}") if existing else {}
    for k, v in d.tasks.items():
        if v:
            tasks[k] = True
        else:
            tasks.pop(k, None)
    c.execute(
        "INSERT INTO hard75(widget_id, day, tasks) VALUES (?,?,?) "
        "ON CONFLICT(widget_id, day) DO UPDATE SET tasks=excluded.tasks",
        (wid, day, json.dumps(tasks)),
    )
    c.commit()
    out = _widget_dict(c, c.execute("SELECT * FROM widgets WHERE id=?", (wid,)).fetchone())
    c.close()
    return out


@app.post("/api/widgets/{wid}/hard75/photo")
async def hard75_set_photo(wid: int, day: str = Form(...), file: UploadFile = File(...)):
    c = db.get_conn()
    _require_hard75(c, wid)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in HARD75_EXT:
        ext = ".jpg"
    folder = os.path.join(HARD75_DIR, str(wid))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{day}{ext}")
    # drop any earlier photo for this day stored under a different extension
    for old in glob.glob(os.path.join(folder, f"{day}.*")):
        if old != path:
            try:
                os.remove(old)
            except OSError:
                pass
    data = await file.read()
    with open(path, "wb") as fh:
        fh.write(data)
    rel = os.path.relpath(path, db.DATA_DIR)
    c.execute(
        "INSERT INTO hard75(widget_id, day, tasks, photo) VALUES (?,?,'{}',?) "
        "ON CONFLICT(widget_id, day) DO UPDATE SET photo=excluded.photo",
        (wid, day, rel),
    )
    c.commit()
    out = _widget_dict(c, c.execute("SELECT * FROM widgets WHERE id=?", (wid,)).fetchone())
    c.close()
    return out


@app.get("/api/widgets/{wid}/hard75/photo/{day}")
def hard75_get_photo(wid: int, day: str):
    c = db.get_conn()
    row = c.execute("SELECT photo FROM hard75 WHERE widget_id=? AND day=?", (wid, day)).fetchone()
    c.close()
    if not row or not row["photo"]:
        raise HTTPException(404, "no photo")
    path = os.path.join(db.DATA_DIR, row["photo"])
    if not os.path.exists(path):
        raise HTTPException(404, "missing file")
    return FileResponse(path)


@app.delete("/api/widgets/{wid}/hard75/photo/{day}")
def hard75_del_photo(wid: int, day: str):
    c = db.get_conn()
    _require_hard75(c, wid)
    row = c.execute("SELECT photo FROM hard75 WHERE widget_id=? AND day=?", (wid, day)).fetchone()
    if row and row["photo"]:
        try:
            os.remove(os.path.join(db.DATA_DIR, row["photo"]))
        except OSError:
            pass
        c.execute("UPDATE hard75 SET photo=NULL WHERE widget_id=? AND day=?", (wid, day))
        c.commit()
    out = _widget_dict(c, c.execute("SELECT * FROM widgets WHERE id=?", (wid,)).fetchone())
    c.close()
    return out


@app.get("/api/hard75/active")
def hard75_active():
    """All started (not-yet-won) 75 Hard widgets with today's task state — used by
    the today tab so the challenge can be ticked off from one place."""
    c = db.get_conn()
    day = today_str()
    out = []
    for r in c.execute("SELECT * FROM widgets WHERE type='hard75'").fetchall():
        cfg = json.loads(r["config"] or "{}")
        if not cfg.get("start_date"):
            continue
        recs = _hard75_records(c, r["id"])
        st = hard.compute(cfg, recs, day)
        if st["won"]:
            continue
        td = (recs.get(day, {}) or {}).get("tasks") or {}
        out.append({
            "widget_id": r["id"], "tab_id": r["tab_id"], "title": r["title"],
            "day": st["day"], "duration": st["duration"], "today_complete": st["today_complete"],
            "require_photo": st["require_photo"], "photo_done": bool((recs.get(day, {}) or {}).get("photo")),
            "tasks": [{"key": t["key"], "label": t["label"], "done": bool(td.get(t["key"]))} for t in st["tasks"]],
        })
    c.close()
    return out


# ----------------------------------------------------------------------------
# Todos
# ----------------------------------------------------------------------------
class TodoIn(BaseModel):
    text: str


class TodoPatch(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None


@app.post("/api/widgets/{wid}/todos")
def add_todo(wid: int, t: TodoIn):
    c = db.get_conn()
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM todos WHERE widget_id=?", (wid,)).fetchone()[0]
    cur = c.execute("INSERT INTO todos(widget_id, text, done, position) VALUES (?,?,0,?)",
                    (wid, t.text.strip(), pos))
    c.commit()
    row = c.execute("SELECT * FROM todos WHERE id=?", (cur.lastrowid,)).fetchone()
    c.close()
    return dict(row)


@app.patch("/api/todos/{tid}")
def patch_todo(tid: int, p: TodoPatch):
    c = db.get_conn()
    if p.text is not None:
        c.execute("UPDATE todos SET text=? WHERE id=?", (p.text.strip(), tid))
    if p.done is not None:
        c.execute("UPDATE todos SET done=? WHERE id=?", (1 if p.done else 0, tid))
    c.commit()
    c.close()
    return {"ok": True}


@app.delete("/api/todos/{tid}")
def delete_todo(tid: int):
    c = db.get_conn()
    c.execute("DELETE FROM todos WHERE id=?", (tid,))
    c.commit()
    c.close()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Today check-in: all trackable widgets across tabs
# ----------------------------------------------------------------------------
@app.get("/api/today")
def today_view():
    day = today_str()
    c = db.get_conn()
    rows = c.execute(
        "SELECT w.*, t.name AS tab_name FROM widgets w JOIN tabs t ON t.id=w.tab_id "
        "WHERE w.type IN ('habit','counter','number','progress') ORDER BY t.position, w.position"
    ).fetchall()
    out = []
    for r in rows:
        w = dict(r)
        w["config"] = json.loads(w.get("config") or "{}")
        log = c.execute("SELECT value FROM logs WHERE widget_id=? AND day=?", (w["id"], day)).fetchone()
        w["today_value"] = log["value"] if log else None
        out.append({
            "id": w["id"], "type": w["type"], "title": w["title"],
            "tab_id": w["tab_id"], "tab_name": w["tab_name"],
            "config": w["config"], "today_value": w["today_value"],
        })
    c.close()
    return {"day": day, "items": out}


# ----------------------------------------------------------------------------
# Dashboard summary + aggregate heatmap
# ----------------------------------------------------------------------------
@app.get("/api/dashboard")
def dashboard():
    c = db.get_conn()
    day = today_str()
    # aggregate activity: a day "counts" for any habit done, plus kaizen activity
    # (a landed daily highlight and each completed micro-commitment) that day.
    habit_ids = [r["id"] for r in c.execute("SELECT id FROM widgets WHERE type='habit'").fetchall()]
    activity = {}
    if habit_ids:
        q = "SELECT day, COUNT(*) n FROM logs WHERE value>0 AND widget_id IN (%s) GROUP BY day" % (
            ",".join("?" * len(habit_ids)))
        for r in c.execute(q, habit_ids).fetchall():
            activity[r["day"]] = r["n"]
    # kaizen: highlights landed
    for r in c.execute("SELECT day FROM kaizen_days WHERE highlight_done=1").fetchall():
        activity[r["day"]] = activity.get(r["day"], 0) + 1
    # kaizen: micro-commitments completed
    for r in c.execute("SELECT day, COUNT(*) n FROM kaizen_logs WHERE done=1 GROUP BY day").fetchall():
        activity[r["day"]] = activity.get(r["day"], 0) + r["n"]
    done_days = list(activity.keys())
    cur_streak, longest = pace.streaks(done_days)

    # today's completion across trackable habits/counters with targets
    trackables = c.execute(
        "SELECT * FROM widgets WHERE type IN ('habit','counter','number','progress')"
    ).fetchall()
    total = 0
    done = 0
    for r in trackables:
        cfg = json.loads(r["config"] or "{}")
        log = c.execute("SELECT value FROM logs WHERE widget_id=? AND day=?", (r["id"], day)).fetchone()
        v = log["value"] if log else None
        if r["type"] == "habit":
            total += 1
            if v:
                done += 1
        elif r["type"] == "counter":
            tgt = float(cfg.get("daily_target", 0) or 0)
            if tgt > 0:
                total += 1
                if v and v >= tgt:
                    done += 1
        elif r["type"] in ("number", "progress"):
            total += 1
            if v is not None:
                done += 1

    # progress widgets needing attention
    behind = []
    for r in c.execute("SELECT * FROM widgets WHERE type='progress'").fetchall():
        cfg = json.loads(r["config"] or "{}")
        logs = [dict(x) for x in c.execute("SELECT day, value FROM logs WHERE widget_id=? ORDER BY day", (r["id"],)).fetchall()]
        p = pace.pace(cfg, logs)
        if p.get("status") == "behind":
            behind.append({"id": r["id"], "title": r["title"], "pace": p})

    c.close()
    return {
        "day": day,
        "activity": activity,
        "current_streak": cur_streak,
        "longest_streak": longest,
        "today_done": done,
        "today_total": total,
        "behind": behind,
    }


# ----------------------------------------------------------------------------
# Entries: notes + journal (unified store, single FTS index)
# ----------------------------------------------------------------------------
class EntryIn(BaseModel):
    kind: str = "note"
    title: str = ""
    body: str = ""
    entry_date: Optional[str] = None
    slot: Optional[str] = None


@app.get("/api/entries")
def list_entries(kind: Optional[str] = None):
    c = db.get_conn()
    if kind in ("note", "journal"):
        rows = c.execute(
            "SELECT id, kind, title, entry_date, slot, updated_at, substr(body,1,160) AS preview "
            "FROM entries WHERE kind=? ORDER BY COALESCE(entry_date, updated_at) DESC, id DESC", (kind,)
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT id, kind, title, entry_date, slot, updated_at, substr(body,1,160) AS preview "
            "FROM entries ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


@app.get("/api/entries/{eid}")
def get_entry(eid: int):
    c = db.get_conn()
    row = c.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "no such entry")
    return dict(row)


@app.post("/api/entries")
def create_entry(e: EntryIn):
    c = db.get_conn()
    ts = now_iso()
    cur = c.execute(
        "INSERT INTO entries(kind,title,body,entry_date,slot,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (e.kind, e.title, e.body, e.entry_date, e.slot, ts, ts),
    )
    c.commit()
    row = c.execute("SELECT * FROM entries WHERE id=?", (cur.lastrowid,)).fetchone()
    c.close()
    return dict(row)


@app.patch("/api/entries/{eid}")
def update_entry(eid: int, e: EntryIn):
    c = db.get_conn()
    c.execute(
        "UPDATE entries SET title=?, body=?, entry_date=?, slot=?, kind=?, updated_at=? WHERE id=?",
        (e.title, e.body, e.entry_date, e.slot, e.kind, now_iso(), eid),
    )
    c.commit()
    row = c.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "no such entry")
    return dict(row)


@app.delete("/api/entries/{eid}")
def delete_entry(eid: int):
    c = db.get_conn()
    c.execute("DELETE FROM entries WHERE id=?", (eid,))
    c.commit()
    c.close()
    return {"ok": True}


def _fts_query(raw: str) -> str:
    """Forgiving query: each term becomes a prefix match, AND-ed together."""
    terms = re.findall(r"[\w']+", raw.lower())
    if not terms:
        return ""
    return " ".join(f'"{t}"*' for t in terms)


@app.get("/api/search")
def search(q: str, kind: Optional[str] = None):
    fq = _fts_query(q)
    if not fq:
        return []
    c = db.get_conn()
    params = [fq]
    kind_clause = ""
    if kind in ("note", "journal"):
        kind_clause = " AND e.kind=?"
        params.append(kind)
    sql = (
        "SELECT e.id, e.kind, e.title, e.entry_date, e.slot, e.updated_at, "
        "snippet(entries_fts, 1, '\u3008', '\u3009', '…', 12) AS snippet, "
        "bm25(entries_fts) AS score "
        "FROM entries_fts JOIN entries e ON e.id=entries_fts.rowid "
        "WHERE entries_fts MATCH ?" + kind_clause +
        " ORDER BY score LIMIT 50"
    )
    try:
        rows = c.execute(sql, params).fetchall()
    except Exception:
        rows = []
    c.close()
    return [dict(r) for r in rows]


@app.post("/api/import")
async def import_files(files: list[UploadFile] = File(...)):
    c = db.get_conn()
    created = []
    for f in files:
        raw = (await f.read()).decode("utf-8", errors="replace")
        title = os.path.splitext(os.path.basename(f.filename or "untitled"))[0]
        # use first markdown heading as title if present
        m = re.search(r"^\s*#\s+(.+)$", raw, re.MULTILINE)
        if m:
            title = m.group(1).strip()
        ts = now_iso()
        cur = c.execute(
            "INSERT INTO entries(kind,title,body,created_at,updated_at) VALUES ('note',?,?,?,?)",
            (title, raw, ts, ts),
        )
        created.append({"id": cur.lastrowid, "title": title})
    c.commit()
    c.close()
    return {"imported": len(created), "entries": created}


# ----------------------------------------------------------------------------
# Journal prompts
# ----------------------------------------------------------------------------
@app.get("/api/prompts")
def list_prompts():
    c = db.get_conn()
    rows = c.execute("SELECT * FROM journal_prompts ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


class PromptIn(BaseModel):
    text: str
    slot: str = "pm"
    active: bool = True
    weekdays: str = ""   # comma list of 0=Mon..6=Sun; empty = any day


@app.post("/api/prompts")
def add_prompt(p: PromptIn):
    c = db.get_conn()
    cur = c.execute("INSERT INTO journal_prompts(text,slot,active,weekdays) VALUES (?,?,?,?)",
                    (p.text.strip(), p.slot, 1 if p.active else 0, p.weekdays.strip()))
    c.commit()
    row = c.execute("SELECT * FROM journal_prompts WHERE id=?", (cur.lastrowid,)).fetchone()
    c.close()
    return dict(row)


@app.delete("/api/prompts/{pid}")
def delete_prompt(pid: int):
    c = db.get_conn()
    c.execute("DELETE FROM journal_prompts WHERE id=?", (pid,))
    c.commit()
    c.close()
    return {"ok": True}


@app.get("/api/journal/today")
def journal_today(slot: str = "pm"):
    """Return today's journal entry for the slot (if any) + a prompt to use.
    Honors per-prompt weekday scheduling: a prompt scheduled for specific
    weekdays only appears on those days; unscheduled prompts are always eligible."""
    day = today_str()
    wd = str(date.today().weekday())  # 0=Mon
    c = db.get_conn()
    row = c.execute("SELECT * FROM entries WHERE kind='journal' AND entry_date=? AND slot=?",
                    (day, slot)).fetchone()
    allp = c.execute(
        "SELECT * FROM journal_prompts WHERE active=1 AND slot IN (?, 'any')", (slot,)
    ).fetchall()
    c.close()

    def scheduled_today(p):
        days = (p["weekdays"] or "").strip()
        if not days:
            return True
        return wd in [x.strip() for x in days.split(",") if x.strip() != ""]

    # prefer prompts explicitly scheduled for today; otherwise fall back to general ones
    todays = [p for p in allp if (p["weekdays"] or "").strip() and scheduled_today(p)]
    pool = todays if todays else [p for p in allp if not (p["weekdays"] or "").strip()]
    if not pool:
        pool = [p for p in allp if scheduled_today(p)]

    chosen = None
    if pool:
        idx = (date.today().toordinal() + (0 if slot == "am" else 1)) % len(pool)
        chosen = dict(pool[idx])
    return {"day": day, "slot": slot, "entry": dict(row) if row else None, "prompt": chosen}


# ----------------------------------------------------------------------------
# Backup / export
# ----------------------------------------------------------------------------
@app.get("/api/export/json")
def export_json():
    c = db.get_conn()
    data = {"exported_at": now_iso(), "tables": {}}
    for tbl in ("tabs", "widgets", "logs", "todos", "hard75",
                "kaizen_days", "kaizen_commitments", "kaizen_logs",
                "entries", "journal_prompts", "settings"):
        rows = c.execute(f"SELECT * FROM {tbl}").fetchall()
        data["tables"][tbl] = [dict(r) for r in rows]
    c.close()
    buf = io.BytesIO(json.dumps(data, indent=2).encode())
    fn = f"lifeboard-backup-{today_str()}.json"
    return StreamingResponse(buf, media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.get("/api/export/csv")
def export_csv():
    """Flat CSV of every daily log with widget + tab context."""
    c = db.get_conn()
    rows = c.execute(
        "SELECT t.name AS tab, w.title AS widget, w.type, l.day, l.value "
        "FROM logs l JOIN widgets w ON w.id=l.widget_id JOIN tabs t ON t.id=w.tab_id "
        "ORDER BY l.day, t.name, w.title"
    ).fetchall()
    c.close()
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["tab", "widget", "type", "day", "value"])
    for r in rows:
        wr.writerow([r["tab"], r["widget"], r["type"], r["day"], r["value"]])
    out = io.BytesIO(buf.getvalue().encode())
    fn = f"lifeboard-logs-{today_str()}.csv"
    return StreamingResponse(out, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.get("/api/export/db")
def export_db():
    if not os.path.exists(db.DB_PATH):
        raise HTTPException(404, "no database yet")
    return FileResponse(db.DB_PATH, media_type="application/octet-stream",
                        filename=f"lifeboard-{today_str()}.db")


@app.get("/api/health")
def health():
    return {"ok": True, "time": now_iso()}


# ============================================================================
# v2 additions
# ============================================================================

# ---- widget reordering (edit mode drag-and-drop) --------------------------
class ReorderIn(BaseModel):
    order: list[int]


@app.patch("/api/tabs/{tab_id}/reorder")
def reorder_widgets(tab_id: int, r: ReorderIn):
    c = db.get_conn()
    for pos, wid in enumerate(r.order):
        c.execute("UPDATE widgets SET position=? WHERE id=? AND tab_id=?", (pos, wid, tab_id))
    c.commit()
    c.close()
    return {"ok": True}


class TabReorderIn(BaseModel):
    order: list[int]


@app.patch("/api/tabs/reorder")
def reorder_tabs(r: TabReorderIn):
    c = db.get_conn()
    for pos, tid in enumerate(r.order):
        c.execute("UPDATE tabs SET position=? WHERE id=?", (pos, tid))
    c.commit()
    c.close()
    return {"ok": True}


# ---- goal templates -------------------------------------------------------
@app.get("/api/templates")
def list_templates():
    return [{"id": t["id"], "name": t["name"], "desc": t["desc"],
             "widgets": [w["title"] for w in t["widgets"]]} for t in extras.TEMPLATES]


class FromTemplateIn(BaseModel):
    template_id: str
    name: Optional[str] = None


@app.post("/api/tabs/from_template")
def tab_from_template(t: FromTemplateIn):
    tpl = extras.template_by_id(t.template_id)
    if not tpl:
        raise HTTPException(404, "unknown template")
    c = db.get_conn()
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM tabs").fetchone()[0]
    cur = c.execute("INSERT INTO tabs(name, position, created_at) VALUES (?,?,?)",
                    (t.name or tpl["name"], pos, now_iso()))
    tab_id = cur.lastrowid
    for i, w in enumerate(tpl["widgets"]):
        c.execute(
            "INSERT INTO widgets(tab_id, type, title, config, position, created_at) VALUES (?,?,?,?,?,?)",
            (tab_id, w["type"], w["title"], json.dumps(w.get("config", {})), i, now_iso()))
    c.commit()
    row = c.execute("SELECT * FROM tabs WHERE id=?", (tab_id,)).fetchone()
    c.close()
    return dict(row)


# ---- review / trends ------------------------------------------------------
@app.get("/api/review")
def review(period: str = "week"):
    if period not in ("week", "month", "year"):
        period = "week"
    c = db.get_conn()
    out = extras.review(c, period)
    c.close()
    return out


# ---- config import / export (structure only, no logs) ---------------------
@app.get("/api/config/export")
def config_export():
    c = db.get_conn()
    tabs = []
    for t in c.execute("SELECT * FROM tabs ORDER BY position, id").fetchall():
        ws = c.execute("SELECT type, title, config, position FROM widgets WHERE tab_id=? ORDER BY position, id",
                       (t["id"],)).fetchall()
        tabs.append({"name": t["name"], "position": t["position"],
                     "widgets": [{"type": w["type"], "title": w["title"],
                                  "config": json.loads(w["config"] or "{}"),
                                  "position": w["position"]} for w in ws]})
    prompts = [{"text": p["text"], "slot": p["slot"], "active": p["active"], "weekdays": p["weekdays"]}
               for p in c.execute("SELECT * FROM journal_prompts").fetchall()]
    c.close()
    cfg = {"version": 2, "exported_at": now_iso(), "tabs": tabs, "journal_prompts": prompts}
    buf = io.BytesIO(json.dumps(cfg, indent=2).encode())
    fn = f"lifeboard-config-{today_str()}.json"
    return StreamingResponse(buf, media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})


class ConfigIn(BaseModel):
    config: dict
    replace: bool = False


@app.post("/api/config/import")
def config_import(c_in: ConfigIn):
    cfg = c_in.config
    conn = db.get_conn()
    if c_in.replace:
        conn.execute("DELETE FROM tabs")           # cascades widgets/logs/todos
        conn.execute("DELETE FROM journal_prompts")
    base = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM tabs").fetchone()[0]
    for ti, t in enumerate(cfg.get("tabs", [])):
        cur = conn.execute("INSERT INTO tabs(name, position, created_at) VALUES (?,?,?)",
                           (t.get("name", "Imported"), base + ti, now_iso()))
        tab_id = cur.lastrowid
        for wi, w in enumerate(t.get("widgets", [])):
            conn.execute(
                "INSERT INTO widgets(tab_id, type, title, config, position, created_at) VALUES (?,?,?,?,?,?)",
                (tab_id, w.get("type", "note"), w.get("title", ""), json.dumps(w.get("config", {})),
                 w.get("position", wi), now_iso()))
    for p in cfg.get("journal_prompts", []):
        conn.execute("INSERT INTO journal_prompts(text, slot, active, weekdays) VALUES (?,?,?,?)",
                     (p.get("text", ""), p.get("slot", "pm"), int(p.get("active", 1)), p.get("weekdays", "")))
    conn.commit()
    conn.close()
    return {"ok": True, "tabs": len(cfg.get("tabs", []))}


# ---- related notes (TF-IDF) ----------------------------------------------
@app.get("/api/entries/{eid}/related")
def entry_related(eid: int):
    c = db.get_conn()
    out = extras.related(c, eid)
    c.close()
    return out


# ---- ntfy reminder settings ----------------------------------------------
@app.get("/api/reminders")
def get_reminders():
    c = db.get_conn()
    cfg = {
        "enabled": db.get_setting(c, "ntfy_enabled", "0") == "1",
        "server": db.get_setting(c, "ntfy_server", "https://ntfy.sh"),
        "topic": db.get_setting(c, "ntfy_topic", ""),
        "time": db.get_setting(c, "ntfy_time", "20:00"),
    }
    c.close()
    return cfg


class RemindersIn(BaseModel):
    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str = ""
    time: str = "20:00"


@app.put("/api/reminders")
def set_reminders(r: RemindersIn):
    c = db.get_conn()
    db.set_setting(c, "ntfy_enabled", "1" if r.enabled else "0")
    db.set_setting(c, "ntfy_server", r.server.strip() or "https://ntfy.sh")
    db.set_setting(c, "ntfy_topic", r.topic.strip())
    db.set_setting(c, "ntfy_time", r.time.strip() or "20:00")
    c.commit()
    c.close()
    return {"ok": True}


@app.post("/api/reminders/test")
def test_reminder():
    sent, detail = reminders.send_reminder(force=True)
    return {"sent": sent, "detail": detail}


# ---- voicetodo integration (proxy to a companion voicetodo-server) --------
# Lifeboard forwards these to the configured voicetodo server so the API key
# stays server-side and the browser avoids cross-origin calls. See vtd.py.
class VtdConfigIn(BaseModel):
    url: str = ""
    api_key: str = ""


@app.get("/api/voicetodo/config")
def vtd_get_config():
    c = db.get_conn()
    cfg = vtd.get_config(c)
    c.close()
    return {"configured": bool(cfg["url"]), "url": cfg["url"], "api_key": cfg["api_key"]}


@app.put("/api/voicetodo/config")
def vtd_set_config(b: VtdConfigIn):
    c = db.get_conn()
    vtd.set_config(c, b.url, b.api_key)
    c.commit()
    cfg = vtd.get_config(c)
    c.close()
    return {"configured": bool(cfg["url"]), "url": cfg["url"], "api_key": cfg["api_key"]}


def _vtd_cfg():
    c = db.get_conn()
    cfg = vtd.get_config(c)
    c.close()
    if not cfg["url"]:
        raise HTTPException(400, "voicetodo server not configured")
    return cfg


def _vtd(fn):
    """Run an upstream call, translating VtdError into an HTTP response."""
    try:
        return fn(_vtd_cfg())
    except vtd.VtdError as e:
        raise HTTPException(e.status or 502, str(e))


@app.get("/api/voicetodo/health")
def vtd_health():
    return _vtd(lambda cfg: vtd.request(cfg, "GET", "/health", auth=False))


@app.get("/api/voicetodo/todos")
def vtd_list_todos(include_completed: bool = False):
    path = "/todos?include_completed=true" if include_completed else "/todos"
    return _vtd(lambda cfg: vtd.request(cfg, "GET", path))


class VtdTodoCreate(BaseModel):
    text: str
    priority: int = 0
    due_at: Optional[str] = None


@app.post("/api/voicetodo/todos")
def vtd_create_todo(b: VtdTodoCreate):
    body = {"text": b.text, "priority": b.priority}
    if b.due_at is not None:
        body["due_at"] = b.due_at
    return _vtd(lambda cfg: vtd.request(cfg, "POST", "/todos", json_body=body))


class VtdTodoPatch(BaseModel):
    text: Optional[str] = None
    priority: Optional[int] = None
    completed: Optional[bool] = None
    due_at: Optional[str] = None   # "" clears, ISO sets, omitted leaves alone


@app.patch("/api/voicetodo/todos/{tid}")
def vtd_patch_todo(tid: int, b: VtdTodoPatch):
    body = {}
    if b.text is not None:
        body["text"] = b.text
    if b.priority is not None:
        body["priority"] = b.priority
    if b.completed is not None:
        body["completed"] = b.completed
    if b.due_at is not None:
        body["due_at"] = b.due_at
    return _vtd(lambda cfg: vtd.request(cfg, "PATCH", f"/todos/{tid}", json_body=body))


@app.delete("/api/voicetodo/todos/{tid}")
def vtd_delete_todo(tid: int):
    return _vtd(lambda cfg: vtd.request(cfg, "DELETE", f"/todos/{tid}"))


@app.get("/api/voicetodo/notes")
def vtd_list_notes(limit: int = 50):
    return _vtd(lambda cfg: vtd.request(cfg, "GET", f"/notes?limit={int(limit)}"))


@app.get("/api/voicetodo/notes/{nid}")
def vtd_get_note(nid: int):
    return _vtd(lambda cfg: vtd.request(cfg, "GET", f"/notes/{nid}"))


@app.post("/api/voicetodo/audio")
async def vtd_audio(audio: UploadFile = File(...), source: str = Form("lifeboard")):
    cfg = _vtd_cfg()
    data = await audio.read()
    try:
        return vtd.upload_audio(cfg, data, audio.filename or "memo.webm", source=source)
    except vtd.VtdError as e:
        raise HTTPException(e.status or 502, str(e))


# ---- kaizen ("light mode"): daily highlight + micro-commitments + brain dump
@app.get("/api/kaizen")
def kaizen_get():
    c = db.get_conn()
    out = kaizen.state(c, today_str())
    c.close()
    return out


def _kaizen_upsert_day(c, day, *, highlight=None, highlight_done=None):
    """Upsert a kaizen_days row's highlight fields. (Brain dumps live in the
    shared entries store — see _kaizen_set_braindump.)"""
    row = c.execute("SELECT highlight, highlight_done FROM kaizen_days WHERE day=?", (day,)).fetchone()
    h = row["highlight"] if row else ""
    hd = row["highlight_done"] if row else 0
    if highlight is not None:
        h = highlight.strip()
    if highlight_done is not None:
        hd = 1 if highlight_done else 0
    c.execute(
        "INSERT INTO kaizen_days(day, highlight, highlight_done, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(day) DO UPDATE SET highlight=excluded.highlight, "
        "highlight_done=excluded.highlight_done, updated_at=excluded.updated_at",
        (day, h, hd, now_iso()),
    )


def _kaizen_set_braindump(c, day, body):
    """Store a day's brain dump as a dated journal entry in the shared `entries`
    store, so it's browsable and full-text searchable under the journal filter.
    Clearing the text removes the entry (no empty clutter)."""
    body = body or ""
    row = c.execute(
        "SELECT id FROM entries WHERE kind='journal' AND slot='dump' AND entry_date=?", (day,)).fetchone()
    ts = now_iso()
    if row:
        if body.strip():
            c.execute("UPDATE entries SET title=?, body=?, updated_at=? WHERE id=?",
                      (f"Brain dump — {day}", body, ts, row["id"]))
        else:
            c.execute("DELETE FROM entries WHERE id=?", (row["id"],))
    elif body.strip():
        c.execute(
            "INSERT INTO entries(kind,title,body,entry_date,slot,created_at,updated_at) "
            "VALUES ('journal',?,?,?,'dump',?,?)", (f"Brain dump — {day}", body, day, ts, ts))


class KaizenHighlightIn(BaseModel):
    day: Optional[str] = None
    text: Optional[str] = None
    done: Optional[bool] = None


@app.put("/api/kaizen/highlight")
def kaizen_set_highlight(b: KaizenHighlightIn):
    c = db.get_conn()
    _kaizen_upsert_day(c, b.day or today_str(), highlight=b.text, highlight_done=b.done)
    c.commit()
    out = kaizen.state(c, today_str())
    c.close()
    return out


class KaizenDumpIn(BaseModel):
    day: Optional[str] = None
    body: str = ""


@app.put("/api/kaizen/braindump")
def kaizen_set_dump(b: KaizenDumpIn):
    c = db.get_conn()
    _kaizen_set_braindump(c, b.day or today_str(), b.body)
    c.commit()
    out = kaizen.state(c, today_str())
    c.close()
    return out


class KaizenCommitIn(BaseModel):
    text: str


@app.post("/api/kaizen/commitments")
def kaizen_add_commitment(b: KaizenCommitIn):
    c = db.get_conn()
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM kaizen_commitments").fetchone()[0]
    c.execute("INSERT INTO kaizen_commitments(text, position, created_at) VALUES (?,?,?)",
              (b.text.strip() or "micro-commitment", pos, now_iso()))
    c.commit()
    out = kaizen.state(c, today_str())
    c.close()
    return out


@app.delete("/api/kaizen/commitments/{cid}")
def kaizen_del_commitment(cid: int):
    c = db.get_conn()
    c.execute("DELETE FROM kaizen_commitments WHERE id=?", (cid,))
    c.commit()
    out = kaizen.state(c, today_str())
    c.close()
    return out


class KaizenLogIn(BaseModel):
    day: Optional[str] = None
    done: bool = True


@app.put("/api/kaizen/commitments/{cid}/log")
def kaizen_log_commitment(cid: int, b: KaizenLogIn):
    day = b.day or today_str()
    c = db.get_conn()
    if not c.execute("SELECT 1 FROM kaizen_commitments WHERE id=?", (cid,)).fetchone():
        c.close()
        raise HTTPException(404, "no such commitment")
    if b.done:
        c.execute("INSERT INTO kaizen_logs(commitment_id, day, done) VALUES (?,?,1) "
                  "ON CONFLICT(commitment_id, day) DO UPDATE SET done=1", (cid, day))
    else:
        c.execute("DELETE FROM kaizen_logs WHERE commitment_id=? AND day=?", (cid, day))
    c.commit()
    out = kaizen.state(c, today_str())
    c.close()
    return out


# ============================================================================
# Gym / weightlifting tracker
# ============================================================================
def _gym_ex_dict(r):
    return gym.exercise_row(r)


def _gym_session_full(c, sid):
    s = c.execute("SELECT * FROM gym_sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        return None
    out = dict(s)
    out["duration"] = gym.session_duration(s["started_at"], s["ended_at"])
    exs = []
    for se in c.execute("SELECT * FROM gym_session_exercises WHERE session_id=? ORDER BY position, id", (sid,)).fetchall():
        ex = c.execute("SELECT * FROM gym_exercises WHERE id=?", (se["exercise_id"],)).fetchone()
        pb = gym.prior_best(c, se["exercise_id"], sid)        # best from OTHER sessions
        prev = gym.last_performance(c, se["exercise_id"], sid)
        sets = []
        prev_done = None
        for x in c.execute("SELECT * FROM gym_sets WHERE se_id=? ORDER BY set_no, id", (se["id"],)).fetchall():
            d = dict(x)
            # rest = seconds between this set's completion and the previous one's
            if d.get("done") and d.get("done_at"):
                t = gym._parse_ts(d["done_at"])
                if prev_done and t:
                    d["rest"] = max(0, round((t - prev_done).total_seconds()))
                prev_done = t or prev_done
            # live PR flag: a completed set beating the best from prior sessions
            if d.get("done") and d.get("weight") and d.get("reps"):
                e = gym.epley_1rm(d["weight"], d["reps"])
                if e > pb["e1rm"] + 0.05 or d["weight"] > pb["weight"] + 0.001:
                    d["pr"] = True
            sets.append(d)
        exs.append({
            "id": se["id"], "exercise_id": se["exercise_id"],
            "name": ex["name"] if ex else "?", "equipment": ex["equipment"] if ex else "",
            "primary": json.loads(ex["primary_m"]) if ex else [],
            "alts": json.loads(ex["alts"]) if ex else [],
            "superset": se["superset"], "notes": se["notes"], "prev": prev, "sets": sets,
        })
    out["exercises"] = exs
    return out


# ---- meta + exercise library ----------------------------------------------
@app.get("/api/gym/meta")
def gym_meta():
    return {
        "muscles": [{"name": m, "low": lo, "high": hi} for m, (lo, hi) in gym.MUSCLES.items()],
        "equipment": gym.EQUIPMENT,
        "programs": [{"id": p["id"], "name": p["name"], "days_per_week": p["days_per_week"],
                      "goal": p["goal"], "summary": p["summary"], "why": p["why"],
                      "days": [d["name"] for d in p["days"]]} for p in gym.PROGRAMS],
    }


@app.get("/api/gym/recommend")
def gym_recommend(days: int = 3, goal: str = "hypertrophy"):
    rec = gym.recommend_split(days, goal)
    prog = next((p for p in gym.PROGRAMS if p["id"] == rec["program_id"]), None)
    rec["program"] = {"id": prog["id"], "name": prog["name"], "days": [d["name"] for d in prog["days"]]} if prog else None
    return rec


@app.get("/api/gym/exercises")
def gym_list_exercises(q: Optional[str] = None, muscle: Optional[str] = None,
                       equipment: Optional[str] = None, category: Optional[str] = None,
                       pattern: Optional[str] = None):
    c = db.get_conn()
    rows = c.execute("SELECT * FROM gym_exercises ORDER BY is_custom DESC, name").fetchall()
    c.close()
    out = []
    ql = (q or "").strip().lower()
    for r in rows:
        d = _gym_ex_dict(r)
        if ql and ql not in d["name"].lower():
            continue
        if muscle and muscle not in d["primary"] and muscle not in d["secondary"]:
            continue
        if equipment and d["equipment"] != equipment:
            continue
        if category and d["category"] != category:
            continue
        if pattern and d["pattern"] != pattern:
            continue
        out.append(d)
    return out


class GymExerciseIn(BaseModel):
    name: str
    equipment: str = ""
    primary: list[str] = []
    secondary: list[str] = []
    category: str = "compound"
    pattern: str = ""
    cue: str = ""
    alts: list[str] = []


@app.post("/api/gym/exercises")
def gym_create_exercise(b: GymExerciseIn):
    c = db.get_conn()
    cur = c.execute(
        "INSERT INTO gym_exercises(name, equipment, primary_m, secondary_m, category, pattern, cue, alts, is_custom, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,1,?)",
        (b.name.strip() or "Custom exercise", b.equipment, json.dumps(b.primary), json.dumps(b.secondary),
         b.category, b.pattern, b.cue, json.dumps(b.alts), now_iso()),
    )
    c.commit()
    row = c.execute("SELECT * FROM gym_exercises WHERE id=?", (cur.lastrowid,)).fetchone()
    out = _gym_ex_dict(row)
    c.close()
    return out


@app.get("/api/gym/exercises/{eid}")
def gym_get_exercise(eid: int):
    c = db.get_conn()
    row = c.execute("SELECT * FROM gym_exercises WHERE id=?", (eid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "no such exercise")
    out = _gym_ex_dict(row)
    out["progression"] = gym.exercise_progression(c, eid)
    c.close()
    return out


class GymExercisePatch(BaseModel):
    content: dict = {}


@app.patch("/api/gym/exercises/{eid}")
def gym_patch_exercise(eid: int, b: GymExercisePatch):
    """Edit an exercise's rich content (instructions, mistakes, tips, ROM, grip,
    strength curve, video) — works for built-in and custom exercises."""
    c = db.get_conn()
    row = c.execute("SELECT content FROM gym_exercises WHERE id=?", (eid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "no such exercise")
    cur = json.loads(row["content"] or "{}")
    cur.update(b.content or {})
    c.execute("UPDATE gym_exercises SET content=? WHERE id=?", (json.dumps(cur), eid))
    c.commit()
    out = _gym_ex_dict(c.execute("SELECT * FROM gym_exercises WHERE id=?", (eid,)).fetchone())
    out["progression"] = gym.exercise_progression(c, eid)
    c.close()
    return out


@app.delete("/api/gym/exercises/{eid}")
def gym_delete_exercise(eid: int):
    c = db.get_conn()
    row = c.execute("SELECT is_custom FROM gym_exercises WHERE id=?", (eid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "no such exercise")
    if not row["is_custom"]:
        c.close()
        raise HTTPException(400, "built-in exercises can't be deleted")
    c.execute("DELETE FROM gym_exercises WHERE id=?", (eid,))
    c.commit()
    c.close()
    return {"ok": True}


# ---- templates (routines) --------------------------------------------------
def _gym_template_full(c, tid):
    t = c.execute("SELECT * FROM gym_templates WHERE id=?", (tid,)).fetchone()
    if not t:
        return None
    out = dict(t)
    items = []
    for it in c.execute("SELECT * FROM gym_template_items WHERE template_id=? ORDER BY position, id", (tid,)).fetchall():
        ex = c.execute("SELECT name, equipment FROM gym_exercises WHERE id=?", (it["exercise_id"],)).fetchone()
        d = dict(it)
        d["name"] = ex["name"] if ex else "?"
        d["equipment"] = ex["equipment"] if ex else ""
        items.append(d)
    out["items"] = items
    return out


@app.get("/api/gym/templates")
def gym_list_templates():
    c = db.get_conn()
    out = []
    for t in c.execute("SELECT * FROM gym_templates ORDER BY position, id").fetchall():
        n = c.execute("SELECT COUNT(*) FROM gym_template_items WHERE template_id=?", (t["id"],)).fetchone()[0]
        d = dict(t)
        d["exercise_count"] = n
        out.append(d)
    c.close()
    return out


@app.get("/api/gym/templates/{tid}")
def gym_get_template(tid: int):
    c = db.get_conn()
    out = _gym_template_full(c, tid)
    c.close()
    if not out:
        raise HTTPException(404, "no such template")
    return out


class GymTemplateIn(BaseModel):
    name: str
    notes: str = ""


@app.post("/api/gym/templates")
def gym_create_template(b: GymTemplateIn):
    c = db.get_conn()
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM gym_templates").fetchone()[0]
    cur = c.execute("INSERT INTO gym_templates(name, notes, position, created_at) VALUES (?,?,?,?)",
                    (b.name.strip() or "Routine", b.notes, pos, now_iso()))
    c.commit()
    out = _gym_template_full(c, cur.lastrowid)
    c.close()
    return out


@app.patch("/api/gym/templates/{tid}")
def gym_patch_template(tid: int, b: GymTemplateIn):
    c = db.get_conn()
    c.execute("UPDATE gym_templates SET name=?, notes=? WHERE id=?", (b.name.strip(), b.notes, tid))
    c.commit()
    out = _gym_template_full(c, tid)
    c.close()
    return out


@app.delete("/api/gym/templates/{tid}")
def gym_delete_template(tid: int):
    c = db.get_conn()
    c.execute("DELETE FROM gym_templates WHERE id=?", (tid,))
    c.commit()
    c.close()
    return {"ok": True}


class GymItemIn(BaseModel):
    exercise_id: int
    target_sets: int = 3
    target_reps: str = "8-12"
    target_rir: str = ""
    notes: str = ""


@app.post("/api/gym/templates/{tid}/items")
def gym_add_item(tid: int, b: GymItemIn):
    c = db.get_conn()
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM gym_template_items WHERE template_id=?", (tid,)).fetchone()[0]
    c.execute("INSERT INTO gym_template_items(template_id, exercise_id, position, target_sets, target_reps, target_rir, notes) "
              "VALUES (?,?,?,?,?,?,?)", (tid, b.exercise_id, pos, b.target_sets, b.target_reps, b.target_rir, b.notes))
    c.commit()
    out = _gym_template_full(c, tid)
    c.close()
    return out


class GymItemPatch(BaseModel):
    target_sets: Optional[int] = None
    target_reps: Optional[str] = None
    target_rir: Optional[str] = None
    superset: Optional[int] = None
    notes: Optional[str] = None


@app.patch("/api/gym/template_items/{iid}")
def gym_patch_item(iid: int, b: GymItemPatch):
    c = db.get_conn()
    row = c.execute("SELECT template_id FROM gym_template_items WHERE id=?", (iid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "no such item")
    for k, v in b.model_dump(exclude_none=True).items():
        c.execute(f"UPDATE gym_template_items SET {k}=? WHERE id=?", (v, iid))
    c.commit()
    out = _gym_template_full(c, row["template_id"])
    c.close()
    return out


@app.delete("/api/gym/template_items/{iid}")
def gym_delete_item(iid: int):
    c = db.get_conn()
    row = c.execute("SELECT template_id FROM gym_template_items WHERE id=?", (iid,)).fetchone()
    c.execute("DELETE FROM gym_template_items WHERE id=?", (iid,))
    c.commit()
    out = _gym_template_full(c, row["template_id"]) if row else {"ok": True}
    c.close()
    return out


class GymReorderIn(BaseModel):
    order: list[int]


@app.patch("/api/gym/templates/{tid}/reorder")
def gym_reorder_items(tid: int, b: GymReorderIn):
    c = db.get_conn()
    for pos, iid in enumerate(b.order):
        c.execute("UPDATE gym_template_items SET position=? WHERE id=? AND template_id=?", (pos, iid, tid))
    c.commit()
    c.close()
    return {"ok": True}


@app.post("/api/gym/templates/{tid}/duplicate")
def gym_duplicate_template(tid: int):
    c = db.get_conn()
    t = c.execute("SELECT * FROM gym_templates WHERE id=?", (tid,)).fetchone()
    if not t:
        c.close()
        raise HTTPException(404, "no such template")
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM gym_templates").fetchone()[0]
    cur = c.execute("INSERT INTO gym_templates(name, notes, position, created_at) VALUES (?,?,?,?)",
                    (t["name"] + " (copy)", t["notes"], pos, now_iso()))
    nid = cur.lastrowid
    for it in c.execute("SELECT * FROM gym_template_items WHERE template_id=? ORDER BY position, id", (tid,)).fetchall():
        c.execute("INSERT INTO gym_template_items(template_id, exercise_id, position, target_sets, target_reps, target_rir, superset, notes) "
                  "VALUES (?,?,?,?,?,?,?,?)",
                  (nid, it["exercise_id"], it["position"], it["target_sets"], it["target_reps"], it["target_rir"],
                   it["superset"] if "superset" in it.keys() else None, it["notes"]))
    c.commit()
    out = _gym_template_full(c, nid)
    c.close()
    return out


@app.post("/api/gym/programs/{program_id}/add")
def gym_add_program(program_id: str):
    prog = next((p for p in gym.PROGRAMS if p["id"] == program_id), None)
    if not prog:
        raise HTTPException(404, "unknown program")
    c = db.get_conn()
    created = []
    base = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM gym_templates").fetchone()[0]
    for di, day in enumerate(prog["days"]):
        cur = c.execute("INSERT INTO gym_templates(name, notes, position, created_at) VALUES (?,?,?,?)",
                        (f"{prog['name'].split(' (')[0]} · {day['name']}", prog["summary"], base + di, now_iso()))
        tid = cur.lastrowid
        for pi, it in enumerate(day["items"]):
            ex = c.execute("SELECT id FROM gym_exercises WHERE name=?", (it["exercise"],)).fetchone()
            if ex:
                c.execute("INSERT INTO gym_template_items(template_id, exercise_id, position, target_sets, target_reps, target_rir) "
                          "VALUES (?,?,?,?,?,?)", (tid, ex["id"], pi, it["sets"], it["reps"], it.get("rir", "")))
        created.append(tid)
    c.commit()
    c.close()
    return {"created": created, "count": len(created)}


# ---- sessions (live logging) -----------------------------------------------
class GymSessionIn(BaseModel):
    template_id: Optional[int] = None
    name: Optional[str] = None


@app.post("/api/gym/sessions")
def gym_start_session(b: GymSessionIn):
    c = db.get_conn()
    name = (b.name or "").strip()
    if b.template_id and not name:
        t = c.execute("SELECT name FROM gym_templates WHERE id=?", (b.template_id,)).fetchone()
        name = t["name"] if t else "Workout"
    cur = c.execute("INSERT INTO gym_sessions(name, template_id, started_at) VALUES (?,?,?)",
                    (name or "Workout", b.template_id, now_iso()))
    sid = cur.lastrowid
    # pre-populate from a template so logging is fast
    if b.template_id:
        items = c.execute("SELECT * FROM gym_template_items WHERE template_id=? ORDER BY position, id", (b.template_id,)).fetchall()
        for pi, it in enumerate(items):
            secur = c.execute("INSERT INTO gym_session_exercises(session_id, exercise_id, position, superset) VALUES (?,?,?,?)",
                              (sid, it["exercise_id"], pi, it["superset"] if "superset" in it.keys() else None))
            seid = secur.lastrowid
            for sn in range(1, max(1, it["target_sets"]) + 1):
                c.execute("INSERT INTO gym_sets(se_id, set_no, set_type, done) VALUES (?,?,'working',0)", (seid, sn))
    c.commit()
    out = _gym_session_full(c, sid)
    c.close()
    return out


@app.get("/api/gym/sessions/active")
def gym_active_session():
    c = db.get_conn()
    row = c.execute("SELECT id FROM gym_sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    out = _gym_session_full(c, row["id"]) if row else None
    c.close()
    return out or {}


@app.get("/api/gym/sessions")
def gym_list_sessions(limit: int = 30):
    c = db.get_conn()
    rows = c.execute("SELECT * FROM gym_sessions WHERE ended_at IS NOT NULL ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for s in rows:
        agg = c.execute(
            "SELECT COUNT(*) AS sets, COALESCE(SUM(g.weight*g.reps),0) AS volume "
            "FROM gym_sets g JOIN gym_session_exercises se ON se.id=g.se_id "
            "WHERE se.session_id=? AND g.done=1 AND g.set_type!='warmup'", (s["id"],)).fetchone()
        nex = c.execute("SELECT COUNT(*) FROM gym_session_exercises WHERE session_id=?", (s["id"],)).fetchone()[0]
        d = dict(s)
        d["sets"] = agg["sets"]
        d["volume"] = round(agg["volume"] or 0)
        d["exercise_count"] = nex
        d["duration"] = gym.session_duration(s["started_at"], s["ended_at"])
        out.append(d)
    c.close()
    return out


@app.get("/api/gym/sessions/{sid}")
def gym_get_session(sid: int):
    c = db.get_conn()
    out = _gym_session_full(c, sid)
    c.close()
    if not out:
        raise HTTPException(404, "no such session")
    return out


class GymAddExerciseIn(BaseModel):
    exercise_id: int
    sets: int = 0   # optional empty sets to pre-create


@app.post("/api/gym/sessions/{sid}/exercises")
def gym_session_add_exercise(sid: int, b: GymAddExerciseIn):
    c = db.get_conn()
    if not c.execute("SELECT 1 FROM gym_sessions WHERE id=?", (sid,)).fetchone():
        c.close()
        raise HTTPException(404, "no such session")
    pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM gym_session_exercises WHERE session_id=?", (sid,)).fetchone()[0]
    cur = c.execute("INSERT INTO gym_session_exercises(session_id, exercise_id, position) VALUES (?,?,?)", (sid, b.exercise_id, pos))
    for sn in range(1, b.sets + 1):
        c.execute("INSERT INTO gym_sets(se_id, set_no, set_type, done) VALUES (?,?,'working',0)", (cur.lastrowid, sn))
    c.commit()
    out = _gym_session_full(c, sid)
    c.close()
    return out


class GymSEPatch(BaseModel):
    exercise_id: Optional[int] = None
    notes: Optional[str] = None


@app.patch("/api/gym/session_exercises/{seid}")
def gym_patch_se(seid: int, b: GymSEPatch):
    c = db.get_conn()
    row = c.execute("SELECT session_id, exercise_id FROM gym_session_exercises WHERE id=?", (seid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "no such session exercise")
    if b.exercise_id is not None and b.exercise_id != row["exercise_id"]:
        # record the swap so analytics can surface most-substituted exercises
        c.execute("INSERT INTO gym_swaps(day, from_id, to_id, session_id) VALUES (?,?,?,?)",
                  (today_str(), row["exercise_id"], b.exercise_id, row["session_id"]))
        c.execute("UPDATE gym_session_exercises SET exercise_id=? WHERE id=?", (b.exercise_id, seid))
    if b.notes is not None:
        c.execute("UPDATE gym_session_exercises SET notes=? WHERE id=?", (b.notes, seid))
    c.commit()
    out = _gym_session_full(c, row["session_id"])
    c.close()
    return out


class GymSupersetIn(BaseModel):
    se_ids: list[int] = []


@app.post("/api/gym/sessions/{sid}/superset")
def gym_make_superset(sid: int, b: GymSupersetIn):
    """Group the given session-exercises into a new superset."""
    c = db.get_conn()
    if len(b.se_ids) < 2:
        c.close()
        raise HTTPException(400, "pick at least two exercises to superset")
    gid = c.execute("SELECT COALESCE(MAX(superset),0)+1 FROM gym_session_exercises WHERE session_id=?", (sid,)).fetchone()[0]
    for seid in b.se_ids:
        c.execute("UPDATE gym_session_exercises SET superset=? WHERE id=? AND session_id=?", (gid, seid, sid))
    c.commit()
    out = _gym_session_full(c, sid)
    c.close()
    return out


@app.delete("/api/gym/session_exercises/{seid}/superset")
def gym_clear_superset(seid: int):
    """Ungroup the whole superset this exercise belongs to."""
    c = db.get_conn()
    row = c.execute("SELECT session_id, superset FROM gym_session_exercises WHERE id=?", (seid,)).fetchone()
    if row and row["superset"] is not None:
        c.execute("UPDATE gym_session_exercises SET superset=NULL WHERE session_id=? AND superset=?",
                  (row["session_id"], row["superset"]))
        c.commit()
    out = _gym_session_full(c, row["session_id"]) if row else {"ok": True}
    c.close()
    return out


@app.patch("/api/gym/sessions/{sid}/reorder")
def gym_reorder_session(sid: int, b: GymReorderIn):
    c = db.get_conn()
    for pos, seid in enumerate(b.order):
        c.execute("UPDATE gym_session_exercises SET position=? WHERE id=? AND session_id=?", (pos, seid, sid))
    c.commit()
    out = _gym_session_full(c, sid)
    c.close()
    return out


@app.delete("/api/gym/session_exercises/{seid}")
def gym_session_del_exercise(seid: int):
    c = db.get_conn()
    row = c.execute("SELECT session_id FROM gym_session_exercises WHERE id=?", (seid,)).fetchone()
    c.execute("DELETE FROM gym_session_exercises WHERE id=?", (seid,))
    c.commit()
    out = _gym_session_full(c, row["session_id"]) if row else {"ok": True}
    c.close()
    return out


class GymSetIn(BaseModel):
    weight: Optional[float] = None
    reps: Optional[float] = None
    rpe: Optional[float] = None
    rir: Optional[float] = None
    set_type: str = "working"
    tempo: Optional[str] = None
    duration: Optional[float] = None
    distance: Optional[float] = None


@app.post("/api/gym/session_exercises/{seid}/sets")
def gym_add_set(seid: int, b: GymSetIn):
    c = db.get_conn()
    row = c.execute("SELECT session_id FROM gym_session_exercises WHERE id=?", (seid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "no such session exercise")
    sn = c.execute("SELECT COALESCE(MAX(set_no),0)+1 FROM gym_sets WHERE se_id=?", (seid,)).fetchone()[0]
    c.execute("INSERT INTO gym_sets(se_id, set_no, weight, reps, rpe, rir, set_type, tempo, duration, distance, done) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,0)",
              (seid, sn, b.weight, b.reps, b.rpe, b.rir, b.set_type, b.tempo, b.duration, b.distance))
    c.commit()
    out = _gym_session_full(c, row["session_id"])
    c.close()
    return out


class GymSetPatch(BaseModel):
    weight: Optional[float] = None
    reps: Optional[float] = None
    rpe: Optional[float] = None
    rir: Optional[float] = None
    set_type: Optional[str] = None
    done: Optional[bool] = None
    tempo: Optional[str] = None
    duration: Optional[float] = None
    distance: Optional[float] = None
    failure: Optional[bool] = None
    paused: Optional[bool] = None
    notes: Optional[str] = None


@app.patch("/api/gym/sets/{set_id}")
def gym_patch_set(set_id: int, b: GymSetPatch):
    c = db.get_conn()
    row = c.execute("SELECT se_id FROM gym_sets WHERE id=?", (set_id,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "no such set")
    fields = b.model_dump(exclude_unset=True)
    for k, v in fields.items():
        if k == "done":
            # stamp/clear done_at so rest between sets can be derived
            c.execute("UPDATE gym_sets SET done=?, done_at=? WHERE id=?",
                      (1 if v else 0, now_iso() if v else None, set_id))
        else:
            if k in ("failure", "paused"):
                v = 1 if v else 0
            c.execute(f"UPDATE gym_sets SET {k}=? WHERE id=?", (v, set_id))
    c.commit()
    se = c.execute("SELECT session_id FROM gym_session_exercises WHERE id=?", (row["se_id"],)).fetchone()
    out = _gym_session_full(c, se["session_id"])
    c.close()
    return out


@app.delete("/api/gym/sets/{set_id}")
def gym_delete_set(set_id: int):
    c = db.get_conn()
    row = c.execute("SELECT se_id FROM gym_sets WHERE id=?", (set_id,)).fetchone()
    seid = row["se_id"] if row else None
    c.execute("DELETE FROM gym_sets WHERE id=?", (set_id,))
    c.commit()
    out = {"ok": True}
    if seid:
        se = c.execute("SELECT session_id FROM gym_session_exercises WHERE id=?", (seid,)).fetchone()
        if se:
            out = _gym_session_full(c, se["session_id"])
    c.close()
    return out


@app.post("/api/gym/sessions/{sid}/finish")
def gym_finish_session(sid: int):
    c = db.get_conn()
    c.execute("UPDATE gym_sessions SET ended_at=? WHERE id=? AND ended_at IS NULL", (now_iso(), sid))
    c.commit()
    out = _gym_session_full(c, sid)
    c.close()
    return out


@app.delete("/api/gym/sessions/{sid}")
def gym_delete_session(sid: int):
    c = db.get_conn()
    c.execute("DELETE FROM gym_sessions WHERE id=?", (sid,))
    c.commit()
    c.close()
    return {"ok": True}


# ---- analytics -------------------------------------------------------------
@app.get("/api/gym/analytics/muscles")
def gym_analytics_muscles(days: int = 7):
    c = db.get_conn()
    out = gym.muscle_volume(c, days=days)
    c.close()
    return out


@app.get("/api/gym/analytics/recommendations")
def gym_analytics_recs():
    c = db.get_conn()
    out = gym.recommendations(c)
    c.close()
    return {"recommendations": out}


@app.get("/api/gym/analytics/prs")
def gym_analytics_prs():
    c = db.get_conn()
    out = gym.personal_records(c)
    c.close()
    return {"prs": out}


@app.get("/api/gym/analytics/overview")
def gym_analytics_overview():
    c = db.get_conn()
    total = c.execute("SELECT COUNT(*) FROM gym_sessions WHERE ended_at IS NOT NULL").fetchone()[0]
    week = c.execute("SELECT COUNT(*) FROM gym_sessions WHERE ended_at IS NOT NULL AND substr(started_at,1,10) >= ?",
                     ((date.today() - timedelta(days=6)).isoformat(),)).fetchone()[0]
    vol = c.execute(
        "SELECT COALESCE(SUM(g.weight*g.reps),0) AS v FROM gym_sets g "
        "JOIN gym_session_exercises se ON se.id=g.se_id JOIN gym_sessions s ON s.id=se.session_id "
        "WHERE g.done=1 AND g.set_type!='warmup' AND substr(s.started_at,1,10) >= ?",
        ((date.today() - timedelta(days=6)).isoformat(),)).fetchone()["v"]
    durs = [gym.session_duration(r["started_at"], r["ended_at"])
            for r in c.execute("SELECT started_at, ended_at FROM gym_sessions WHERE ended_at IS NOT NULL").fetchall()]
    durs = [d for d in durs if d is not None]
    c.close()
    return {"total_sessions": total, "sessions_this_week": week, "volume_this_week": round(vol or 0),
            "avg_duration": round(sum(durs) / len(durs)) if durs else 0}


# ---- goals -----------------------------------------------------------------
def _gym_goals_payload(c):
    out = gym.goals(c)
    changed = False
    for g in out:
        if g["achieved"] and not g["achieved_at"]:
            c.execute("UPDATE gym_goals SET achieved_at=? WHERE id=? AND achieved_at IS NULL", (now_iso(), g["id"]))
            changed = True
    if changed:
        c.commit()
        out = gym.goals(c)
    return {"goals": out}


@app.get("/api/gym/goals")
def gym_list_goals():
    c = db.get_conn()
    out = _gym_goals_payload(c)
    c.close()
    return out


class GymGoalIn(BaseModel):
    name: str = ""
    kind: str = "custom"          # lift | volume | frequency | custom
    exercise_id: Optional[int] = None
    target: float = 0
    unit: str = ""
    current: float = 0            # custom baseline / current value


@app.post("/api/gym/goals")
def gym_create_goal(b: GymGoalIn):
    c = db.get_conn()
    start = gym.goal_current(c, b.kind, b.exercise_id, b.current)
    c.execute("INSERT INTO gym_goals(name, kind, exercise_id, target, unit, start_value, current, created_at) "
              "VALUES (?,?,?,?,?,?,?,?)",
              (b.name.strip(), b.kind, b.exercise_id, b.target, b.unit, start, b.current, now_iso()))
    c.commit()
    out = _gym_goals_payload(c)
    c.close()
    return out


class GymGoalPatch(BaseModel):
    name: Optional[str] = None
    target: Optional[float] = None
    current: Optional[float] = None
    unit: Optional[str] = None


@app.patch("/api/gym/goals/{gid}")
def gym_patch_goal(gid: int, b: GymGoalPatch):
    c = db.get_conn()
    for k, v in b.model_dump(exclude_none=True).items():
        c.execute(f"UPDATE gym_goals SET {k}=? WHERE id=?", (v, gid))
    c.commit()
    out = _gym_goals_payload(c)
    c.close()
    return out


@app.delete("/api/gym/goals/{gid}")
def gym_delete_goal(gid: int):
    c = db.get_conn()
    c.execute("DELETE FROM gym_goals WHERE id=?", (gid,))
    c.commit()
    c.close()
    return {"ok": True}


@app.get("/api/gym/analytics/calendar")
def gym_calendar(days: int = 90):
    c = db.get_conn()
    out = gym.calendar(c, days=days)
    c.close()
    return out


@app.get("/api/gym/analytics/trends")
def gym_trends(weeks: int = 8):
    c = db.get_conn()
    out = gym.trends(c, weeks=weeks)
    c.close()
    return out


@app.get("/api/gym/analytics/insights")
def gym_insights():
    c = db.get_conn()
    out = gym.insights(c)
    c.close()
    return out


@app.get("/api/gym/analytics/pr-timeline")
def gym_pr_timeline():
    c = db.get_conn()
    out = gym.pr_timeline(c)
    c.close()
    return {"events": out}


# ---- body metrics ----------------------------------------------------------
@app.get("/api/gym/metrics")
def gym_list_metrics():
    c = db.get_conn()
    out = gym.metrics(c)
    c.close()
    return {"metrics": out}


class GymMetricIn(BaseModel):
    metric: str
    value: float
    unit: str = ""
    day: Optional[str] = None


@app.post("/api/gym/metrics")
def gym_log_metric(b: GymMetricIn):
    day = b.day or today_str()
    c = db.get_conn()
    c.execute("DELETE FROM gym_metrics WHERE metric=? AND day=?", (b.metric.strip(), day))  # one value per day
    c.execute("INSERT INTO gym_metrics(day, metric, value, unit, created_at) VALUES (?,?,?,?,?)",
              (day, b.metric.strip() or "metric", b.value, b.unit, now_iso()))
    c.commit()
    out = gym.metrics(c)
    c.close()
    return {"metrics": out}


@app.delete("/api/gym/metrics/{metric}/{day}")
def gym_delete_metric(metric: str, day: str):
    c = db.get_conn()
    c.execute("DELETE FROM gym_metrics WHERE metric=? AND day=?", (metric, day))
    c.commit()
    out = gym.metrics(c)
    c.close()
    return {"metrics": out}


# ---- tab visibility (hide built-in tabs from the bar; synced across devices)
@app.get("/api/settings/tabs")
def get_tab_visibility():
    c = db.get_conn()
    raw = db.get_setting(c, "hidden_tabs", "[]")
    c.close()
    try:
        hidden = json.loads(raw)
        if not isinstance(hidden, list):
            hidden = []
    except (ValueError, TypeError):
        hidden = []
    return {"hidden": hidden}


class TabVisibilityIn(BaseModel):
    hidden: list[str] = []


@app.put("/api/settings/tabs")
def set_tab_visibility(b: TabVisibilityIn):
    hidden = [t for t in b.hidden if isinstance(t, str)]
    c = db.get_conn()
    db.set_setting(c, "hidden_tabs", json.dumps(hidden))
    c.commit()
    c.close()
    return {"hidden": hidden}


# ---- today-tab section visibility (which aggregated cards to show) ----------
@app.get("/api/settings/today")
def get_today_hidden():
    c = db.get_conn()
    raw = db.get_setting(c, "today_hidden", "[]")
    c.close()
    try:
        hidden = json.loads(raw)
        if not isinstance(hidden, list):
            hidden = []
    except (ValueError, TypeError):
        hidden = []
    return {"hidden": hidden}


class TodayHiddenIn(BaseModel):
    hidden: list[str] = []


@app.put("/api/settings/today")
def set_today_hidden(b: TodayHiddenIn):
    hidden = [t for t in b.hidden if isinstance(t, str)]
    c = db.get_conn()
    db.set_setting(c, "today_hidden", json.dumps(hidden))
    c.commit()
    c.close()
    return {"hidden": hidden}


# ---- appearance (accent color, synced across devices) ---------------------
@app.get("/api/appearance")
def get_appearance():
    c = db.get_conn()
    accent = db.get_setting(c, "accent", "")
    theme = db.get_setting(c, "theme", "")
    c.close()
    return {"accent": accent, "theme": theme}


class AppearanceIn(BaseModel):
    accent: str = ""              # CSS color, or "" to fall back to the theme accent
    theme: Optional[str] = None   # theme id; None leaves it unchanged


@app.put("/api/appearance")
def set_appearance(a: AppearanceIn):
    c = db.get_conn()
    db.set_setting(c, "accent", a.accent.strip())
    if a.theme is not None:
        db.set_setting(c, "theme", a.theme.strip())
    c.commit()
    theme = db.get_setting(c, "theme", "")
    c.close()
    return {"ok": True, "accent": a.accent.strip(), "theme": theme}


# ----------------------------------------------------------------------------
# Static frontend (mounted last so /api wins)
# ----------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
