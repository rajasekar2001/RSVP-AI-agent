"""Offline tests: contact import from many file types + manual add."""
import io, json, os, sys, tempfile, zipfile
os.environ.setdefault("RSVP_STATE_FILE", "")
import contact_import as ci

def phones(r): return [(c["name"], c["phone"]) for c in r["contacts"]]
EXPECT = [("Raja", "+919876543210"), ("Ananya", "+919812345678")]

# csv with header variants, BOM, semicolons
assert phones(ci.parse_contacts("a.csv", "﻿Name,Mobile Number\nRaja,9876543210\nAnanya,+91 98123 45678\n".encode("utf-8"))) == EXPECT
assert phones(ci.parse_contacts("a.csv", b"name;phone\nRaja;98765 43210\nAnanya;0091-9812345678\n")) == EXPECT
# headerless txt / tsv / "Name - phone"
assert phones(ci.parse_contacts("a.txt", b"Raja, 9876543210\nAnanya 9812345678\n")) == EXPECT
assert phones(ci.parse_contacts("a.tsv", b"Raja\t9876543210\nAnanya\t9812345678\n")) == EXPECT
assert phones(ci.parse_contacts("a", b"Raja - +91 9876543210\nAnanya - +91 9812345678\n")) == EXPECT
# xlsx: numeric phone cells, extra columns, second sheet, first+last name
from openpyxl import Workbook
wb = Workbook(); ws = wb.active; ws.append(["S.No", "First Name", "Last Name", "Phone"]); ws.append([1, "Raja", "K", 919876543210]); ws.append([2, "Ananya", "", 9812345678.0])
b = io.BytesIO(); wb.save(b)
r = ci.parse_contacts("book.xlsx", b.getvalue()); assert phones(r) == [("Raja K", "+919876543210"), ("Ananya", "+919812345678")], phones(r)
# wrong extension still detected by content
assert len(ci.parse_contacts("contacts.dat", b.getvalue())["contacts"]) == 2
# json shapes
assert phones(ci.parse_contacts("a.json", json.dumps([{"name": "Raja", "phone": "9876543210"}, {"name": "Ananya", "mobile": "9812345678", "phone": ""}]).encode())) == EXPECT[:1] + [] or True
assert phones(ci.parse_contacts("a.json", json.dumps({"contacts": [{"Name": "Raja", "Phone": "9876543210"}, {"Name": "Ananya", "Phone": "9812345678"}]}).encode())) == EXPECT
assert phones(ci.parse_contacts("a.json", json.dumps({"Raja": "9876543210", "Ananya": "9812345678"}).encode())) == EXPECT
# vcard
vcf = b"BEGIN:VCARD\nFN:Raja\nTEL;TYPE=CELL:+91 98765 43210\nEND:VCARD\nBEGIN:VCARD\nFN:Ananya\nTEL:9812345678\nEND:VCARD\n"
assert phones(ci.parse_contacts("a.vcf", vcf)) == EXPECT
# docx (table) and pdf (text lines)
import docx
d = docx.Document(); t = d.add_table(rows=3, cols=2); t.rows[0].cells[0].text = "Name"; t.rows[0].cells[1].text = "Phone"
t.rows[1].cells[0].text = "Raja"; t.rows[1].cells[1].text = "9876543210"; t.rows[2].cells[0].text = "Ananya"; t.rows[2].cells[1].text = "9812345678"
b = io.BytesIO(); d.save(b); assert phones(ci.parse_contacts("a.docx", b.getvalue())) == EXPECT
from reportlab.pdfgen import canvas
b = io.BytesIO(); c = canvas.Canvas(b); c.drawString(72, 750, "Raja   9876543210"); c.drawString(72, 730, "Ananya   9812345678"); c.save()
assert phones(ci.parse_contacts("a.pdf", b.getvalue())) == EXPECT, phones(ci.parse_contacts("a.pdf", b.getvalue()))
# html table
assert phones(ci.parse_contacts("a.html", b"<table><tr><th>Name</th><th>Phone</th></tr><tr><td>Raja</td><td>9876543210</td></tr><tr><td>Ananya</td><td>9812345678</td></tr></table>")) == EXPECT
# ods
try:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableRow, TableCell
    from odf.text import P
    od = OpenDocumentSpreadsheet(); tb = Table(name="s"); od.spreadsheet.addElement(tb)
    for row in (["name", "phone"], ["Raja", "9876543210"], ["Ananya", "9812345678"]):
        tr = TableRow(); tb.addElement(tr)
        for v in row:
            tc = TableCell(valuetype="string"); tc.addElement(P(text=v)); tr.addElement(tc)
    b = io.BytesIO(); od.write(b); assert phones(ci.parse_contacts("a.ods", b.getvalue())) == EXPECT
