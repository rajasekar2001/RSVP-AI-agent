"""Offline test: transcript is fetched from Sarvam when the webhook has none."""
import os, tempfile
os.environ.update(SARVAM_API_KEY="k", SARVAM_ORG_ID="o", SARVAM_WORKSPACE_ID="w", SARVAM_APP_ID="a",
                  SARVAM_APP_VERSION="3", SARVAM_CONNECTION_ID="c", SARVAM_PHONE_NUMBER="+918012345678",
                  PUBLIC_BASE_URL="https://example.onrender.com", SARVAM_WEBHOOK_TOKEN="t",
                  RSVP_RECORDINGS_ACCESS_TOKEN="rec")
from unittest import mock
import app
from fastapi.testclient import TestClient

app.STATE_FILE = app.Path(tempfile.mkdtemp()) / "state.json"
st = app.load_state()
st["contacts"] = [{"name": "Raja", "phone": "+919876543210", "status": "Calling", "attempt_id": "att1"}]
app.save_state(st)

class Resp:
    ok = True; status_code = 200
    def json(self): return {"interaction_id": "20261103/abc", "messages": [
        {"turn_id": 1, "role": "assistant", "content": "Hello! Am I speaking with Raja?", "language_name": "English"},
        {"turn_id": 2, "role": "user", "content": "Yes", "language_name": "English"}]}

c = TestClient(app.app)
# webhook WITHOUT transcript (what happened in your dashboard)
r = c.post("/api/sarvam/webhook?token=t", json={"attempt_id": "att1", "status": "connected", "duration": 41.8,
           "interaction_id": "20261103/abc", "final_agent_variables": None, "interaction_transcript": None})
assert r.status_code == 200, r.text
assert not app.load_state()["contacts"][0].get("transcript")

with mock.patch.object(app.requests, "get", return_value=Resp()) as g:
    t = c.get("/api/calls/att1/transcript", headers={"X-Recordings-Token": "rec"}).json()
assert [x["role"] for x in t["transcript"]] == ["agent", "user"], t
assert t["transcript"][0]["en_text"].startswith("Hello!")
assert "20261103%2Fabc" in g.call_args.args[0]
assert app.load_state()["contacts"][0]["transcript"], "should be saved"

# empty -> helpful note, not a crash
st = app.load_state(); st["contacts"][0]["transcript"] = []; app.save_state(st)
class Empty(Resp):
    def json(self): return {"messages": []}
with mock.patch.object(app.requests, "get", return_value=Empty()):
    t = c.get("/api/calls/att1/transcript", headers={"X-Recordings-Token": "rec"}).json()
assert t["transcript"] == [] and "yet" in t["note"], t
print("transcript tests passed")
