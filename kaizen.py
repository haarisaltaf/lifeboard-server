"""Kaizen "light mode" — the daily ritual + a weekly reflection.

Three small practices, in the spirit of kaizen (改善, "change for good"):
  1. a single **daily highlight** — the one thing that makes today a win,
  2. one or more 2-minute **micro-commitments** — just show up,
  3. a **brain dump** — empty the mental buffer.

Streaks reuse lifeboard's forgiving `pace.streaks` (today isn't a miss until it's
over, and yesterday still counts) — which is itself the kaizen "allow mistakes"
principle, so micro-commitments never punish a single slip.
"""
from __future__ import annotations

from datetime import date, timedelta

import pace


def _commitment_days(conn, cid):
    return [r["day"] for r in conn.execute(
        "SELECT day FROM kaizen_logs WHERE commitment_id=? AND done=1", (cid,)).fetchall()]


def state(conn, day):
    """Full kaizen state for `day` (the daily ritual + this-week reflection)."""
    row = conn.execute(
        "SELECT highlight, highlight_done, braindump FROM kaizen_days WHERE day=?", (day,)).fetchone()
    highlight = {"text": row["highlight"] if row else "", "done": bool(row["highlight_done"]) if row else False}
    braindump = row["braindump"] if row else ""

    commitments = []
    for r in conn.execute(
            "SELECT * FROM kaizen_commitments WHERE archived=0 ORDER BY position, id").fetchall():
        days_done = _commitment_days(conn, r["id"])
        cur, longest = pace.streaks(days_done)
        commitments.append({
            "id": r["id"], "text": r["text"],
            "today_done": day in days_done,
            "streak": {"current": cur, "longest": longest, "total": len(days_done)},
        })

    return {
        "day": day,
        "highlight": highlight,
        "braindump": braindump,
        "commitments": commitments,
        "week": _week(conn, day, commitments),
    }


def _week(conn, day, commitments):
    today = date.fromisoformat(day)
    start = today - timedelta(days=6)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]

    rows = {r["day"]: r for r in conn.execute(
        "SELECT day, highlight, highlight_done FROM kaizen_days WHERE day>=? AND day<=?",
        (days[0], days[-1])).fetchall()}
    highlight_days = [d for d in days if d in rows and rows[d]["highlight"] and rows[d]["highlight_done"]]

    total = done = 0
    for c in commitments:
        ddays = set(_commitment_days(conn, c["id"]))
        for d in days:
            total += 1
            if d in ddays:
                done += 1
    commit_rate = round(done / total * 100) if total else 0

    best = max((c["streak"]["current"] for c in commitments), default=0)
    return {
        "days": days,
        "highlight_days": highlight_days,
        "highlights_hit": len(highlight_days),
        "commit_rate": commit_rate,
        "nudge": _nudge(len(highlight_days), commit_rate, best, bool(commitments)),
    }


def _nudge(highlights_hit, commit_rate, best_streak, has_commitments):
    """A friendly, non-judgmental narrative line — kaizen rewards showing up."""
    if highlights_hit >= 6:
        return f"{highlights_hit}/7 highlights this week — serious momentum. Keep it tiny and keep it daily."
    if highlights_hit >= 3:
        return f"{highlights_hit}/7 highlights hit. Small wins are stacking up — that's exactly how it compounds."
    if best_streak >= 3:
        return f"Highlights were light this week, but a {best_streak}-day micro-streak says you're still in it."
    if commit_rate >= 50 and has_commitments:
        return "Showing up more days than not. Pick one clear highlight tomorrow and ride it."
    return "Fresh slate. Choose one tiny win for today — showing up is the whole game."