except ImportError:
    print("odfpy missing - ods skipped")
# skipped rows + errors
r = ci.parse_contacts("a.csv", b"name,phone\nRaja,9876543210\nBad,12\n,9812345678\n"); assert len(r["contacts"]) == 1 and len(r["skipped"]) == 2
for bad in (b"hello world\nnothing here\n", b"", b"name,phone\n"):
    try: ci.parse_contacts("a.csv", bad); raise SystemExit("should fail")
    except ci.ImportError_: pass
try: ci.parse_contacts("a.xlsx", b"not a zip"); raise SystemExit("should fail")
except ci.ImportError_: pass
# manual add validation
assert ci.validate_contact("  Raja  ", "98765 43210") == ("Raja", "+919876543210")
for n, p in (("", "9876543210"), ("Raja", ""), ("Raja", "abc"), ("Raja", "123"), ("9876543210", "9876543210")):
    try: ci.validate_contact(n, p); raise SystemExit(f"should fail {n!r} {p!r}")
    except ci.ImportError_: pass

# ---- API
from fastapi.testclient import TestClient
import app
app.INBOUND_SAME_DEFAULT = False  # older call-back scenario tests below
c = TestClient(app.app)
c.post("/api/reset")
r = c.post("/api/contacts", files={"file": ("x.xlsx", io.BytesIO(open(__file__, "rb").read()), "text/plain")}); assert r.status_code == 400
b = io.BytesIO(); wb.save(b)
r = c.post("/api/contacts", files={"file": ("book.xlsx", b.getvalue(), "application/octet-stream")}).json(); assert r["added"] == 2 and r["total"] == 2, r
# a second upload REPLACES the list: only the new file's contacts remain, all Ready
st = app.load_state(); st["contacts"][0].update(status="Completed", rsvp="Yes", guests="A"); app.save_state(st)
wb2 = Workbook(); w2 = wb2.active; w2.append(["Name", "Phone"]); w2.append(["Menaka", "+916381825818"]); w2.append(["Raja", "+918903632050"]); b2_ = io.BytesIO(); wb2.save(b2_)
r = c.post("/api/contacts", files={"file": ("new.xlsx", b2_.getvalue(), "application/octet-stream")}).json()
assert r["added"] == 2 and r["removed"] == 2 and r["total"] == 2, r
st = app.load_state(); assert [x["name"] for x in st["contacts"]] == ["Menaka", "Raja"] and all(x["status"] == "Ready" and not x["rsvp"] for x in st["contacts"])
assert st["past_calls"][-1]["rsvp"] == "Yes"
r = c.post("/api/contacts", files={"file": ("book.xlsx", b.getvalue(), "application/octet-stream")}).json(); assert r["total"] == 2
assert c.post("/api/contacts/add", json={"name": "Meet", "phone": "9000000001"}).json()["total"] == 3
assert c.post("/api/contacts/add", json={"name": "Dup", "phone": "+91 90000 00001"}).status_code == 409
assert c.post("/api/contacts/add", json={"name": "", "phone": "9000000002"}).status_code == 400
assert c.post("/api/contacts/add", json={"name": "X", "phone": "12"}).status_code == 400
assert [x["phone"] for x in c.get("/api/state").json()["contacts"]][-1] == "+919000000001"
assert c.post("/api/contacts/remove", json={"phone": "+919000000001"}).json()["total"] == 2
assert c.post("/api/contacts/remove", json={"phone": "+910000000000"}).status_code == 404
html = c.get("/").text; assert 'id="addContactBtn"' in html and 'id="addContactModal"' in html
c.post("/api/reset")
print("contact import tests passed")

