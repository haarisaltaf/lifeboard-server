import os, shutil
os.environ["LIFEBOARD_DATA"] = "/tmp/lbtest"
shutil.rmtree("/tmp/lbtest", ignore_errors=True)
from fastapi.testclient import TestClient
import app as A
A.db.init_db()
c = TestClient(A.app)

def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + ("  " + str(extra) if extra else ""))
    assert cond, label

# health
ok("health", c.get("/api/health").json()["ok"])

# tab
tid = c.post("/api/tabs", json={"name": "Gym"}).json()["id"]
ok("create tab", isinstance(tid, int))

# habit
h = c.post("/api/widgets", json={"tab_id": tid, "type": "habit", "title": "Workout"}).json()
hid = h["id"]
w = c.put(f"/api/widgets/{hid}/log", json={"value": 1}).json()
ok("habit log -> streak", w["streak"]["current"] == 1, w["streak"])

# counter with target
cw = c.post("/api/widgets", json={"tab_id": tid, "type": "counter", "title": "Water", "config": {"daily_target": 8, "unit": "glasses"}}).json()
c.put(f"/api/widgets/{cw['id']}/log", json={"value": 8})

# progress cumulative
p = c.post("/api/widgets", json={"tab_id": tid, "type": "progress", "title": "Run 100km",
     "config": {"goal_mode": "cumulative", "target": 100, "start_date": "2026-06-10", "end_date": "2026-07-10", "unit": "km"}}).json()
c.put(f"/api/widgets/{p['id']}/log", json={"day": "2026-06-15", "value": 12})
pw = c.put(f"/api/widgets/{p['id']}/log", json={"value": 8}).json()
ok("progress pace", pw["pace"]["current"] == 20 and pw["pace"]["required_per_day"] is not None, pw["pace"]["status"])

# progress metric (weight loss)
pm = c.post("/api/widgets", json={"tab_id": tid, "type": "progress", "title": "Weight",
     "config": {"goal_mode": "metric", "target": 75, "start_value": 80, "start_date": "2026-05-20", "end_date": "2026-08-20", "unit": "kg"}}).json()
c.put(f"/api/widgets/{pm['id']}/log", json={"day": "2026-05-20", "value": 80})
pmw = c.put(f"/api/widgets/{pm['id']}/log", json={"value": 78.5}).json()
ok("metric pace direction", pmw["pace"]["direction"] == "down", pmw["pace"].get("projected_date"))

# todo
tw = c.post("/api/widgets", json={"tab_id": tid, "type": "todo", "title": "Checklist"}).json()
it = c.post(f"/api/widgets/{tw['id']}/todos", json={"text": "Stretch"}).json()
c.patch(f"/api/todos/{it['id']}", json={"done": True})
lw = c.get(f"/api/tabs/{tid}/widgets").json()
todo_w = [x for x in lw if x["type"] == "todo"][0]
ok("todo done", todo_w["todos"][0]["done"] == 1)

# today + dashboard
today = c.get("/api/today").json()
ok("today items", len(today["items"]) >= 3, len(today["items"]))
dash = c.get("/api/dashboard").json()
ok("dashboard streak", dash["current_streak"] >= 1, dash)

# notes + unified search
c.post("/api/entries", json={"kind": "note", "title": "Proxmox tips", "body": "Unprivileged LXC. Tailscale needs the tun device enabled."})
c.post("/api/entries", json={"kind": "journal", "title": "J", "body": "Shipped the tailscale config, felt focused.", "entry_date": "2026-06-17", "slot": "pm"})
s_all = c.get("/api/search", params={"q": "tailscal"}).json()
ok("fuzzy/prefix search hits both", len(s_all) == 2, [x["kind"] for x in s_all])
s_note = c.get("/api/search", params={"q": "tailscal", "kind": "note"}).json()
ok("search kind filter", len(s_note) == 1 and s_note[0]["kind"] == "note")

# import .md
import io
files = {"files": ("notes/My Note.md", io.BytesIO(b"# Imported Title\n\nbody text about kubernetes"), "text/markdown")}
imp = c.post("/api/import", files=files).json()
ok("import md", imp["imported"] == 1 and imp["entries"][0]["title"] == "Imported Title", imp)
ok("imported searchable", len(c.get("/api/search", params={"q": "kubernetes"}).json()) == 1)

# journal today
jt = c.get("/api/journal/today", params={"slot": "pm"}).json()
ok("journal prompt present", jt["prompt"] is not None and "entry" in jt)

# exports
ok("json export", c.get("/api/export/json").status_code == 200)
ok("csv export", c.get("/api/export/csv").status_code == 200)
ok("db export", c.get("/api/export/db").status_code == 200)

# static
ok("index served", c.get("/").status_code == 200)
ok("app.js served", c.get("/app.js").status_code == 200)
ok("manifest served", c.get("/manifest.json").status_code == 200)

# delete cascade
c.delete(f"/api/tabs/{tid}")
ok("tab deleted", c.get("/api/today").json()["items"] == [])

print("\nALL TESTS PASSED")
