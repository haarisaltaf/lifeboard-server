"""Pace engine + streak math.

Goal shapes supported in v1:
  - cumulative : you add amounts that sum toward a target (run 500km, save 5000)
  - metric     : you log an absolute value heading to a target (weight 80 -> 75)
  - frequency  : a habit done N times per week (handled on the habit widget)

Everything here is pure functions over (config, logs) so it's easy to test.
"""
from datetime import date, datetime, timedelta


def _today():
    return date.today()


def _parse(d):
    if not d:
        return None
    return datetime.strptime(d, "%Y-%m-%d").date()


def streaks(days_done):
    """days_done: set/list of 'YYYY-MM-DD' strings that count as completed.
    Returns (current_streak, longest_streak). Current counts back from today;
    a gap only today (not yet logged) is forgiven by checking yesterday too."""
    done = {(_parse(d) if isinstance(d, str) else d) for d in days_done}
    if not done:
        return 0, 0

    # longest
    longest = 0
    run = 0
    prev = None
    for d in sorted(done):
        if prev is not None and (d - prev).days == 1:
            run += 1
        else:
            run = 1
        prev = d
        longest = max(longest, run)

    # current: count consecutive days ending today or yesterday
    today = _today()
    anchor = today if today in done else (today - timedelta(days=1))
    current = 0
    cur = anchor
    while cur in done:
        current += 1
        cur -= timedelta(days=1)
    if today not in done and anchor not in done:
        current = 0
    return current, longest


def pace(config, logs):
    """logs: list of {'day': 'YYYY-MM-DD', 'value': float} for this widget.
    Returns a dict describing progress + the daily rate needed to stay on track.
    """
    mode = config.get("goal_mode", "cumulative")
    target = float(config.get("target", 0) or 0)
    baseline = float(config.get("start_value", 0) or 0)
    start = _parse(config.get("start_date")) or _today()
    end = _parse(config.get("end_date"))
    unit = config.get("unit", "")
    today = _today()

    values = [(_parse(l["day"]), float(l["value"])) for l in logs if l.get("day")]
    values.sort()

    out = {
        "mode": mode, "target": target, "unit": unit,
        "start_date": start.isoformat(),
        "end_date": end.isoformat() if end else None,
        "days_left": (end - today).days if end else None,
    }

    if mode == "metric":
        current = values[-1][1] if values else baseline
        direction = "down" if target < baseline else "up"
        total_span = abs(target - baseline) or 1
        done_span = abs(current - baseline)
        pct = max(0.0, min(1.0, done_span / total_span))
        remaining = target - current  # signed
        out.update(current=current, baseline=baseline, direction=direction,
                   percent=round(pct * 100, 1), remaining=round(remaining, 3))
        if end and today <= end:
            days_left = max(1, (end - today).days)
            out["required_per_day"] = round(remaining / days_left, 3)
        # projection from observed rate of change
        if len(values) >= 2:
            d0, v0 = values[0]
            d1, v1 = values[-1]
            span_days = max(1, (d1 - d0).days)
            rate = (v1 - v0) / span_days  # per day
            out["observed_per_day"] = round(rate, 3)
            if rate != 0 and ((target - current) / rate) > 0:
                eta = today + timedelta(days=(target - current) / rate)
                out["projected_date"] = eta.isoformat()
        out["status"] = _status_metric(out)
        return out

    # cumulative (and a sane default)
    current = baseline + sum(v for _, v in values)
    remaining = max(0.0, target - current)
    pct = 0.0 if target == 0 else max(0.0, min(1.0, current / target))
    out.update(current=round(current, 3), percent=round(pct * 100, 1),
               remaining=round(remaining, 3))
    if end and today <= end:
        days_left = max(1, (end - today).days)
        out["required_per_day"] = round(remaining / days_left, 3)
    # observed average per active day for projection
    if values:
        first_day = values[0][0]
        elapsed = max(1, (today - first_day).days + 1)
        avg = (current - baseline) / elapsed
        out["observed_per_day"] = round(avg, 3)
        if avg > 0 and remaining > 0:
            eta = today + timedelta(days=remaining / avg)
            out["projected_date"] = eta.isoformat()
    out["status"] = _status_cumulative(out)
    return out


def _status_cumulative(o):
    if o.get("remaining", 1) <= 0:
        return "done"
    req = o.get("required_per_day")
    obs = o.get("observed_per_day")
    if req is None:
        return "open"            # no end date: just projecting
    if obs is None:
        return "behind" if req > 0 else "ahead"
    return "ahead" if obs >= req else "behind"


def _status_metric(o):
    if o.get("direction") == "down":
        if o.get("current", 0) <= o.get("target", 0):
            return "done"
    else:
        if o.get("current", 0) >= o.get("target", 0):
            return "done"
    req = o.get("required_per_day")
    obs = o.get("observed_per_day")
    if req is None:
        return "open"
    if obs is None:
        return "behind"
    # moving in the right direction fast enough?
    if o.get("direction") == "down":
        return "ahead" if obs <= req else "behind"
    return "ahead" if obs >= req else "behind"