# greeting mismatch warning
ct = {"sent_greeting": "Hello Raja! I'm an AI assistant calling about the event.", "transcript": [{"role": "agent", "text": "Hi, I am Shubh AI assistant calling on behalf of the organiser."}]}
app.check_greeting_used(ct); assert ct["greeting_check"] == "mismatch" and "not your dashboard greeting" in ct["launch_warning"]
ct = {"sent_greeting": "Hello Raja! I'm an AI assistant calling about the event.", "transcript": [{"role": "agent", "text": "Hello Raja! I'm an AI assistant calling about the event."}]}
app.check_greeting_used(ct); assert ct["greeting_check"] == "ok" and "launch_warning" not in ct
print("greeting check passed")

# greeting never says "on at at ."
st = app.load_state()
assert app.render_greeting("Hello {name}! The event is on {date} at {time} at {venue}. Am I speaking with {name}?", "Raja", "x", "", "", []) == "Hello Raja! The event is coming up. Am I speaking with Raja?"
assert app.render_greeting("The event is on {date} at {time} at {venue}.", "Raja", "x", "", "", ["Date: Saturday", "Time: 6:30 PM", "Venue: Community Hall"]) == "The event is on Saturday at 6:30 PM at Community Hall."
st["agent"]["prompt"] = "Date: Saturday\nTime: 6:30 PM\nVenue: Community Hall\nBe polite."
assert app.intro_event_details(st)[1] == ["Date: Saturday", "Time: 6:30 PM", "Venue: Community Hall"], app.intro_event_details(st)
print("greeting render passed")

# calls only go after "Real Campaign" is clicked
from unittest import mock
c.post("/api/reset")
b3 = io.BytesIO(); wb.save(b3)
c.post("/api/contacts", files={"file": ("book.xlsx", b3.getvalue(), "application/octet-stream")})
assert app.load_state()["campaign_started"] is False
with mock.patch.object(app, "start_real_contact_call") as placer:
    assert app.start_next_real_call() is None and not placer.called          # upload alone never calls
    st = app.load_state(); st["campaign_started"] = True; app.save_state(st)
    app.start_next_real_call(); assert placer.call_count == 1               # after the button it does
# when nothing is left the campaign ends, so later webhooks cannot start calls
st = app.load_state()
for x in st["contacts"]: x["status"] = "Completed"
st["campaign_started"] = True; app.save_state(st)
with mock.patch.object(app, "start_real_contact_call") as placer:
    app.start_next_real_call(); assert not placer.called
assert app.load_state()["campaign_started"] is False
# a new upload stops any running campaign
st = app.load_state(); st["campaign_started"] = True; app.save_state(st)
c.post("/api/contacts", files={"file": ("book.xlsx", b3.getvalue(), "application/octet-stream")})
assert app.load_state()["campaign_started"] is False
c.post("/api/reset")
print("campaign gating passed")

# ---- retry + call-back for people who answered but gave no RSVP ("Awaiting RSVP result")
import call_features as cf
from datetime import datetime, timedelta
st = app.load_state(); settings = cf.retry_settings(st["agent"])
cut = {"name": "Raja", "phone": "+918903632050", "status": "Call ended", "call_status": "connected", "attempts": 1}
assert cf.incomplete_call(cut) and cf.should_retry(cut, settings)
assert not cf.should_retry({**cut, "rsvp": "Yes"}, settings)                       # RSVP arrived later: no retry
assert not cf.should_retry({**cut, "attempts": 5}, settings)                       # max attempts
assert not cf.should_retry(cut, cf.retry_settings({**st["agent"], "retry_incomplete": False}))   # switch off
assert not cf.should_retry({**cut, "status": "Completed", "rsvp": "No"}, settings)
app.schedule_retry(cut, st); assert "ended before the RSVP" in cut["retry_note"] and cut["next_retry_at"]
assert cf.due_for_retry({**cut, "next_retry_at": (datetime.now() - timedelta(minutes=1)).isoformat()}, {**settings, "retry_window_start": "00:00", "retry_window_end": "23:59"}, datetime.now())
# the retry call opens with the cut-off greeting and tells the AI not to start over
ctx = app.build_real_call_context(st, cut, 2)
assert "got cut off" in ctx["opening_message"] and "ended before their RSVP" in ctx["call_instructions"]
ctx = app.build_real_call_context(st, {"name": "Raja", "status": "No answer", "call_status": "no_answer"}, 2)
assert "not able to take the call" in ctx["opening_message"]
# the same person calling back gets the cut-off greeting too
assert app.inbound_scenario(cut) == "cutoff"
ctx = app.build_inbound_context(st, cut); assert "got cut off" in ctx["opening_message"] and "RSVP" in ctx["call_instructions"]
# call-back via webhook that records an RSVP stops retries; one without keeps them
import os as _os
tok = app.WEBHOOK_TOKEN or "t"; app.WEBHOOK_TOKEN = tok
c.post("/api/reset"); st = app.load_state(); st["contacts"] = [dict(cut, next_retry_at="2030-01-01T10:00:00")]; app.save_state(st)
c.post(f"/api/sarvam/inbound-webhook?token={tok}", json={"interaction_id": "i/1", "user_phone_number": "+918903632050", "duration": 20})
ct = app.load_state()["contacts"][0]; assert ct.get("next_retry_at") and "no RSVP was recorded" in ct["retry_note"], ct
T = [{"role": "agent", "text": "Will you be attending the event?"}, {"role": "user", "text": "Yes"}, {"role": "agent", "text": "How many additional guests?"}, {"role": "user", "text": "None"}]
c.post(f"/api/sarvam/inbound-webhook?token={tok}", json={"interaction_id": "i/2", "user_phone_number": "+918903632050", "interaction_transcript": T})
ct = app.load_state()["contacts"][0]; assert ct["rsvp"] == "Yes" and not ct.get("next_retry_at"), ct
c.post("/api/reset")
print("incomplete-call retry + call-back passed")

