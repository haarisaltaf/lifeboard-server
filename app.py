"""lifeboard — a self-hosted personal dashboard + second brain.

Runs as a single FastAPI process backed by one SQLite file.
No auth (designed to sit behind Tailscale on a private tailnet).
"""
import json
import io
import csv
import re
import glob
from datetime import datetime, date
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
import asyncio

app = FastAPI(title="lifeboard")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.on_event("startup")
def _startup():
    db.init_db()


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
    # aggregate activity: a day "counts" if any habit was done that day
    habit_ids = [r["id"] for r in c.execute("SELECT id FROM widgets WHERE type='habit'").fetchall()]
    activity = {}
    if habit_ids:
        q = "SELECT day, COUNT(*) n FROM logs WHERE value>0 AND widget_id IN (%s) GROUP BY day" % (
            ",".join("?" * len(habit_ids)))
        for r in c.execute(q, habit_ids).fetchall():
            activity[r["day"]] = r["n"]
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


def _kaizen_upsert_day(c, day, *, highlight=None, highlight_done=None, braindump=None):
    """Upsert a kaizen_days row, touching only the provided fields."""
    row = c.execute("SELECT highlight, highlight_done, braindump FROM kaizen_days WHERE day=?", (day,)).fetchone()
    h = row["highlight"] if row else ""
    hd = row["highlight_done"] if row else 0
    bd = row["braindump"] if row else ""
    if highlight is not None:
        h = highlight.strip()
    if highlight_done is not None:
        hd = 1 if highlight_done else 0
    if braindump is not None:
        bd = braindump
    c.execute(
        "INSERT INTO kaizen_days(day, highlight, highlight_done, braindump, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(day) DO UPDATE SET highlight=excluded.highlight, "
        "highlight_done=excluded.highlight_done, braindump=excluded.braindump, updated_at=excluded.updated_at",
        (day, h, hd, bd, now_iso()),
    )


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
    _kaizen_upsert_day(c, b.day or today_str(), braindump=b.body)
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


# ---- appearance (accent color, synced across devices) ---------------------
@app.get("/api/appearance")
def get_appearance():
    c = db.get_conn()
    accent = db.get_setting(c, "accent", "")
    c.close()
    return {"accent": accent}


class AppearanceIn(BaseModel):
    accent: str = ""   # CSS color, or "" to fall back to the theme default


@app.put("/api/appearance")
def set_appearance(a: AppearanceIn):
    c = db.get_conn()
    db.set_setting(c, "accent", a.accent.strip())
    c.commit()
    c.close()
    return {"ok": True, "accent": a.accent.strip()}


# ----------------------------------------------------------------------------
# Static frontend (mounted last so /api wins)
# ----------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
