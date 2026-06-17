"""v2 helpers: goal templates, review/summary stats, and keyword related-notes.

All pure-ish stdlib code (no extra dependencies). Related-notes uses a small
TF-IDF + cosine over the entries store — good enough until embeddings land in v3.
"""
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta


# ----------------------------------------------------------------------------
# Goal templates — pre-built tabs you can spin up in one click
# ----------------------------------------------------------------------------
TEMPLATES = [
    {
        "id": "essentials",
        "name": "Daily Essentials",
        "desc": "Core habits + water + a daily checklist.",
        "widgets": [
            {"type": "habit", "title": "Wake up early"},
            {"type": "habit", "title": "Move 30 min"},
            {"type": "counter", "title": "Water", "config": {"daily_target": 8, "unit": "glasses"}},
            {"type": "todo", "title": "Top 3 today"},
        ],
    },
    {
        "id": "cut",
        "name": "Cut / Weight loss",
        "desc": "Weight trend to a target, calories, training streak.",
        "widgets": [
            {"type": "progress", "title": "Reach target weight",
             "config": {"goal_mode": "metric", "start_value": 85, "target": 78, "unit": "kg"}},
            {"type": "number", "title": "Calories", "config": {"unit": "kcal"}},
            {"type": "habit", "title": "Train"},
            {"type": "counter", "title": "Steps (k)", "config": {"daily_target": 10, "unit": "k"}},
        ],
    },
    {
        "id": "run",
        "name": "Run a distance",
        "desc": "Cumulative mileage to a goal by a date + run streak.",
        "widgets": [
            {"type": "progress", "title": "Distance goal",
             "config": {"goal_mode": "cumulative", "target": 200, "unit": "km"}},
            {"type": "habit", "title": "Run"},
            {"type": "number", "title": "Resting HR", "config": {"unit": "bpm"}},
        ],
    },
    {
        "id": "strength",
        "name": "Strength",
        "desc": "Lift streak, bodyweight, protein counter.",
        "widgets": [
            {"type": "habit", "title": "Lift"},
            {"type": "number", "title": "Bodyweight", "config": {"unit": "kg"}},
            {"type": "counter", "title": "Protein", "config": {"daily_target": 160, "unit": "g"}},
            {"type": "timer", "title": "Session length"},
        ],
    },
    {
        "id": "study",
        "name": "Study sprint",
        "desc": "Deep-work hours, reading streak, syllabus checklist.",
        "widgets": [
            {"type": "counter", "title": "Deep work", "config": {"daily_target": 4, "unit": "hrs"}},
            {"type": "habit", "title": "Read"},
            {"type": "timer", "title": "Focus timer"},
            {"type": "todo", "title": "Syllabus"},
        ],
    },
    {
        "id": "build",
        "name": "Ship a project",
        "desc": "Build streak, hours toward a goal, task list.",
        "widgets": [
            {"type": "habit", "title": "Ship something"},
            {"type": "progress", "title": "Hours to launch",
             "config": {"goal_mode": "cumulative", "target": 100, "unit": "hrs"}},
            {"type": "todo", "title": "Backlog"},
        ],
    },
]


def template_by_id(tid):
    return next((t for t in TEMPLATES if t["id"] == tid), None)


# ----------------------------------------------------------------------------
# Review / summary stats over a period
# ----------------------------------------------------------------------------
def _days_in_period(period):
    return 7 if period == "week" else (30 if period == "month" else 365)