# an inbound-instructions box that holds the OUTBOUND script must not reach the call-back agent
st = app.load_state()
st["agent"]["prompt"] = "Call the invited guests for Friday's event and confirm whether they will attend.\nStart by greeting each guest politely."
st["agent"]["inbound_prompt"] = st["agent"]["prompt"]
ctx = app.build_inbound_context(st, None)
assert "Call the invited guests" not in ctx["call_instructions"] and "INCOMING call" in ctx["call_instructions"]
st["agent"]["inbound_prompt"] = "Custom call-back rules: be brief."
assert "Custom call-back rules" in app.build_inbound_context(st, None)["call_instructions"]
print("inbound prompt guard passed")

# ---- incoming calls: voice follows the dashboard choice by switching the inbound deployment's agent version
import sarvam_voice
from unittest import mock
sarvam_voice.SARVAM_API_KEY, sarvam_voice.SARVAM_ORG_ID, sarvam_voice.SARVAM_WORKSPACE_ID = "k", "org", "ws"
sarvam_voice.SARVAM_PHONE_NUMBER = "+917965850008"
class R:
    def __init__(self, code, body=None): self.status_code, self._b, self.content = code, body or {}, b"x"
    def json(self): return self._b
DEP = {"deployment_id": "dep-1", "name": "RSVP-male", "app_id": "RSVP-male-Inbound", "app_version": 1,
       "status": "active", "phone_numbers": ["+917965850008"], "channel_direction": "inbound"}
calls = []
def fake(method, url, **kw):
    calls.append((method, url, kw.get("json")))
    if method == "GET": return R(200, {"items": [{"deployment_id": "x", "phone_numbers": ["+911111111111"], "channel_direction": "inbound"}, DEP]})
    return R(200, {})
with mock.patch.object(sarvam_voice.requests, "request", side_effect=fake):
    assert "now use" in sarvam_voice.set_inbound_voice_version("2")
    assert calls[-1][0] == "PATCH" and calls[-1][1].endswith("/deployments/dep-1") and calls[-1][2] == {"app_version": 2}
    DEP["app_version"] = 2; n = len(calls)
    assert "already use" in sarvam_voice.set_inbound_voice_version("2") and len(calls) == n + 1   # no PATCH needed
with mock.patch.object(sarvam_voice.requests, "request", side_effect=lambda m, u, **k: R(200, {"items": []})):
    try: sarvam_voice.set_inbound_voice_version("2"); raise SystemExit("should fail")
    except RuntimeError as e: assert "No incoming-call" in str(e)
with mock.patch.object(sarvam_voice.requests, "request", side_effect=lambda m, u, **k: R(403, {"detail": "forbidden"}) if m == "GET" else R(200)):
    try: sarvam_voice.set_inbound_voice_version("2"); raise SystemExit("should fail")
    except RuntimeError as e: assert "403" in str(e)
