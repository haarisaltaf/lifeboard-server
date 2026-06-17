import os, shutil, io, json
os.environ["LIFEBOARD_DATA"] = "/tmp/lbtest2"
shutil.rmtree("/tmp/lbtest2", ignore_errors=True)
from fastapi.testclient import TestClient
import app as A
A.db.init_db()
c = TestClient(A.app)

def ok(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + ("  " + str(extra) if extra else ""))
    assert cond, label

# templates
tpls = c.get("/api/templates").json()
ok("templates listed", len(tpls) >= 5, len(tpls))
tab = c.post("/api/tabs/from_template", json={"template_id": "cut"}).json()
tid = tab["id"]
ws = c.get(f"/api/tabs/{tid}/widgets").json()
ok("template instantiated widgets", len(ws) == 4, [w["type"] for w in ws])
ok("template metric widget has pace", any(w["type"] == "progress" and "pace" in w for w in ws))

# reorder
order = list(reversed([w["id"] for w in ws]))
c.patch(f"/api/tabs/{tid}/reorder", json={"order": order})
ws2 = c.get(f"/api/tabs/{tid}/widgets").json()
ok("reorder persisted", [w["id"] for w in ws2] == order, [w["id"] for w in ws2])

# log some habit data for review
hid = [w["id"] for w in ws if w["type"] == "habit"][0]
from datetime import date, timedelta
for i in range(7):
    if i % 2 == 0:
        c.put(f"/api/widgets/{hid}/log", json={"day": (date.today()-timedelta(days=i)).isoformat(), "value": 1})
rev = c.get("/api/review", params={"period": "week"}).json()
ok("review week", rev["days"] == 7 and "items" in rev and len(rev["daily"]) == 7, rev["overall_rate"])
ok("review has habit rate", any("rate" in it for it in rev["items"]))

# config export/import roundtrip
exp = c.get("/api/config/export").json()
ok("config export structure", "tabs" in exp and exp["version"] == 2, len(exp["tabs"]))
imp = c.post("/api/config/import", json={"config": exp, "replace": False}).json()
ok("config import", imp["ok"] and imp["tabs"] == len(exp["tabs"]))

# related notes
c.post("/api/entries", json={"kind": "note", "title": "Tailscale on Proxmox", "body": "tailscale serve https lxc proxmox tun device unprivileged container networking"})
c.post("/api/entries", json={"kind": "note", "title": "Proxmox networking", "body": "proxmox lxc container tun device bridge vlan networking unprivileged"})
c.post("/api/entries", json={"kind": "note", "title": "Sourdough", "body": "flour water starter levain bake oven crumb hydration"})
ents = c.get("/api/entries", params={"kind": "note"}).json()
first = [e for e in ents if "Tailscale" in e["title"]][0]
rel = c.get(f"/api/entries/{first['id']}/related").json()
ok("related notes ranks proxmox above sourdough", rel and rel[0]["title"] == "Proxmox networking",
   [(r["title"], r["score"]) for r in rel])

# prompt scheduling
wd = str(date.today().weekday())
p = c.post("/api/prompts", json={"text": "Scheduled-today prompt", "slot": "pm", "weekdays": wd}).json()
ok("prompt has weekdays", p["weekdays"] == wd)
jt = c.get("/api/journal/today", params={"slot": "pm"}).json()
ok("scheduled prompt surfaces today", jt["prompt"] and jt["prompt"]["text"] == "Scheduled-today prompt", jt["prompt"])
# a prompt scheduled for a different weekday should NOT be forced
other = str((date.today().weekday()+1) % 7)
c.post("/api/prompts", json={"text": "Other-day only", "slot": "am", "weekdays": other})
jt_am = c.get("/api/journal/today", params={"slot": "am"}).json()
ok("off-day scheduled prompt not shown", (jt_am["prompt"] or {}).get("text") != "Other-day only", jt_am.get("prompt"))

# reminders settings (no actual network send)
c.put("/api/reminders", json={"enabled": True, "server": "https://ntfy.sh", "topic": "lb-test-xyz", "time": "20:00"})
r = c.get("/api/reminders").json()
ok("reminder settings saved", r["enabled"] and r["topic"] == "lb-test-xyz")

print("\nALL V2 TESTS PASSED")
