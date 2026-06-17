import os, shutil
os.environ["LIFEBOARD_DATA"] = "/tmp/lbtest25"
shutil.rmtree("/tmp/lbtest25", ignore_errors=True)
from datetime import date, timedelta
import pace
from fastapi.testclient import TestClient
import app as A
A.db.init_db()
c = TestClient(A.app)

def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + ("  " + str(extra) if extra else ""))
    assert cond, label

# --- pace milestones: cumulative ---
cfg = {"goal_mode": "cumulative", "target": 100, "start_date": (date.today()-timedelta(days=10)).isoformat(),
       "milestones": [{"label": "quarter", "at": 25}, {"label": "half", "at": 50}, {"label": "three-q", "at": 75}]}
logs = [{"day": (date.today()-timedelta(days=i)).isoformat(), "value": 4} for i in range(10)]  # current ~40
p = pace.pace(cfg, logs)
ms = p["milestones"]
ok("cumulative milestones count", len(ms) == 3)
ok("quarter reached, half not", ms[0]["reached"] and not ms[1]["reached"], [(m["label"], m["reached"]) for m in ms])
nxt = [m for m in ms if m.get("next")]
ok("next milestone is half with eta", nxt and nxt[0]["label"] == "half" and "eta" in nxt[0], nxt)
ok("milestone positions sorted", [m["pos"] for m in ms] == sorted(m["pos"] for m in ms))

# --- pace milestones: metric (weight down) ---
cfgm = {"goal_mode": "metric", "start_value": 85, "target": 78, "unit": "kg",
        "start_date": (date.today()-timedelta(days=20)).isoformat(),
        "milestones": [{"label": "82", "at": 82}, {"label": "80", "at": 80}]}
logsm = [{"day": (date.today()-timedelta(days=20)).isoformat(), "value": 85},
         {"day": (date.today()-timedelta(days=1)).isoformat(), "value": 81.5}]
pm = pace.pace(cfgm, logsm)
msm = pm["milestones"]
ok("metric: 82 reached (passed going down)", msm[0]["reached"], [(m["label"], m["reached"]) for m in msm])
ok("metric: 80 not yet, is next", (not msm[1]["reached"]) and msm[1].get("next"), msm[1])

# --- via API: widget carries milestones in pace ---
tid = c.post("/api/tabs", json={"name": "Run"}).json()["id"]
w = c.post("/api/widgets", json={"tab_id": tid, "type": "progress", "title": "100km",
     "config": {"goal_mode": "cumulative", "target": 100, "unit": "km",
                "milestones": [{"label": "halfway", "at": 50}]}}).json()
c.put(f"/api/widgets/{w['id']}/log", json={"value": 30})
ww = c.get(f"/api/tabs/{tid}/widgets").json()[0]
ok("widget exposes milestones", ww["pace"]["milestones"][0]["label"] == "halfway" and not ww["pace"]["milestones"][0]["reached"])

# --- appearance endpoint ---
ok("appearance default empty", c.get("/api/appearance").json()["accent"] == "")
c.put("/api/appearance", json={"accent": "#6cb6ff"})
ok("appearance persists", c.get("/api/appearance").json()["accent"] == "#6cb6ff")
c.put("/api/appearance", json={"accent": ""})
ok("appearance reset", c.get("/api/appearance").json()["accent"] == "")

print("\nALL V2.5 TESTS PASSED")