def review(conn, period="week"):
    """Per-habit completion + counter/number averages over the last N days,
    plus an overall completion rate. Used by the review view and trend charts."""
    days = _days_in_period(period)
    today = date.today()
    start = today - timedelta(days=days - 1)
    start_s = start.isoformat()

    widgets = conn.execute(
        "SELECT w.*, t.name AS tab_name FROM widgets w JOIN tabs t ON t.id=w.tab_id "
        "WHERE w.type IN ('habit','counter','number','progress','timer') "
        "ORDER BY t.position, w.position"
    ).fetchall()

    items = []
    habit_done = habit_slots = 0
    for w in widgets:
        cfg = json.loads(w["config"] or "{}")
        logs = conn.execute(
            "SELECT day, value FROM logs WHERE widget_id=? AND day>=? ORDER BY day",
            (w["id"], start_s)).fetchall()
        series = [{"day": r["day"], "value": r["value"]} for r in logs]
        entry = {"id": w["id"], "type": w["type"], "title": w["title"],
                 "tab": w["tab_name"], "unit": cfg.get("unit", ""), "series": series}
        if w["type"] == "habit":
            done = sum(1 for r in logs if r["value"])
            entry["done"] = done
            entry["rate"] = round(done / days * 100)
            habit_done += done
            habit_slots += days
        elif w["type"] in ("counter", "number", "timer", "progress"):
            vals = [r["value"] for r in logs]
            entry["avg"] = round(sum(vals) / len(vals), 2) if vals else 0
            entry["total"] = round(sum(vals), 2)
            entry["best"] = max(vals) if vals else 0
        items.append(entry)

    # daily overall completion: fraction of habits done each day
    habit_ids = [w["id"] for w in widgets if w["type"] == "habit"]
    daily = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        if habit_ids:
            q = "SELECT COUNT(*) n FROM logs WHERE day=? AND value>0 AND widget_id IN (%s)" % (
                ",".join("?" * len(habit_ids)))
            n = conn.execute(q, [d, *habit_ids]).fetchone()["n"]
            daily.append({"day": d, "rate": round(n / len(habit_ids) * 100)})
        else:
            daily.append({"day": d, "rate": 0})

    return {
        "period": period, "days": days,
        "start": start_s, "end": today.isoformat(),
        "overall_rate": round(habit_done / habit_slots * 100) if habit_slots else 0,
        "habit_completions": habit_done,
        "items": items,
        "daily": daily,
    }


# ----------------------------------------------------------------------------
# Related notes — TF-IDF + cosine similarity
# ----------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9']+")
_STOP = set("the a an and or but if then this that these those is are was were be been being "
            "to of in on at for with as by from it its i you he she they we me my your our their "
            "have has had do does did not no so up out about into over after before just can will "
            "would should could there here what when where which who how than too very also more most "
            "some any all one two".split())


def _tokens(text):
    return [t for t in _WORD.findall((text or "").lower()) if t not in _STOP and len(t) > 2]


def related(conn, entry_id, limit=5):
    rows = conn.execute("SELECT id, kind, title, body FROM entries").fetchall()
    docs = {r["id"]: _tokens((r["title"] or "") + " " + (r["body"] or "")) for r in rows}
    meta = {r["id"]: {"id": r["id"], "kind": r["kind"], "title": r["title"]} for r in rows}
    if entry_id not in docs or len(docs) < 2:
        return []

    # idf
    n = len(docs)
    df = Counter()
    for toks in docs.values():
        for t in set(toks):
            df[t] += 1
    idf = {t: math.log(n / (1 + c)) + 1 for t, c in df.items()}

    def vec(toks):
        tf = Counter(toks)
        v = {t: (tf[t] / len(toks)) * idf.get(t, 0) for t in tf} if toks else {}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return v, norm

    base_v, base_n = vec(docs[entry_id])
    scored = []
    for oid, toks in docs.items():
        if oid == entry_id:
            continue
        v, nrm = vec(toks)
        # dot over the smaller dict
        small, big = (base_v, v) if len(base_v) < len(v) else (v, base_v)
        dot = sum(val * big.get(t, 0) for t, val in small.items())
        sim = dot / (base_n * nrm)
        if sim > 0.02:
            scored.append((sim, oid))
    scored.sort(reverse=True)
    out = []
    for sim, oid in scored[:limit]:
        m = dict(meta[oid]); m["score"] = round(sim, 3)
        out.append(m)
    return out
