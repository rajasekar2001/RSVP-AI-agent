"""Offline tests: automatic retry of unanswered calls (default every 60 min, max attempts, call hours)."""
import os, tempfile
from datetime import datetime, timedelta
os.environ.update(SARVAM_API_KEY="k", SARVAM_ORG_ID="o", SARVAM_WORKSPACE_ID="w", SARVAM_APP_ID="a",
                  SARVAM_APP_VERSION="3", SARVAM_CONNECTION_ID="c", SARVAM_PHONE_NUMBER="+918012345678",
                  PUBLIC_BASE_URL="https://example.onrender.com", SARVAM_WEBHOOK_TOKEN="t",
                  RSVP_RECORDINGS_ACCESS_TOKEN="rec")
from unittest import mock
import app, call_features as cf
from fastapi.testclient import TestClient

app.STATE_FILE = app.Path(tempfile.mkdtemp()) / "state.json"
c = TestClient(app.app)

# --- pure helpers
s = cf.retry_settings({"retry_interval_minutes": 1, "retry_max_attempts": 99, "retry_window_start": "bad"})
assert s["retry_interval_minutes"] == 5 and s["retry_max_attempts"] == 10 and s["retry_window_start"] == "09:00"
assert cf.retry_settings({})["retry_interval_minutes"] == 60 and cf.retry_settings({})["retry_enabled"] is True
assert cf.in_call_window(datetime(2026, 11, 6, 10, 0), "09:00", "20:00")
assert not cf.in_call_window(datetime(2026, 11, 6, 21, 0), "09:00", "20:00")
assert cf.in_call_window(datetime(2026, 11, 6, 23, 30), "22:00", "06:00")  # crosses midnight

def fresh(**contact):
    st = app.load_state()
    base = {"name": "Raja", "phone": "+919876543210", "status": "Calling", "attempt_id": "att1", "attempts": 1}
    st["contacts"] = [{**base, **contact}]
    st["campaign_started"] = True
    st["agent"].update(retry_window_start="00:00", retry_window_end="23:59", retry_interval_minutes=60, retry_max_attempts=3)
    app.save_state(st)

def hook(status, attempt="att1", **extra):
    r = c.post("/api/sarvam/webhook?token=t", json={"attempt_id": attempt, "status": status, "duration": 0, **extra})
    assert r.status_code == 200, r.text
    return app.load_state()["contacts"][0]

# --- 1. not answered -> retry planned 1 hour later
fresh()
raja = hook("no_answer")
due = datetime.fromisoformat(raja["next_retry_at"])
assert timedelta(minutes=59) < due - datetime.now() <= timedelta(minutes=60), raja
assert "Attempt 1 of 3" in raja["retry_note"] and raja["status"] == "No answer"
for status in ("busy", "failed"):
    fresh(); assert hook(status).get("next_retry_at")

# --- 2. answered -> never retried
fresh(); raja = hook("connected", final_agent_variables={"rsvp": "Yes", "guest_names": "[]"})
assert not raja.get("next_retry_at") and raja["rsvp"] == "Yes"
fresh(); assert not hook("connected").get("next_retry_at")   # picked up but no RSVP: they DID attend the call

# --- 3. last attempt -> gives up
fresh(attempts=3); raja = hook("no_answer")
assert not raja.get("next_retry_at") and "Gave up after 3 attempts" in raja["retry_note"]

# --- 4. due retry is placed; greeting asks "are you free now?"
class R: pass
def due_contact(**kw):
    fresh(status="No answer", call_status="no_answer", next_retry_at=(datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"), **kw)
with mock.patch.object(app, "start_sarvam_outbound", return_value={"attempt_id": "att2", "status": "calling"}) as out:
    due_contact()
    app.run_due_retry()
    ctx = out.call_args.args[2]
    assert "I called you earlier but you were not able to take the call. Are you free to talk now?" in ctx["opening_message"]
    assert "Hello Raja!" in ctx["opening_message"] and "call attempt 2" in ctx["call_instructions"]
    r2 = app.load_state()["contacts"][0]
    assert (r2["attempts"], r2["status"], r2["attempt_id"]) == (2, "Calling", "att2") and "next_retry_at" not in r2

    # --- 5. things that must NOT trigger a call
    out.reset_mock()
    due_contact(); st = app.load_state(); st["campaign_started"] = False; app.save_state(st); app.run_due_retry()
    due_contact(); st = app.load_state(); st["agent"]["retry_enabled"] = False; app.save_state(st); app.run_due_retry()
    due_contact(); st = app.load_state(); st["agent"].update(retry_window_start="03:00", retry_window_end="03:01"); app.save_state(st); app.run_due_retry()
    due_contact(); st = app.load_state(); st["contacts"][0]["next_retry_at"] = (datetime.now() + timedelta(minutes=30)).isoformat(); app.save_state(st); app.run_due_retry()
    due_contact(); st = app.load_state(); st["contacts"][0]["do_not_call"] = True; app.save_state(st); app.run_due_retry()
    due_contact(); st = app.load_state(); st["contacts"].append({"name": "Other", "phone": "+911", "status": "Calling"}); app.save_state(st); app.run_due_retry()
    assert out.call_count == 0, out.call_args_list

# --- 6. they ring the AI back -> retries stop
due_contact()
body = {"interaction_id": "20261106/x1", "user_phone_number": "+919876543210", "duration": 30,
        "interaction_transcript": [{"role": "agent", "en_text": "Hello Raja!"}, {"role": "user", "en_text": "I am busy now"}]}
assert c.post("/api/sarvam/inbound-webhook?token=t", json=body).status_code == 200
raja = app.load_state()["contacts"][0]
assert "next_retry_at" not in raja and "called back" in raja["retry_note"]

# --- 7. launch (config) errors never auto-redial
fresh(status="Ready")
with mock.patch.object(app, "start_sarvam_outbound", side_effect=RuntimeError("bad key")):
    try: app.start_real_contact_call(app.load_state()["contacts"][0])
    except RuntimeError: pass
raja = app.load_state()["contacts"][0]
assert raja["status"] == "Failed" and not raja.get("next_retry_at") and not cf.should_retry(raja, cf.retry_settings({}))

# --- 8. /api/build saves + clamps settings; failed call transcript message is clean
r = c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT, "retry_interval_minutes": 2, "retry_max_attempts": 50, "retry_enabled": False,
                               "retry_window_start": "08:30", "retry_window_end": "21:15"})
a = app.load_state()["agent"]
assert (a["retry_interval_minutes"], a["retry_max_attempts"], a["retry_enabled"], a["retry_window_end"]) == (5, 10, False, "21:15")
fresh(status="Failed", call_status="failed", interaction_id="x/1")
t = c.get("/api/calls/att1/transcript", headers={"X-Recordings-Token": "rec"}).json()
assert t["transcript"] == [] and "did not connect" in t["note"], t
hl = c.get("/api/inbound/health").json()
assert "Are you free to talk now?" in hl["sarvam_defaults"]["opening_message"]
print("retry tests passed")
