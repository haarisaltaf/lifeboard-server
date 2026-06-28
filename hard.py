"""75 Hard challenge logic — all-or-nothing daily completion with auto-reset.

A day "passes" only when every configured task is checked (and, if required, a
progress photo is logged). Miss a day and the run resets to day 1. This is all
*derived on read* from the per-day records, so there's no background reset job:
the day counter and streak always reflect the logs, and retroactively filling a
missed day automatically heals the streak.
"""
from datetime import date, timedelta


DEFAULT_TASKS = [
    {"key": "diet", "label": "Follow the diet — no cheats, no alcohol"},
    {"key": "workout1", "label": "45-min workout"},
    {"key": "workout2", "label": "45-min workout (outdoors)"},
    {"key": "water", "label": "Drink a gallon of water"},
    {"key": "read", "label": "Read 10 pages (non-fiction)"},
]


def _d(s):
    return date.fromisoformat(s)


def compute(cfg, records, today_s):
    """Derive the full 75 Hard state.

    cfg: the widget config (start_date, duration, require_photo, tasks).
    records: {day: {"tasks": {key: bool}, "photo": path|None}}.
    today_s: 'YYYY-MM-DD' (local today).
    """
    tasks = cfg.get("tasks") or DEFAULT_TASKS
    require_photo = bool(cfg.get("require_photo", True))
    duration = int(cfg.get("duration") or 75)
    start_s = cfg.get("start_date") or None

    state = {
        "tasks": tasks,
        "require_photo": require_photo,
        "duration": duration,
        "start_date": start_s,
        "today": today_s,
        "started": bool(start_s),
    }
    if not start_s:
        state.update(day=0, complete_count=0, won=False, today_complete=False,
                     run_start=None, last_fail=None, resets=0, grid=[])
        return state

    start = _d(start_s)
    today = _d(today_s)

    def fraction(day_s):
        rec = records.get(day_s) or {}
        td = rec.get("tasks") or {}
        need = len(tasks) + (1 if require_photo else 0)
        have = sum(1 for t in tasks if td.get(t["key"]))
        if require_photo and rec.get("photo"):
            have += 1
        return have, need

    def complete(day_s):
        have, need = fraction(day_s)
        return need > 0 and have >= need

    # Walk every past day (today can't fail yet). Any incomplete past day breaks
    # the run and restarts it the following day.
    run_start = start if start <= today else today
    last_fail = None
    resets = 0
    d = start
    while d < today:
        if not complete(d.isoformat()):
            last_fail = d
            run_start = d + timedelta(days=1)
            resets += 1
        d += timedelta(days=1)

    day_num = (today - run_start).days + 1 if today >= run_start else 0
    complete_count = 0
    dd = run_start
    while dd <= today:
        if complete(dd.isoformat()):
            complete_count += 1
        dd += timedelta(days=1)

    won = complete_count >= duration
    today_complete = complete(today_s)

    # Per-day grid for the current run (day 1 .. duration), heatmap-style.
    grid = []
    for n in range(1, duration + 1):
        cd = run_start + timedelta(days=n - 1)
        cds = cd.isoformat()
        have, need = fraction(cds)
        if cd > today:
            status = "upcoming"
        elif complete(cds):
            status = "done"
        elif cd == today:
            status = "today"
        else:
            status = "fail"
        grid.append({"n": n, "day": cds, "status": status, "have": have, "need": need})

    state.update(
        day=min(day_num, duration), raw_day=day_num,
        complete_count=complete_count, won=won,
        today_complete=today_complete,
        run_start=run_start.isoformat(),
        last_fail=last_fail.isoformat() if last_fail else None,
        resets=resets, grid=grid,
    )
    return state
