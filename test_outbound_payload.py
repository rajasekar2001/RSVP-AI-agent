"""Offline test: checks the JSON sent to Sarvam (no real call is placed)."""
import os, json, tempfile
os.environ.update(SARVAM_API_KEY="k", SARVAM_ORG_ID="o", SARVAM_WORKSPACE_ID="w", SARVAM_APP_ID="a",
                  SARVAM_APP_VERSION="3", SARVAM_CONNECTION_ID="c", SARVAM_PHONE_NUMBER="+918012345678",
                  PUBLIC_BASE_URL="https://example.onrender.com", SARVAM_WEBHOOK_TOKEN="t")
from unittest import mock
import app, sarvam_voice
from fastapi.testclient import TestClient

app.STATE_FILE = app.Path(tempfile.mkdtemp()) / "state.json"
c = TestClient(app.app)
r = c.post("/api/build", json={
    "prompt": "Event name: Community Dinner\nEvent date: Saturday\nEvent time: 6:30 PM\nEvent venue: Community Hall\nAsk RSVP politely.",
    "greeting": "Vanakkam! This is an AI assistant. The event is on {date} at {time} at {venue}. Am I speaking with {name}?",
    "voice_language": "ta-IN", "voice_tone": "friendly", "voice_speed": 0.8})
assert r.status_code == 200, r.text
p = c.get("/api/call-preview?name=Raja").json()
print(p["opening_message"]); print(p["language"])
assert p["opening_message"] == "Vanakkam! This is an AI assistant. The event is on Saturday at 6:30 PM at Community Hall. Am I speaking with Raja?"
assert "friendly" in p["call_instructions"] and "slowly" in p["call_instructions"]

ctx = app.build_real_call_context(app.load_state(), {"name": "Raja"})
class R:
    ok = True; status_code = 200
    def json(self): return {"attempt_id": "att1"}
with mock.patch.object(sarvam_voice.requests, "post", return_value=R()) as post:
    sarvam_voice.start_sarvam_outbound("+919876543210", "Raja", ctx)
ac = post.call_args.kwargs["json"]["app_config"]
assert ac["app_overrides"] == {"initial_bot_message": p["opening_message"], "initial_language_name": "Tamil"}, ac
assert ac["agent_variables"]["call_instructions"].startswith("Event name")

# default greeting
c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT, "greeting": "", "voice_language": ""})
d = c.get("/api/call-preview?name=Raja").json()
print(d["opening_message"])
assert d["opening_message"].endswith("Am I speaking with Raja?")
print("payload tests passed")

# 422 "variable not found" -> drop those variables and retry once
class Bad:
    ok = False; status_code = 422
    def json(self):
        return {"error": {"message": "Invalid Parameter", "data": {"details":
            "Agent variables '{'opening_message', 'call_instructions'}' not found in agent variables of app 'X'"}}}
class Good:
    ok = True; status_code = 200
    def json(self): return {"attempt_id": "att2"}
c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT})
ctx = app.build_real_call_context(app.load_state(), {"name": "Raja"})
with mock.patch.object(sarvam_voice.requests, "post", side_effect=[Bad(), Good()]) as post:
    res = sarvam_voice.start_sarvam_outbound("+919876543210", "Raja", ctx)
assert post.call_count == 2 and res["attempt_id"] == "att2"
assert sorted(res["dropped_variables"]) == ["call_instructions", "opening_message"]
assert "agent_variables" not in post.call_args.kwargs["json"]["app_config"]
assert "initial_bot_message" in post.call_args.kwargs["json"]["app_config"]["app_overrides"]
print("422 retry test passed")
