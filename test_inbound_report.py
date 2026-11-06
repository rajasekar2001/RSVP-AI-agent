"""Offline tests: incoming call-backs, call-length limit, report filters + downloads."""
import io, json, os, tempfile
os.environ.update(SARVAM_API_KEY="k", SARVAM_ORG_ID="o", SARVAM_WORKSPACE_ID="w", SARVAM_APP_ID="a",
                  SARVAM_APP_VERSION="3", SARVAM_CONNECTION_ID="c", SARVAM_PHONE_NUMBER="+918012345678",
                  PUBLIC_BASE_URL="https://example.onrender.com", SARVAM_WEBHOOK_TOKEN="t",
                  SARVAM_INSTRUCTIONS_TOKEN="ins", RSVP_RECORDINGS_ACCESS_TOKEN="rec")
import app, call_features as cf
from fastapi.testclient import TestClient

app.STATE_FILE = app.Path(tempfile.mkdtemp()) / "state.json"
c = TestClient(app.app)
H = {"X-Instructions-Token": "ins"}; R = {"X-Recordings-Token": "rec"}

assert c.post("/api/build", json={
    "prompt": "Event name: Community Dinner\nEvent date: Saturday\nEvent time: 6:30 PM\nEvent venue: Community Hall\nAsk RSVP.",
    "max_call_seconds": 45}).status_code == 200
st = app.load_state()
st["contacts"] = [
    {"name": "Raja", "phone": "+919876543210", "status": "Busy"},
    {"name": "Ananya", "phone": "+919812345678", "status": "Completed", "rsvp": "Yes", "guests": "Vino, Mannu",
     "duration": 41.8, "call_ended_at": "2026-11-03T11:00:00", "attempt_id": "a2"},
    {"name": "=cmd|evil", "phone": "+919811223344", "status": "Failed", "rsvp": "No", "duration": 70,
     "call_ended_at": "2026-11-04T09:00:00", "attempt_id": "a3"},
]
app.save_state(st)

# 1) time limit is in what the phone agent receives (outbound + inbound previews)
out = c.get("/api/call-preview?name=Raja").json()
inn = c.get("/api/call-preview?name=Raja&direction=inbound").json()
assert "within 45 seconds" in out["call_instructions"] and "within 45 seconds" in inn["call_instructions"]
assert "I called you earlier" in inn["opening_message"] and "Are you free to talk now?" in inn["opening_message"] and "Raja" in inn["opening_message"]
assert inn["scenario"] == "missed" and "busy or not free" in inn["call_instructions"] and "Hello, can you hear me?" in inn["call_instructions"]
ans = c.get("/api/call-preview?name=Raja&direction=inbound&scenario=answered").json()
unk = c.get("/api/call-preview?direction=inbound&scenario=unknown").json()
assert "Thanks for calling back" in ans["opening_message"] and "already have your RSVP" in ans["opening_message"]
assert "How can I help you?" in unk["opening_message"] and "NOT on our invite list" in unk["call_instructions"]
assert "Community Hall" in inn["call_instructions"]

# 2) on-start hook for inbound: known and unknown caller, token required
assert c.post("/api/sarvam/inbound-context", json={"phone": "+919876543210"}).status_code == 401
k = c.post("/api/sarvam/inbound-context", headers=H, json={"user_phone_number": "09876543210"}).json()
assert k["known_caller"] and k["contact_name"] == "Raja", k
u = c.get("/api/sarvam/inbound-context?phone=%2B911111111111", headers=H).json()
assert not u["known_caller"] and u["scenario"] == "unknown" and "NOT on our invite list" in u["call_instructions"]
# token may also come as ?token= (matches SARVAM_WEBHOOK_TOKEN too) so the hook needs no extra header
assert c.get("/api/sarvam/inbound-context?phone=%2B919876543210&token=t").json()["contact_name"] == "Raja"
assert c.get("/api/sarvam/inbound-context?phone=%2B919876543210&token=ins").status_code == 200
assert c.get("/api/sarvam/inbound-context?phone=%2B919876543210&token=wrong").status_code == 401
# hook never fails: broken state -> fallback context (no silent call)
from unittest import mock
with mock.patch.object(app, "build_inbound_context", side_effect=RuntimeError("boom")):
    fb = c.get("/api/sarvam/inbound-context?phone=1&token=t").json()
assert fb["scenario"] == "fallback" and "Are you free to talk now?" in fb["opening_message"]
# self-check endpoint
hl = c.get("/api/inbound/health").json()
labels = {x["label"][:22]: x["ok"] for x in hl["checks"]}
assert any("Sarvam has called the on-start hook" in x["label"] and x["ok"] for x in hl["checks"])
assert all("ok" in x for x in hl["checks"])
# old saved default script is upgraded automatically
st2 = app.load_state(); st2["agent"]["inbound_prompt"] = cf.LEGACY_INBOUND_PROMPT_V1; app.save_state(st2)
assert app.load_state()["agent"]["inbound_prompt"] == cf.DEFAULT_INBOUND_PROMPT