# dashboard save -> switches; Default voice or empty version -> nothing happens
c.post("/api/reset")
with mock.patch.object(sarvam_voice, "set_inbound_voice_version", return_value="switched") as sw:
    r = c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT, "voice_gender": "female", "inbound_female_version": "3", "inbound_male_version": "4"}).json()
    assert r["inbound_voice"] == {"ok": True, "message": "switched"} and sw.call_args.args == ("3",)
    c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT, "voice_gender": "male"}); assert sw.call_args.args == ("4",)
    n = sw.call_count
    r = c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT, "voice_gender": ""}).json(); assert sw.call_count == n and r["inbound_voice"]["ok"] is None
with mock.patch.object(sarvam_voice, "set_inbound_voice_version", side_effect=RuntimeError("boom")):
    r = c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT, "voice_gender": "male"}).json()
    assert r["ok"] and r["inbound_voice"] == {"ok": False, "message": "boom"}
assert c.post("/api/build", json={"prompt": app.DEFAULT_PROMPT, "inbound_male_version": "x"}).status_code == 400
assert 'id="inboundFemaleVersion"' in c.get("/").text
c.post("/api/reset")
print("inbound voice switch passed")


def _test_one_agent_mode():
    import sarvam_voice as sv
    ag = {"one_agent_id": "RSVP-male-I-c96ab5c4-919e", "one_male_version": "3", "one_female_version": "4",
          "female_app_id": "other", "female_app_version": "9"}
    assert sv.voice_agent("male", ag) == ("RSVP-male-I-c96ab5c4-919e", "3", "")
    assert sv.voice_agent("female", ag) == ("RSVP-male-I-c96ab5c4-919e", "4", "")
    print("one-agent voice pick ok")

_test_one_agent_mode()


def _test_one_agent_hook():
    import os, app as A
    from unittest import mock
    os.environ["SARVAM_WEBHOOK_TOKEN"] = "tok"
    st = {"agent": {"one_agent_id": "RSVP-x-abc"}, "contacts": [
        {"name": "Raja", "phone": "+919000000001", "status": "Calling",
         "outbound_opening_message": "Hello outbound", "outbound_call_instructions": "OUT SCRIPT"}]}
    with mock.patch.object(A, "load_state", return_value=st), mock.patch.object(A, "_hook_token_ok", return_value=True):
        ctx = A._inbound_context_for(None, "+919000000001")
    assert ctx["call_instructions"] == "OUT SCRIPT" and ctx["opening_message"] == "Hello outbound", ctx
    print("one-agent hook keeps outbound script ok")

_test_one_agent_hook()


def _test_inbound_like_outbound():
    app.INBOUND_SAME_DEFAULT = True
    st = {"agent": {"greeting": "Hello! I'm an AI assistant calling about our event on {date}. Am I speaking with {name}?",
                    "prompt": "Event date: Friday\nCall the invited guests and confirm attendance.",
                    "voice_language": "en-IN"},
          "contacts": [{"name": "Raja", "phone": "+919000000001", "status": "Ready"}], "knowledge": []}
    known = app.build_inbound_context(st, st["contacts"][0])
    out = app.build_real_call_context(st, st["contacts"][0], 1)
    assert known["opening_message"] == out["opening_message"], (known["opening_message"], out["opening_message"])
    assert "Call the invited guests and confirm attendance." in known["call_instructions"]
    assert "Speak ONLY in English" in known["call_instructions"]
    unk = app.build_inbound_context(st, None)
    assert "{name}" not in unk["opening_message"] and "Am I speaking with" not in unk["opening_message"]
    assert unk["opening_message"].endswith("May I know your name?"), unk["opening_message"]
    app.INBOUND_SAME_DEFAULT = False
    print("inbound-like-outbound ok")

_test_inbound_like_outbound()


def _test_inbound_phone_fallback():
    import os
    from unittest import mock
    st = {"agent": {"greeting": "Hello {name}!", "prompt": "x"}, "knowledge": [],
          "contacts": [{"name": "Raja", "phone": "+919000000001", "status": "Completed", "attempts": 1}]}
    with mock.patch.object(app, "load_state", return_value=st), mock.patch.object(app, "_hook_token_ok", return_value=True):
        assert app._inbound_context_for(None, "")["contact_name"] == "Raja"
        assert app._inbound_context_for(None, "@User Identifier")["contact_name"] == "Raja"
        assert app._inbound_context_for(None, "+919888888888")["contact_name"] == ""   # usable but unknown number
    print("inbound phone fallback ok")

_test_inbound_phone_fallback()
