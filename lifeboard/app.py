"""lifeboard — a self-hosted personal dashboard + second brain.

Runs as a single FastAPI process backed by one SQLite file.
No auth (designed to sit behind Tailscale on a private tailnet).
"""
import json
import io
import csv
import re
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import (
    JSONResponse, HTMLResponse, FileResponse, StreamingResponse, PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

import db
import pace

app = FastAPI(title="lifeboard")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.on_event("startup")
def _startup():
    db.init_db()


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


VALID_TYPES = {"habit", "counter", "number", "progress", "todo", "note", "timer"}


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


@app.post("/api/prompts")
def add_prompt(p: PromptIn):
    c = db.get_conn()
    cur = c.execute("INSERT INTO journal_prompts(text,slot,active) VALUES (?,?,?)",
                    (p.text.strip(), p.slot, 1 if p.active else 0))
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
    """Return today's journal entry for the slot (if any) + a prompt to use."""
    day = today_str()
    c = db.get_conn()
    row = c.execute("SELECT * FROM entries WHERE kind='journal' AND entry_date=? AND slot=?",
                    (day, slot)).fetchone()
    prompts = c.execute(
        "SELECT * FROM journal_prompts WHERE active=1 AND slot IN (?, 'any')", (slot,)
    ).fetchall()
    c.close()
    # deterministic prompt-of-the-day so it doesn't reshuffle on refresh
    chosen = None
    if prompts:
        idx = (date.today().toordinal() + (0 if slot == "am" else 1)) % len(prompts)
        chosen = dict(prompts[idx])
    return {"day": day, "slot": slot, "entry": dict(row) if row else None, "prompt": chosen}


# ----------------------------------------------------------------------------
# Backup / export
# ----------------------------------------------------------------------------
@app.get("/api/export/json")
def export_json():
    c = db.get_conn()
    data = {"exported_at": now_iso(), "tables": {}}
    for tbl in ("tabs", "widgets", "logs", "todos", "entries", "journal_prompts", "settings"):
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


# ----------------------------------------------------------------------------
# Static frontend (mounted last so /api wins)
# ----------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