# 3) inbound webhook: Raja (who was busy) calls back and gives his RSVP
tr = [{"role": "agent", "en_text": "Hello Raja! Thanks for calling back. The event is on Saturday at 6:30 PM at Community Hall. Would you like to confirm if you can attend?"},
      {"role": "user", "en_text": "Yes, I will come."},
      {"role": "agent", "en_text": "How many additional guests will you bring?"}, {"role": "user", "en_text": "Two."},
      {"role": "agent", "en_text": "Could you tell me their names?"}, {"role": "user", "en_text": "Vino and Mannu."},
      {"role": "agent", "en_text": "Thank you. Noted."}]
body = {"interaction_id": "20261105/abc", "user_phone_number": "+919876543210", "agent_phone_number": "+918012345678",
        "duration": 38.2, "start_datetime": "2026-11-05T07:00:00Z", "end_datetime": "2026-11-05T07:00:38Z",
        "interaction_transcript": tr, "final_agent_variables": {}}
assert c.post("/api/sarvam/inbound-webhook?token=bad", json=body).status_code == 401
r = c.post("/api/sarvam/inbound-webhook?token=t", json=body)
assert r.status_code == 200 and r.json()["matched_contact"], r.text
raja = app.load_state()["contacts"][0]
assert (raja["rsvp"], raja["guests"], raja["status"]) == ("Yes", "Vino, Mannu", "Completed"), raja
c.post("/api/sarvam/inbound-webhook?token=t", json=body)  # duplicate delivery
assert len(app.load_state()["inbound_calls"]) == 1
# unknown caller is stored but touches nobody
c.post("/api/sarvam/inbound-webhook?token=t", json={**body, "interaction_id": "20261105/zzz", "user_phone_number": "+910000000001", "interaction_transcript": [tr[0], {"role": "user", "en_text": "What is the venue?"}]})
assert app.load_state()["contacts"][1]["rsvp"] == "Yes"

# 3b) inbound shows in call history + transcript endpoint; public state hides transcripts
calls = c.get("/api/calls", headers=R).json()["calls"]
assert any(x["direction"] == "Inbound" and x["attempt_id"] == "20261105/abc" for x in calls)
t = c.get("/api/calls/20261105%2Fabc/transcript", headers=R)
assert t.status_code == 200 and len(t.json()["transcript"]) == 7, t.text
assert "transcript" not in json.dumps(c.get("/api/state").json()["inbound_calls"])

# 4) report: filters + formats
rows = cf.collect_rows(app.load_state())
assert len(rows) == 5
def pre(**f): return c.post("/api/report", json={"format": "preview", "filters": f}).json()["count"]
assert pre() == 5 and pre(direction=["Inbound"]) == 2 and pre(rsvp=["Yes"]) == 3 and pre(rsvp=["none"]) == 1
assert pre(has_guests=True) == 3 and pre(search="ananya") == 1 and pre(over_limit_only=True) == 1  # 70s > 45s (41.8 and 38.2 ok)
assert pre(date_from="2026-11-04", date_to="2026-11-04") == 1 and pre(min_duration=40, max_duration=50) == 1
assert pre(status=["Completed", "Failed"]) == 3

cols = ["name", "phone", "rsvp", "guests", "duration"]
csv_r = c.post("/api/report", json={"format": "csv", "columns": cols, "filters": {"rsvp": ["Yes"]}})
assert csv_r.status_code == 200 and "attachment" in csv_r.headers["content-disposition"]
text = csv_r.content.decode("utf-8-sig")
assert text.splitlines()[0] == "Name,Phone,RSVP,Guests,Duration (s)" and "Ananya" in text and "Raja" in text
all_csv = c.post("/api/report", json={"format": "csv", "columns": ["name"]}).content.decode("utf-8-sig")
assert "'=cmd|evil" in all_csv                       # formula injection neutralised
x = c.post("/api/report", json={"format": "xlsx", "columns": cols})
from openpyxl import load_workbook
ws = load_workbook(io.BytesIO(x.content)).active
assert ws["A1"].value == "Name" and ws.max_row == 6
j = c.post("/api/report", json={"format": "json", "columns": cols, "filters": {"direction": ["Outbound"]}}).json()
assert len(j) == 3 and set(j[0]) == set(cols)
pdf = c.post("/api/report", json={"format": "pdf", "columns": cols})
assert pdf.content[:4] == b"%PDF"
assert c.post("/api/report", json={"format": "exe"}).status_code == 400
empty = c.post("/api/report", json={"format": "pdf", "filters": {"search": "nobody-matches"}})
assert empty.status_code == 422 and "No rows match" in empty.json()["detail"]
from call_features import describe_filters
assert describe_filters({}) == "none (all rows)" and "RSVP: Yes" in describe_filters({"rsvp": ["Yes"]})
# transcript column is private
assert c.post("/api/report", json={"format": "csv", "columns": ["name", "transcript"]}).status_code == 401
tcsv = c.post("/api/report", headers=R, json={"format": "csv", "columns": ["name", "transcript"], "filters": {"direction": ["Inbound"]}})
assert tcsv.status_code == 200 and "Thanks for calling back" in tcsv.content.decode("utf-8-sig")
assert c.get("/api/report/columns").json()["columns"][0]["key"] == "name"
print("inbound + report tests passed")
