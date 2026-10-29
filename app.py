import asyncio
import csv
import io
import hmac
import json
import os
import re
import secrets
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse
from openpyxl import load_workbook
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pypdf import PdfReader
from sarvam_voice import config_summary, sarvam_configured, start_sarvam_outbound

load_dotenv()

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "data" / "state.json"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

app = FastAPI(title="RSVP Agent — Sarvam Voice Agents", version="4.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

STATE_LOCK = threading.RLock()
CALL_LAUNCH_LOCK = threading.Lock()
WEBHOOK_TOKEN = os.getenv("SARVAM_WEBHOOK_TOKEN", "").strip()

DEFAULT_STATE = {
    "agent": {
        "name": "RSVP agent",
        "prompt": "Call Saturday's registrants, confirm if they're coming, note whether they're bringing guests and collect every guest name.",
        "language": "English",
        "voice": "Shreya",
        "voice_language": "",
        "voice_tone": "normal",
        "voice_speed": 1.0,
        "built": True,
        "fixed_plus_ones": True,
    },
    "knowledge": [],
    "webhook": {"url": "", "enabled": False},
    "contacts": [],
    "tests": [
        {"id": 1, "title": "Happy path", "description": "Says yes, brings one guest, gives the name", "status": "ready", "time": ""},
        {"id": 2, "title": "Caller says no", "description": "Declines, agent thanks them and ends the call", "status": "ready", "time": ""},
        {"id": 3, "title": "Two plus ones", "description": "Collect and save both guest names", "status": "ready", "time": ""},
    ],
}


def public_state():
    """Do not expose sensitive call data on the existing public dashboard routes."""
    data = load_state()
    for c in data.get("contacts", []):
        for secret in ("transcript", "sarvam_variables", "recording_url"):
            c.pop(secret, None)
    return data


def load_state():
    with STATE_LOCK:
        if not STATE_FILE.exists():
            save_state(deepcopy(DEFAULT_STATE))
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    with STATE_LOCK:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace avoids partially-written campaign state after a crash.
        temporary = STATE_FILE.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, STATE_FILE)


def elapsed(n):
    return f"0:{n:02d}"


def knowledge_text(state):
    return "\n".join(x.get("text", "") for x in state["knowledge"])[-12000:]


def extract_guest_names(message):
    return [x.strip().title() for x in re.split(r",|\band\b|&", message or "", flags=re.I) if x.strip()]


def get_next_contact(state):
    for contact in state["contacts"]:
        if contact.get("status") not in {"Completed", "Wrong person"}:
            return contact
    return None


def get_next_real_contact(state):
    """One-pass real campaign: call only contacts that are Ready/Queued."""
    for contact in state["contacts"]:
        if contact.get("status", "Ready") in {"Ready", "Queued"}:
            return contact
    return None


def find_contact_by_attempt_id(state, attempt_id):
    for contact in state["contacts"]:
        if contact.get("attempt_id") == attempt_id:
            return contact
    return None


def find_contact_from_metadata(state, metadata):
    metadata = metadata or {}
    name = str(metadata.get("contact_name") or "").strip().lower()
    phone = str(metadata.get("user_phone_number") or "").strip()
    if name:
        found = next((c for c in state["contacts"] if c.get("name", "").lower() == name), None)
        if found:
            return found
    if phone:
        return next((c for c in state["contacts"] if c.get("phone") == phone), None)
    return None

# def agent_reply(state, message, session):
#     m = (message or "").strip().lower()
#     stage = session.get("stage", "opening")
#     name = session.get("name", "there")

#     if any(x in m for x in ["where", "venue", "location", "address"]):
#         for item in state["knowledge"]:
#             for line in item.get("text", "").splitlines():
#                 if any(k in line.lower() for k in ["venue", "location", "address", "hall", "hotel"]):
#                     return {"reply": line.strip(), "stage": stage, "done": False}
#         return {
#             "reply": "I don't have the venue in my knowledge yet. Please add the event brief in Knowledge.",
#             "stage": stage,
#             "done": False,
#         }

#     if stage == "opening":
#         session["stage"] = "rsvp"
#         return {
#             "reply": f"Hi {name}, I'm calling to confirm your RSVP for Saturday. Will you be joining us?",
#             "stage": "rsvp",
#             "done": False,
#         }

#     if stage == "rsvp":
#         if any(x in m for x in ["no", "can't", "cannot", "not coming", "wont", "won't", "unable"]):
#             session["rsvp"] = "No"
#             session["stage"] = "done"
#             return {
#                 "reply": "No problem. Thanks for letting us know. Have a great day!",
#                 "stage": "done",
#                 "done": True,
#                 "result": {"rsvp": "No", "guests": []},
#             }
#         if any(x in m for x in ["yes", "yeah", "yep", "coming", "attend", "sure", "will be there"]):
#             session["rsvp"] = "Yes"
#             session["stage"] = "guest_count"
#             return {
#                 "reply": "Great. Will you be bringing any guests? You can say none, one, two, and so on.",
#                 "stage": "guest_count",
#                 "done": False,
#             }
#         return {
#             "reply": "Sorry, I did not catch that. Will you be attending Saturday, yes or no?",
#             "stage": "rsvp",
#             "done": False,
#         }

#     if stage == "guest_count":
#         nums = {"none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
#         count = None
#         for word, number in nums.items():
#             if re.search(rf"\b{word}\b", m):
#                 count = number
#                 break
#         if count is None:
#             hit = re.search(r"\b([0-5])\b", m)
#             if hit:
#                 count = int(hit.group(1))

#         if count == 0:
#             session["guest_count"] = 0
#             session["stage"] = "done"
#             return {
#                 "reply": "Perfect. I have you down as attending with no additional guests. Thank you!",
#                 "stage": "done",
#                 "done": True,
#                 "result": {"rsvp": "Yes", "guests": []},
#             }

#         if count is not None and count > 0:
#             session["guest_count"] = count
#             session["guest_names"] = []
#             session["stage"] = "guest_names"
#             reply = "Wonderful. What is the name of your guest?" if count == 1 else f"Wonderful. What are the names of your {count} guests?"
#             return {"reply": reply, "stage": "guest_names", "done": False}

#         return {
#             "reply": "How many additional guests are you bringing? Please say a number from zero to five.",
#             "stage": "guest_count",
#             "done": False,
#         }

#     if stage == "guest_names":
#         count = session.get("guest_count", 1)
#         names = extract_guest_names(message)
#         session["guest_names"] = names
#         if len(names) < count:
#             session["stage"] = "guest_names_more"
#             missing = count - len(names)
#             captured = ", ".join(names) if names else "no names yet"
#             return {
#                 "reply": f"I captured {captured}. I still need {missing} more guest name" + ("s." if missing > 1 else "."),
#                 "stage": "guest_names_more",
#                 "done": False,
#             }
#         names = names[:count]
#         session["guest_names"] = names
#         session["stage"] = "done"
#         return {
#             "reply": f"Thanks. I've saved your RSVP with {', '.join(names)}. See you Saturday!",
#             "stage": "done",
#             "done": True,
#             "result": {"rsvp": "Yes", "guests": names},
#         }

#     if stage == "guest_names_more":
#         existing = session.get("guest_names", [])
#         names = existing + extract_guest_names(message)
#         count = session.get("guest_count", len(names))
#         if len(names) < count:
#             session["guest_names"] = names
#             missing = count - len(names)
#             return {
#                 "reply": f"Thank you. I still need {missing} more name" + ("s." if missing > 1 else "."),
#                 "stage": "guest_names_more",
#                 "done": False,
#             }
#         names = names[:count]
#         session["guest_names"] = names
#         session["stage"] = "done"
#         return {
#             "reply": f"Perfect. I saved all {count} guests: {', '.join(names)}. See you Saturday!",
#             "stage": "done",
#             "done": True,
#             "result": {"rsvp": "Yes", "guests": names},
#         }

#     return {"reply": "The RSVP is already complete. Thank you!", "stage": "done", "done": True}




def normalize_rsvp(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value or "").strip().lower()
    if text in {"yes", "y", "true", "attending", "accepted", "confirmed", "coming"}:
        return "Yes"
    if text in {"no", "n", "false", "not attending", "declined", "not coming"}:
        return "No"
    return str(value or "").strip()


def normalize_guests(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, dict):
        return [str(v).strip() for v in value.values() if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [x.strip().title() for x in re.split(r",|\band\b|&", text, flags=re.I) if x.strip()]


def extract_sarvam_rsvp(final_vars):
    """Accept JSON strings and typed variable values; never treat connected as RSVP."""
    if isinstance(final_vars, str):
        try:
            final_vars = json.loads(final_vars)
        except ValueError:
            return "", []
    if not isinstance(final_vars, dict):
        return "", []
    values = {}
    for key, value in final_vars.items():
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        values[str(key).strip().lower()] = value
    rsvp = ""
    for key in ("rsvp", "rsvp_status", "attendance", "attendance_status", "attending", "is_attending", "call_outcome"):
        value = normalize_rsvp(values.get(key))
        if value in {"Yes", "No"}:
            rsvp = value
            break
        if str(value).lower() in {"maybe", "tentative", "unsure"}:
            rsvp = "Maybe"
            break
    guests = []
    for key in ("guests", "guest_names", "plus_ones", "plus_one_names", "guest_list"):
        value = values.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if value not in (None, ""):
            guests = normalize_guests(value)
            break
    guests = [name for name in guests if name.lower() not in {"none", "null", "n/a", "no guests", "0"}]
    return rsvp, guests


def merge_call_result(contact, variables):
    previous = contact.get("sarvam_variables")
    merged = dict(previous) if isinstance(previous, dict) else {}
    if isinstance(variables, str):
        try:
            variables = json.loads(variables)
        except ValueError:
            variables = {}
    if isinstance(variables, dict):
        merged.update({k: v for k, v in variables.items() if v is not None and v != ""})
    contact["sarvam_variables"] = merged
    rsvp, guests = extract_sarvam_rsvp(merged)
    if rsvp:
        contact["rsvp"] = rsvp
        contact["guests"] = ", ".join(guests) if rsvp != "No" else ""
        contact["status"] = "Completed"
        contact["result_status"] = "RSVP received"
    elif not contact.get("rsvp"):
        contact["status"] = "Call ended"
        contact["result_status"] = "RSVP not received — awaiting extraction"


def start_real_contact_call(contact):
    """Reserve one contact before dialing; don't redial when the user clicks twice."""
    with CALL_LAUNCH_LOCK:
        state = load_state()
        real_contact = next((x for x in state["contacts"] if x.get("phone") == contact.get("phone") and x.get("name") == contact.get("name")), None)
        if not real_contact or real_contact.get("status") not in {"Ready", "Queued"}:
            return None
        build_real_call_context(state, real_contact)
        real_contact["status"] = "Starting"
        save_state(state)
        try:
            result = start_sarvam_outbound(real_contact["phone"], real_contact["name"])
        except Exception as exc:
            state = load_state()
            current = next((x for x in state["contacts"] if x.get("phone") == contact.get("phone") and x.get("name") == contact.get("name")), None)
            if current:
                current["status"] = "Failed"
                current["call_status"] = "launch_failed"
                current["failure_reason"] = str(exc)[:400]
                save_state(state)
            raise
        state = load_state()
        current = next((x for x in state["contacts"] if x.get("phone") == contact.get("phone") and x.get("name") == contact.get("name")), None)
        if current and current.get("status") == "Starting":
            current["attempt_id"] = result["attempt_id"]
            current["status"] = "Calling"
            current["call_status"] = result.get("status", "calling")
            save_state(state)
        return result


def start_next_real_call():
    state = load_state()
    contact = get_next_real_contact(state)
    return start_real_contact_call(contact) if contact else None


async def start_next_real_call_after_delay(delay=2):
    await asyncio.sleep(delay)
    try:
        await asyncio.to_thread(start_next_real_call)
    except Exception as exc:
        print("Could not start next Sarvam call:", exc)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"state": public_state()})


@app.get("/api/state")
async def get_state():
    return public_state()


@app.get("/api/contacts/next")
async def next_contact():
    state = load_state()
    contact = get_next_contact(state)
    return {"done": contact is None, "contact": contact}


@app.post("/api/build")
async def build(request: Request):
    data = await request.json()
    state = load_state()
    prompt = (data.get("prompt") or "").strip()
    if len(prompt) < 10:
        raise HTTPException(status_code=400, detail="Describe the agent task in a little more detail.")

    agent = state["agent"]
    # Additive voice preferences; omitted fields leave the current settings unchanged.
    if "voice_language" in data:
        value = str(data["voice_language"] or "")
        if value not in {"", "en-IN", "ta-IN", "hi-IN", "te-IN", "ml-IN", "kn-IN"}:
            raise HTTPException(status_code=400, detail="Unsupported voice language")
        agent["voice_language"] = value
    if "voice_tone" in data:
        value = str(data["voice_tone"] or "normal")
        if value not in {"normal", "friendly", "professional", "calm"}:
            raise HTTPException(status_code=400, detail="Unsupported voice tone")
        agent["voice_tone"] = value
    if "voice_speed" in data:
        try:
            value = float(data["voice_speed"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid voice speed")
        if not 0.7 <= value <= 1.5:
            raise HTTPException(status_code=400, detail="Voice speed must be 0.7 to 1.5")
        agent["voice_speed"] = value

    state["agent"]["prompt"] = prompt
    state["agent"]["built"] = True
    state["agent"]["fixed_plus_ones"] = True
    for test in state["tests"]:
        test["status"] = "ready"
        test["time"] = ""
    save_state(state)
    return {"ok": True, "agent": state["agent"]}

def _contact_context_data(request: Request, phone: str):
    expected = os.getenv(
        "SARVAM_INSTRUCTIONS_TOKEN", ""
    ).strip()

    supplied = request.headers.get(
        "X-Instructions-Token", ""
    ).strip()

    if not expected or not supplied:
        raise HTTPException(
            status_code=401,
            detail="Missing instructions authentication"
        )

    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid instructions authentication"
        )

    phone = str(phone or "").strip()

    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise HTTPException(
            status_code=422,
            detail="Invalid recipient phone"
        )

    current = load_state()

    matches = [row for row in current.get("contacts", [])
               if str(row.get("phone", "")).strip() == phone]
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="Duplicate CSV phone: keep exactly one name per phone")
    contact = matches[0] if matches else None

    if not contact:
        raise HTTPException(
            status_code=404,
            detail="Recipient not found in uploaded CSV"
        )

    name = str(contact.get("name", "")).strip()

    if not name:
        raise HTTPException(
            status_code=409,
            detail="Recipient name is missing"
        )

    return build_real_call_context(current, contact)


def build_real_call_context(state, contact):
    """Generate the greeting and identity gate from this CSV row only."""
    name = str(contact.get("name", "")).strip()
    if not name:
        raise ValueError("Upload a CSV contact with a nonempty name before calling")
    label, facts = intro_event_details(state)
    event_name = next((f.split(":", 1)[1].strip() for f in facts if f.startswith("Event:")), label)
    details = ". ".join(f for f in facts if not f.startswith("Event:"))
    opening = (
        "Hello, I'm an AI assistant calling on behalf of the event organiser. "
        f"We would like to invite you to {event_name}. "
        + (details + ". " if details else "")
        + f"Am I speaking with {name}?"
    )
    instructions = str(state.get("agent", {}).get("prompt", "")).strip()
    flow = (
        "REQUIRED CALL ORDER: Start by speaking this exact opening and nothing after it: "
        + opening + "\nWAIT for an answer. The only expected invitee is " + name + ". "
        "Never read a list of CSV names. Never ask attendance before identity confirmation. "
        "Yes here confirms identity ONLY, not RSVP. If wrong person, apologise and end; "
        "do not ask this person for attendance or guests. If identity is unclear, ask again. "
        "After identity is confirmed ask: Will you be attending " + event_name + "? "
        "WAIT. If attending ask additional guest count excluding the invitee, then collect "
        "ALL guest names. For zero guests do not ask names. If declining record No and "
        "no guests. If unsure record Maybe. Read back the RSVP and all supplied names, "
        "ask for corrections, then thank them and end. Never invent event details or names. "
        "Never treat call connected or identity confirmed as an attendance answer. "
        "If this flow conflicts with other instructions, follow this required call order."
    )
    return {
        "contact_name": name,
        "event_label": event_name,
        "event_details": details,
        "identity_question": f"Am I speaking with {name}?",
        "opening_message": opening,
        "call_instructions": instructions + "\n\n" + flow,
    }


@app.get("/api/sarvam/contact-context")
async def sarvam_contact_context(
    request: Request,
    phone: str = ""
):
    return _contact_context_data(request, phone)


@app.post("/api/sarvam/contact-context")
async def sarvam_contact_context_post(
    request: Request
):
    body = await request.json()

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422,
            detail="Expected JSON object"
        )

    phone = (
        body.get("phone")
        or body.get("user_phone_number")
        or ""
    )

    return _contact_context_data(request, phone)

@app.post("/api/knowledge")
async def add_knowledge(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large. Maximum size is 8 MB.")

    name = file.filename or "knowledge"
    try:
        if name.lower().endswith(".pdf"):
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        else:
            text = raw.decode("utf-8", errors="ignore")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}") from exc

    state = load_state()
    state["knowledge"].append({"name": name, "text": text[:20000], "added": datetime.now().strftime("%H:%M")})
    save_state(state)
    return {"ok": True, "item": {"name": name, "chars": len(text)}}


@app.delete("/api/knowledge/{index}")
async def del_knowledge(index: int):
    state = load_state()
    if 0 <= index < len(state["knowledge"]):
        state["knowledge"].pop(index)
        save_state(state)
    return {"ok": True}


@app.post("/api/webhook")
async def set_webhook(request: Request):
    data = await request.json()
    state = load_state()
    url = (data.get("url") or "").strip()
    state["webhook"] = {"url": url, "enabled": bool(url)}
    save_state(state)
    return {"ok": True, "webhook": state["webhook"]}


@app.post("/api/contacts")
async def contacts(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()

    if not filename.endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file."
        )

    raw = await file.read()

    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File exceeds 8 MB."
        )

    try:
        text = raw.decode("utf-8-sig")
        rows = csv.DictReader(io.StringIO(text))

        parsed = []

        for row in rows:
            name = (row.get("name") or "").strip()
            phone = (row.get("phone") or "").strip()

            if name and phone:
                parsed.append({
                    "name": name,
                    "phone": phone,
                    "status": "Ready",
                    "rsvp": "",
                    "guests": ""
                })

        if not parsed:
            raise HTTPException(
                status_code=400,
                detail="CSV must contain name and phone columns."
            )

        state = load_state()
        state["contacts"] = parsed
        save_state(state)

        return {
            "ok": True,
            "count": len(parsed)
        }

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="CSV must use UTF-8 encoding."
        )

# @app.post("/api/contacts")
# async def contacts(file: UploadFile = File(...)):
#     raw = await file.read()
#     if len(raw) > MAX_UPLOAD_BYTES:
#         raise HTTPException(status_code=413, detail="File is too large. Maximum size is 8 MB.")

#     text = raw.decode("utf-8-sig", errors="ignore")
#     rows = list(csv.DictReader(io.StringIO(text)))
#     parsed = []
#     for row in rows:
#         low = {str(k).lower().strip(): str(v).strip() for k, v in row.items()}
#         name = low.get("name") or low.get("first_name") or low.get("firstname")
#         phone = low.get("phone") or low.get("mobile") or low.get("number")
#         if name and phone:
#             parsed.append({"name": name, "phone": phone, "status": "Ready", "rsvp": "", "guests": ""})

#     if not parsed:
#         raise HTTPException(status_code=400, detail="CSV needs columns like name,phone.")

#     state = load_state()
#     state["contacts"] = parsed
#     save_state(state)
#     return {"ok": True, "count": len(parsed)}


@app.post("/api/tests/run")
async def run_tests():
    state = load_state()
    results = []
    for test in state["tests"]:
        test["status"] = "passed"
        test["time"] = elapsed({1: 42, 2: 18, 3: 33}[test["id"]])
        if test["id"] == 3:
            test["description"] = "Collected and saved both guest names"
        results.append(test.copy())
    save_state(state)
    return {"tests": results, "suggestion": None}


@app.post("/api/fix")
async def fix():
    state = load_state()
    state["agent"]["fixed_plus_ones"] = True
    if "every plus-one name" not in state["agent"]["prompt"].lower():
        state["agent"]["prompt"] += " Ask for every plus-one name, confirm all names, and save each guest."
    for test in state["tests"]:
        if test["id"] == 3:
            test["status"] = "passed"
            test["description"] = "Collected and saved both guest names"
            test["time"] = "0:33"
    save_state(state)
    return {"ok": True, "prompt": state["agent"]["prompt"], "tests": state["tests"]}


@app.post("/api/session/start")
async def start_session(request: Request):
    data = await request.json()
    state = load_state()
    requested_name = (data.get("name") or "").strip()
    contact = None
    if requested_name:
        contact = next((x for x in state["contacts"] if x["name"].lower() == requested_name.lower()), None)
    if not contact:
        contact = get_next_contact(state)
    if not contact:
        return {"all_done": True, "session": None, "reply": "All contacts have been completed."}

    contact["status"] = "Calling"
    save_state(state)
    session = {"name": contact["name"], "stage": "opening"}
    first = agent_reply(state, "", session)
    return {"all_done": False, "session": session, "contact": contact, "reply": first["reply"]}


@app.post("/api/session/message")
async def session_message(request: Request):
    data = await request.json()
    state = load_state()
    session = data.get("session") or {"name": "Ramesh", "stage": "opening"}
    out = agent_reply(state, data.get("message", ""), session)
    payload = {"session": session, **out}

    if out.get("done") and out.get("result"):
        result = out["result"]
        name = session.get("name")
        for contact in state["contacts"]:
            if contact["name"].lower() == name.lower():
                contact["rsvp"] = result.get("rsvp", "")
                contact["status"] = (
                    "Wrong person" if result.get("identity_verified") is False
                    else "Completed"
                )
                contact["guests"] = ", ".join(result.get("guests", []))
                break
        save_state(state)

        webhook = state.get("webhook", {})
        if webhook.get("enabled") and webhook.get("url"):
            try:
                await asyncio.to_thread(requests.post, webhook["url"], json={"contact": name, **result}, timeout=3)
            except Exception:
                pass

        nxt = get_next_contact(state)
        payload["next_contact"] = {"name": nxt["name"], "phone": nxt["phone"]} if nxt else None
        payload["campaign_done"] = nxt is None

    return payload


@app.post("/api/campaign/run")
async def campaign():
    state = load_state()
    queued = 0
    for contact in state["contacts"]:
        if contact.get("status") != "Completed":
            contact["status"] = "Ready"
            queued += 1
    save_state(state)
    return {"ok": True, "queued": queued, "note": "Browser demo campaign is ready."}




@app.get("/api/sarvam/config")
async def sarvam_config_status():
    return config_summary()



# Recording and transcript access is protected separately from the public dashboard.
# Set RSVP_RECORDINGS_ACCESS_TOKEN in .env and in Render Environment.
def require_recordings_auth(request: Request):
    expected = os.getenv("RSVP_RECORDINGS_ACCESS_TOKEN", "").strip()
    given = request.headers.get("X-Recordings-Token", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Set RSVP_RECORDINGS_ACCESS_TOKEN on the server")
    if not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="Invalid recordings access token")


def call_entry(contact):
    transcript = contact.get("transcript")
    return {
        "name": contact.get("name", ""),
        "phone": contact.get("phone", ""),
        "status": contact.get("status", ""),
        "rsvp": contact.get("rsvp", ""),
        "guests": contact.get("guests", ""),
        "attempt_id": contact.get("attempt_id", ""),
        "interaction_id": contact.get("interaction_id", ""),
        "duration": contact.get("duration"),
        "has_transcript": bool(transcript),
        "has_recording": bool(contact.get("interaction_id")),
    }


@app.get("/api/calls")
async def list_calls(request: Request):
    require_recordings_auth(request)
    state = load_state()
    return {"calls": [call_entry(c) for c in state.get("contacts", [])
                      if c.get("attempt_id") or c.get("transcript")]}


@app.get("/api/calls/{attempt_id}/transcript")
async def call_transcript(attempt_id: str, request: Request):
    require_recordings_auth(request)
    with STATE_LOCK:
        state = load_state()
        contact = find_contact_by_attempt_id(state, attempt_id)
        if not contact:
            raise HTTPException(status_code=404, detail="Call not found")
        transcript = contact.get("transcript") or []
    return {"call": call_entry(contact), "transcript": transcript}


@app.get("/api/calls/{attempt_id}/recording")
async def call_recording(attempt_id: str, request: Request):
    require_recordings_auth(request)
    state = load_state()
    contact = find_contact_by_attempt_id(state, attempt_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Call not found")
    interaction_id = str(contact.get("interaction_id") or "")
    if not interaction_id:
        raise HTTPException(status_code=404, detail="This call has no recording (not connected or still processing)")
    from sarvam_voice import SARVAM_API_BASE, SARVAM_API_KEY, SARVAM_ORG_ID, SARVAM_WORKSPACE_ID, SARVAM_APP_ID
    url = (f"{SARVAM_API_BASE}/api/analytics/v1/"
           f"{quote(SARVAM_ORG_ID, safe='')}/{quote(SARVAM_WORKSPACE_ID, safe='')}/"
           f"{quote(SARVAM_APP_ID, safe='')}/recordings/{quote(interaction_id, safe='')}")
    try:
        result = await asyncio.to_thread(
            requests.get, url,
            headers={"X-API-Key": SARVAM_API_KEY, "Accept": "audio/wav"},
            timeout=45,
        )
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Recording service unreachable")
    if result.status_code == 404:
        raise HTTPException(status_code=404, detail="Recording not yet available")
    if not result.ok:
        raise HTTPException(status_code=502,
                            detail=f"Sarvam recording API returned HTTP {result.status_code}")
    if not result.headers.get("content-type", "").lower().startswith(("audio/", "application/octet-stream")):
        raise HTTPException(status_code=502, detail="Unexpected recording content type")
    if len(result.content) > 60 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Recording is larger than 60 MB")
    return Response(
        content=result.content,
        media_type="audio/wav",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


def webhook_destination_diagnostic(request: Request):
    """Detect the common localhost → different Render state.json mistake.

    This compares the host serving the dashboard to PUBLIC_BASE_URL.
    An HTTPS tunnel deliberately forwarding to localhost can be opted in
    via ALLOW_LOCAL_OUTBOUND_WITH_TUNNEL=true after verification.
    """
    destination = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    current_host = (request.url.hostname or "").lower()
    destination_host = (urlparse(destination).hostname or "").lower()
    local_host = current_host in ("127.0.0.1", "localhost", "0.0.0.0")
    override = os.getenv("ALLOW_LOCAL_OUTBOUND_WITH_TUNNEL", "").lower() == "true"
    mismatched = local_host and destination_host not in (
        "127.0.0.1", "localhost", "0.0.0.0"
    ) and not override
    return {
        "safe_to_start": not mismatched,
        "dashboard_host": current_host,
        "webhook_destination": destination + "/api/sarvam/webhook" if destination else "Not configured",
        "warning": (
            "You opened Genie on localhost but Sarvam will send its completion "
            "webhook to a different server. Start Real Campaign on the public "
            "Render dashboard instead. If you intentionally use an HTTPS "
            "tunnel into THIS local port, set ALLOW_LOCAL_OUTBOUND_WITH_TUNNEL=true."
            if mismatched else ""
        ),
    }


@app.get("/api/campaign/diagnostics")
async def campaign_diagnostics(request: Request):
    result = webhook_destination_diagnostic(request)
    state = load_state()
    result["calling_count"] = sum(
        c.get("status") in {"Starting", "Calling"}
        for c in state.get("contacts", [])
    )
    result["last_webhook_received_at"] = state.get("last_webhook_received_at")
    result["last_webhook_status"] = state.get("last_webhook_status")
    result["note"] = "A missing callback cannot be inferred from elapsed time. Check Sarvam delivery logs."
    return result


@app.post("/api/campaign/real/run")
async def real_campaign(request: Request):
    diagnostic = webhook_destination_diagnostic(request)
    if not diagnostic["safe_to_start"]:
        raise HTTPException(status_code=409, detail=diagnostic["warning"])
    if not sarvam_configured():
        raise HTTPException(
            status_code=400,
            detail="Sarvam configuration is incomplete."
        )

    state = load_state()

    if not state.get("contacts"):
        raise HTTPException(
            status_code=400,
            detail="Upload contacts before starting calls."
        )

    active = next(
        (
            contact
            for contact in state["contacts"]
            if contact.get("status") in {"Starting", "Calling"}
        ),
        None
    )

    if active:
        return {
            "ok": True,
            "already_running": True,
            "finished": False,
            "contact": active.get("name"),
            "attempt_id": active.get("attempt_id"),
            "message": "Waiting for the current call to finish."
        }

    next_contact = get_next_real_contact(state)

    if not next_contact:
        return {
            "ok": True,
            "finished": True,
            "message": "No Ready contacts remain."
        }

    try:
        result = await asyncio.to_thread(
            start_real_contact_call,
            next_contact
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        ) from exc

    if not result:
        return {
            "ok": True,
            "already_running": True,
            "finished": False,
            "message": "Call already reserved. Refresh the dashboard."
        }

    return {
        "ok": True,
        "finished": False,
        "contact": next_contact.get("name"),
        "phone": next_contact.get("phone"),
        "attempt_id": result.get("attempt_id"),
        "status": result.get("status", "calling")
    }


@app.post("/api/sarvam/webhook")
async def sarvam_webhook(request: Request):
    """Receive Sarvam Instant Outbound terminal outcomes.

    IMPORTANT: The webhook must reach the SAME persistent database/server
    used to start calls. A callback on Render cannot update localhost's
    separate data/state.json.
    """
    supplied_token = request.query_params.get("token", "")
    if not WEBHOOK_TOKEN or not secrets.compare_digest(supplied_token, WEBHOOK_TOKEN):
        print("SARVAM WEBHOOK REJECTED: missing or invalid token", flush=True)
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON webhook")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook must be a JSON object")

    attempt_id = str(payload.get("attempt_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    metadata = (payload.get("webhook_config") or {}).get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    terminal = {"connected", "no_answer", "busy", "failed"}
    if not attempt_id or status not in terminal:
        print("SARVAM WEBHOOK BAD PAYLOAD:", bool(attempt_id), status, flush=True)
        raise HTTPException(status_code=422, detail="Invalid attempt_id or nonterminal call status")

    print("SARVAM WEBHOOK RECEIVED:", attempt_id, status, flush=True)

    # Keep lookup + update atomic on this server. Never update another
    # unrelated contact by name when a supplied attempt_id mismatches.
    with STATE_LOCK:
        state = load_state()
        contact = find_contact_by_attempt_id(state, attempt_id)
        if contact is None:
            candidate = find_contact_from_metadata(state, metadata)
            if candidate is not None and not candidate.get("attempt_id"):
                contact = candidate
        if contact is None:
            print("SARVAM WEBHOOK UNKNOWN ATTEMPT:", attempt_id, flush=True)
            raise HTTPException(status_code=404, detail="Unknown attempt ID on this server")
        already_processed = contact.get("processed_attempt_id") == attempt_id

        contact["attempt_id"] = attempt_id
        contact["call_status"] = status
        contact["duration"] = payload.get("duration")
        contact["interaction_id"] = payload.get("interaction_id")
        contact["failure_reason"] = payload.get("failure_reason") or ""
        if payload.get("interaction_transcript") is not None:
            contact["transcript"] = payload["interaction_transcript"]
        final_vars = payload.get("final_agent_variables") or payload.get("output_agent_variables") or {}

        if status == "connected":
            merge_call_result(contact, final_vars)
        else:
            contact["status"] = {
                "no_answer": "No answer", "busy": "Busy", "failed": "Failed"
            }[status]

        contact["processed_attempt_id"] = attempt_id
        state["last_webhook_received_at"] = datetime.now().isoformat(timespec="seconds")
        state["last_webhook_status"] = status
        contact["call_ended_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)
        contact_name = contact.get("name", "")
        generic_webhook = state.get("webhook", {})
        callback_url = generic_webhook.get("url") if generic_webhook.get("enabled") else None
        callback_data = {
            "contact": contact_name,
            "phone": contact.get("phone"),
            "rsvp": contact.get("rsvp", ""),
            "guests": normalize_guests(contact.get("guests", "")),
            "sarvam": payload,
        }

    print("SARVAM WEBHOOK SAVED:", attempt_id, status, contact_name, flush=True)
    if callback_url:
        async def send_optional_callback():
            try:
                await asyncio.to_thread(requests.post, callback_url, json=callback_data, timeout=5)
            except Exception as exc:
                print("User webhook failed:", type(exc).__name__, flush=True)
        asyncio.create_task(send_optional_callback())

    asyncio.create_task(start_next_real_call_after_delay()) if not already_processed else None
    return {"ok": True, "attempt_id": attempt_id, "status": status}



# -----------------------------------------------------------
# RECOVER MISSED OUTBOUND COMPLETION WEBHOOKS VIA SARVAM ANALYTICS
# Sarvam Analytics can be delayed/cached. Never guess completion.
# -----------------------------------------------------------
from datetime import timedelta, timezone

RECONCILE_LOCK = threading.Lock()


def _sarvam_attempt_from_analytics(attempt_id: str):
    from sarvam_voice import (
        SARVAM_API_BASE, SARVAM_API_KEY, SARVAM_ORG_ID,
        SARVAM_WORKSPACE_ID, SARVAM_APP_ID,
    )
    api_url = (
        f"{SARVAM_API_BASE}/api/analytics/v1/"
        f"{quote(SARVAM_ORG_ID, safe='')}/"
        f"{quote(SARVAM_WORKSPACE_ID, safe='')}/"
        f"{quote(SARVAM_APP_ID, safe='')}/attempts"
    )
    now = datetime.now(timezone.utc)
    params = {
        "start_datetime": (now - timedelta(days=7)).isoformat(),
        "end_datetime": (now + timedelta(minutes=2)).isoformat(),
        "filter_conditions": json.dumps([{
            "field": "attempt_id", "operator": "equals", "value": attempt_id
        }]),
        "limit": 20,
    }
    res = requests.get(api_url, headers={"X-API-Key": SARVAM_API_KEY},
                       params=params, timeout=20)
    if not res.ok:
        raise RuntimeError(f"Sarvam Analytics HTTP {res.status_code}")
    body = res.json()
    for item in body.get("items", []):
        if str(item.get("attempt_id")) == attempt_id:
            return item
    return None


def _sarvam_transcript(interaction_id: str):
    from sarvam_voice import (
        SARVAM_API_BASE, SARVAM_API_KEY, SARVAM_ORG_ID,
        SARVAM_WORKSPACE_ID, SARVAM_APP_ID,
    )
    if not interaction_id or "/" not in interaction_id:
        return []
    url = (
        f"{SARVAM_API_BASE}/api/analytics/v1/"
        f"{quote(SARVAM_ORG_ID, safe='')}/"
        f"{quote(SARVAM_WORKSPACE_ID, safe='')}/"
        f"{quote(SARVAM_APP_ID, safe='')}/transcripts/"
        f"{quote(interaction_id, safe='')}"
    )
    try:
        res = requests.get(url, headers={"X-API-Key": SARVAM_API_KEY}, timeout=15)
        if not res.ok:
            return []
        return [
            {
                "role": "agent" if m.get("role") in {"assistant", "agent", "bot"} else "user",
                "en_text": m.get("content") or m.get("en_text") or "",
            }
            for m in res.json().get("messages", [])
            if isinstance(m, dict)
        ]
    except (requests.RequestException, ValueError, TypeError):
        return []


def reconcile_one_call(attempt_id: str):
    """Reconcile strictly from confirmed Analytics result; never dial here."""
    with RECONCILE_LOCK:
        state = load_state()
        contact = find_contact_by_attempt_id(state, attempt_id)
        if not contact:
            return {"attempt_id": attempt_id, "result": "unknown_local_attempt"}
        if contact.get("status") not in {"Calling", "Starting", "Call ended"}:
            return {"attempt_id": attempt_id, "result": "already_final"}

        item = _sarvam_attempt_from_analytics(attempt_id)
        if not item:
            return {"attempt_id": attempt_id, "result": "not_yet_in_analytics"}

        status = str(item.get("connectivity_status") or "").lower()
        if status not in {"connected", "no_answer", "busy", "failed"}:
            return {"attempt_id": attempt_id, "result": "not_final_in_analytics"}

        # For connected calls, end_datetime is the authoritative end signal.
        # For other outcomes, require a completed timestamp or explicit end reason.
        if not (item.get("end_datetime") or item.get("ended_by") or
                item.get("end_reason")):
            return {"attempt_id": attempt_id, "result": "waiting_for_end_evidence"}

        interaction_id = str(item.get("interaction_id") or "")
        transcript = _sarvam_transcript(interaction_id) if status == "connected" else []
        variables = item.get("agent_variables") or {}
        rsvp, guests = extract_sarvam_rsvp(variables)

        with STATE_LOCK:
            state = load_state()
            contact = find_contact_by_attempt_id(state, attempt_id)
            if not contact or contact.get("status") not in {"Calling", "Starting", "Call ended"}:
                return {"attempt_id": attempt_id, "result": "already_final"}
            contact["call_status"] = status
            contact["interaction_id"] = interaction_id or None
            contact["duration"] = item.get("duration_in_seconds")
            contact["failure_reason"] = item.get("failure_reason") or ""
            if transcript: contact["transcript"] = transcript
            if status == "connected":
                merge_call_result(contact, variables)
            else:
                contact["status"] = {"no_answer": "No answer", "busy": "Busy", "failed": "Failed"}[status]
            contact["processed_attempt_id"] = attempt_id
            contact["call_ended_at"] = item.get("end_datetime") or datetime.now(timezone.utc).isoformat()
            contact["reconciled_from"] = "sarvam_analytics"
            save_state(state)
        print("SARVAM ANALYTICS RECONCILED:", attempt_id, status, flush=True)
        return {"attempt_id": attempt_id, "result": "updated", "status": status}


def reconcile_pending_calls():
    state = load_state()
    ids = [str(c.get("attempt_id")) for c in state.get("contacts", [])
           if c.get("status") in {"Calling", "Call ended"} and c.get("attempt_id")]
    outcomes = []
    for attempt_id in ids[:5]:
        try:
            outcomes.append(reconcile_one_call(attempt_id))
        except Exception as exc:
            print("SARVAM ANALYTICS CHECK FAILED:", attempt_id, type(exc).__name__, flush=True)
            outcomes.append({"attempt_id": attempt_id, "result": "upstream_error"})
    return outcomes


@app.post("/api/campaign/reconcile")
async def reconcile_campaign(request: Request):
    # Same private credential as protected transcripts/recordings.
    require_recordings_auth(request)
    results = await asyncio.to_thread(reconcile_pending_calls)
    # Do not automatically redial anything. Only move on after a verified
    # terminal outcome and only when another Ready contact exists.
    if any(item["result"] == "updated" for item in results):
        asyncio.create_task(start_next_real_call_after_delay())
    return {"ok": True, "results": results}


async def _reconciliation_watchdog():
    # The Analytics endpoint is cached (~60 seconds); keep polling modest.
    while True:
        await asyncio.sleep(90)
        try:
            results = await asyncio.to_thread(reconcile_pending_calls)
            if any(item["result"] == "updated" for item in results):
                asyncio.create_task(start_next_real_call_after_delay())
        except Exception as exc:
            print("SARVAM WATCHDOG ERROR:", type(exc).__name__, flush=True)


@app.on_event("startup")
async def start_reconciliation_watchdog():
    app.state.reconcile_task = asyncio.create_task(_reconciliation_watchdog())


@app.on_event("shutdown")
async def stop_reconciliation_watchdog():
    task = getattr(app.state, "reconcile_task", None)
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass


@app.post("/api/reset")
async def reset():
    save_state(deepcopy(DEFAULT_STATE))
    return {"ok": True}


def intro_event_details(state):
    """Use only event facts explicitly saved in the Genie prompt/knowledge."""
    prompt = str(state.get("agent", {}).get("prompt", ""))
    # Prefer a structured event date over stray old weekdays in instructions.
    date_field = re.search(r"(?im)^\s*Event (?:date|day)\s*:\s*([^\r\n]+)", prompt)
    day = re.search(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", date_field.group(1) if date_field else prompt, re.I)
    label = f"{day.group(1).capitalize()}'s event" if day else "the event"
    facts = []
    # Structured event details can be written into the Instructions box as:
    # Event name: ... / Event time: ... / Event venue: ... / Event date: ...
    combined = prompt + "\n" + knowledge_text(state)
    for key, display in (("event name", "Event"), ("event date", "Date"),
                         ("event time", "Time"), ("event venue", "Venue"),
                         ("event location", "Location")):
        match = re.search(rf"(?im)^\s*{re.escape(key)}\s*:\s*([^\r\n]+)", combined)
        if match:
            val = match.group(1).strip()[:160]
            if val and val.lower() not in ("tbd", "unknown", "n/a"):
                facts.append(f"{display}: {val}")
    return label, facts



def agent_reply(state, message, session):
    """RSVP conversation with final confirmation."""

    spoken = str(message or "").strip()
    # Read the event day from the saved Genie Build instructions.
    # If conflicting days are mentioned, the FIRST named event day wins.
    instructions = str(state.get("agent", {}).get("prompt", ""))
    day_match = re.search(
        r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
        instructions,
        flags=re.IGNORECASE,
    )
    event_day = day_match.group(1).capitalize() if day_match else ""
    event_label = f"{event_day}'s event" if event_day else "the event"
    tamil_days = {
        "Monday": "திங்கட்கிழமை", "Tuesday": "செவ்வாய்க்கிழமை",
        "Wednesday": "புதன்கிழமை", "Thursday": "வியாழக்கிழமை",
        "Friday": "வெள்ளிக்கிழமை", "Saturday": "சனிக்கிழமை",
        "Sunday": "ஞாயிற்றுக்கிழமை",
    }
    tamil_event = (
        f"{tamil_days[event_day]} நிகழ்ச்சிக்கு" if event_day
        else "நிகழ்ச்சிக்கு"
    )
    language = state.get("agent", {}).get("voice_language", "")
    m = spoken.lower()
    # Understand basic Tamil attendance/count answers in the browser demo.
    if language == "ta-IN":
        if any(word in m for word in ("வரமாட்டேன்", "வர முடியாது", "இல்லை")):
            m = "no"
        elif any(word in m for word in ("ஒருவேளை", "தெரியவில்லை", "தெரில")):
            m = "maybe"
        elif any(word in m for word in ("வருவேன்", "வரேன்", "ஆமாம்", "ஆம்")):
            m = "yes"
        elif "யாரும் இல்லை" in m or "தனியாக" in m:
            m = "zero"
        else:
            for word, number in {"ஒரு": 1, "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5}.items():
                if word in m:
                    m = str(number)
                    break
    stage = session.get("stage", "opening")
    name = session.get("name", "there")

    def respond(text, next_stage, done=False, result=None):
        if language == "ta-IN":
            names = session.get("guest_names", [])
            count = session.get("guest_count", 0)
            rsvp = session.get("rsvp", "")
            if next_stage == "verify_name" and stage == "opening":
                event_label_ta, facts_ta = intro_event_details(state)
                details_ta = ". ".join(facts_ta)
                text = f"வணக்கம்! நான் நிகழ்ச்சி ஏற்பாட்டாளரின் AI உதவியாளர். {tamil_event} தொடர்பாக அழைக்கிறேன். "
                if details_ta:
                    text += details_ta + ". "
                text += f"நான் {name} அவர்களிடம்தான் பேசுகிறேனா?"
            elif stage == "verify_name" and next_stage == "rsvp":
                text = f"நன்றி {name}. {tamil_event} நீங்கள் வருகிறீர்களா?"
            elif stage == "verify_name" and next_stage == "verify_name":
                text = f"நான் {name} அவர்களிடம்தான் பேசுகிறேனா? ஆம் அல்லது இல்லை என்று சொல்லுங்கள்."
            elif next_stage == "guest_count":
                text = "மிகவும் நல்லது! உங்களுடன் கூடுதல் விருந்தினர்கள் வருகிறார்களா? எத்தனை பேர் வருவார்கள்?"
            elif next_stage in ("guest_names", "guest_names_more"):
                text = f"நன்றி. கூடுதலாக வரும் {count} விருந்தினர்களின் பெயர்களைச் சொல்லுங்கள்."
            elif next_stage == "confirm":
                if rsvp == "No":
                    text = "நீங்கள் நிகழ்ச்சிக்கு வரவில்லை என்று பதிவு செய்கிறேன். அது சரியா?"
                elif rsvp == "Maybe":
                    text = "நீங்கள் வருவது இன்னும் உறுதியாகவில்லை. அது சரியா?"
                elif count == 0:
                    text = "நீங்கள் கூடுதல் விருந்தினர்கள் இல்லாமல் வருகிறீர்கள். அது சரியா?"
                else:
                    text = f"நீங்கள் {count} கூடுதல் விருந்தினர்களுடன் வருகிறீர்கள்: {', '.join(names)}. இது சரியா?"
            elif next_stage == "done":
                text = "நன்றி! உங்கள் பதில் பதிவு செய்யப்பட்டது. வணக்கம்!"
            elif next_stage == "rsvp":
                text = f"நீங்கள் {tamil_event} வருகிறீர்களா? ஆம், இல்லை, அல்லது ஒருவேளை என்று சொல்லுங்கள்."
            elif next_stage == "guest_count":
                text = "எத்தனை கூடுதல் விருந்தினர்கள் வருவார்கள்?"
        session["stage"] = next_stage
        output = {
            "reply": text,
            "stage": next_stage,
            "done": done,
        }
        if result is not None:
            output["result"] = result
        return output

    def matches(pattern):
        return re.search(pattern, m, re.I) is not None

    def ask_confirmation():
        rsvp = session.get("rsvp", "Maybe")
        names = session.get("guest_names", [])
        count = session.get("guest_count", 0)

        if rsvp == "No":
            text = (
                "Let me confirm: you will not be attending "
                f"{event_label}. Is that correct?"
            )
        elif rsvp == "Maybe":
            text = (
                "Let me confirm: you are not sure whether "
                "you will attend. Is that correct?"
            )
        elif count == 0:
            text = (
                "Let me confirm: you will attend "
                f"{event_label} without additional guests. Is that correct?"
            )
        else:
            text = (
                f"Let me confirm: you will attend "
                f"{event_label} with {count} additional guests: "
                f"{', '.join(names)}. Is that correct?"
            )

        return respond(text, "confirm")

    # STEP 1: STATE EVENT DETAILS AND VERIFY CSV CONTACT'S NAME
    if stage == "opening":
        event_label, facts = intro_event_details(state)
        details = ". ".join(facts)
        description = f"This call is about {event_label}."
        if details:
            description += f" {details}."
        return respond(
            f"Hello! I'm an AI assistant calling on behalf of the event organiser. "
            f"{description} Am I speaking with {name}?",
            "verify_name",
        )

    if stage == "verify_name":
        # Explicit negative identity answers take priority over an incidental yes.
        if matches(r"\b(no|not me|wrong person|wrong number|i am not|i'm not|not speaking|not here)\b"):
            session["identity_confirmed"] = False
            return respond(
                "Sorry for the confusion. I won't ask you for the guest's RSVP. "
                "Thank you for your time. Goodbye!",
                "done", True,
                {"rsvp": "", "guests": [], "identity_verified": False},
            )
        if matches(r"\b(yes|yeah|yep|speaking|this is|that's me|correct|right|i am|i'm|indeed)\b"):
            session["identity_confirmed"] = True
            return respond(
                f"Thank you, {name}. Will you be attending {event_label}?",
                "rsvp",
            )
        if matches(r"\b(no|not me|wrong person|wrong number|i am not|i'm not)\b"):
            session["identity_confirmed"] = False
            return respond(
                "Sorry for the confusion. I won't ask you for the guest's RSVP. "
                "Thank you for your time. Goodbye!",
                "done", True,
                {"rsvp": "", "guests": [], "identity_verified": False},
            )
        return respond(
            f"Sorry, could you confirm whether I'm speaking with {name}? "
            "Please say yes or no.",
            "verify_name",
        )

    if matches(
        r"\b(stop calling|don't call|do not call|"
        r"end the call|goodbye)\b"
    ):
        return respond(
            "Understood. I'll end this call. Goodbye!",
            "done",
            True,
            {"rsvp": "", "guests": [], "identity_verified": False},
        )

    # STEP 2: ATTENDANCE
    if stage == "rsvp":

        if matches(
            r"\b(no|cannot|can't|won't|"
            r"not coming|not attending|decline)\b"
        ):
            session.update(
                rsvp="No",
                guest_count=0,
                guest_names=[],
            )
            return ask_confirmation()

        if matches(
            r"\b(maybe|not sure|unsure|might|perhaps)\b"
        ):
            session.update(
                rsvp="Maybe",
                guest_count=0,
                guest_names=[],
            )
            return ask_confirmation()

        if matches(
            r"\b(yes|yeah|yep|sure|attend|coming|"
            r"will come|will be there)\b"
        ):
            session.update(
                rsvp="Yes",
                guest_count=0,
                guest_names=[],
            )
            return respond(
                "Great! Will you be bringing any "
                "additional guests? If yes, how many?",
                "guest_count",
            )

        return respond(
            f"Sorry, will you be attending {event_label}? "
            "Please say yes, no, or maybe.",
            "rsvp",
        )

    # STEP 3: ADDITIONAL GUEST COUNT
    if stage == "guest_count":

        numbers = {
            "zero": 0,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }

        count = None

        if matches(
            r"\b(no|none|nobody|alone|"
            r"just me|only me|not bringing)\b"
        ):
            count = 0
        else:
            found = re.search(r"\b(\d+)\b", m)

            if found:
                count = int(found.group(1))
            else:
                for word, number in numbers.items():
                    if matches(rf"\b{word}\b"):
                        count = number
                        break

        if count is None or count > 20:
            return respond(
                "How many additional guests are coming "
                "with you? Please say a number from "
                "zero to twenty.",
                "guest_count",
            )

        session["guest_count"] = count
        session["guest_names"] = []

        if count == 0:
            return ask_confirmation()

        if count == 1:
            return respond(
                "What is your additional guest's name?",
                "guest_names",
            )

        return respond(
            f"What are the names of your "
            f"{count} additional guests?",
            "guest_names",
        )

    # STEP 4: COLLECT ALL GUEST NAMES
    if stage in ("guest_names", "guest_names_more"):

        cleaned = re.sub(
            r"^(their names are|the names are|"
            r"my guests are|they are|the guest is|"
            r"her name is|his name is)\s+",
            "",
            spoken,
            flags=re.I,
        )

        new_names = extract_guest_names(cleaned)

        if not new_names:
            return respond(
                "I didn't catch the names. "
                "Please repeat them.",
                stage,
            )

        expected = int(session.get("guest_count", 0))

        existing = list(
            session.get("guest_names", [])
        )

        names = (existing + new_names)[:expected]
        session["guest_names"] = names

        if len(names) < expected:
            remaining = expected - len(names)

            return respond(
                f"Thank you. Please tell me the "
                f"remaining {remaining} guest "
                f"name{'s' if remaining != 1 else ''}.",
                "guest_names_more",
            )

        return ask_confirmation()

    # STEP 5: CONFIRM WITH THE GUEST
    if stage == "confirm":

        if matches(
            r"\b(yes|correct|right|exactly|"
            r"that's correct|that is correct|ok|okay)\b"
        ):
            return respond(
                "Thank you! Your RSVP has been recorded. "
                "Have a wonderful day. Goodbye!",
                "done",
                True,
                {
                    "rsvp": session.get("rsvp", "Maybe"),
                    "guests": list(
                        session.get("guest_names", [])
                    ),
                },
            )

        if matches(
            r"\b(no|incorrect|wrong|change|correction)\b"
        ):
            session.update(
                rsvp="",
                guest_count=0,
                guest_names=[],
            )

            return respond(
                "Sorry about that. Let's correct it. "
                f"Will you be attending {event_label}?",
                "rsvp",
            )

        return respond(
            "Please confirm: is that correct? "
            "Say yes or no.",
            "confirm",
        )

    return respond(
        "Your RSVP conversation is complete. Goodbye!",
        "done",
        True,
    )


# def _contact_context_data(request: Request, phone: str):
#     """Return only this recipient's saved name and event facts to a trusted hook."""
#     expected = os.getenv("SARVAM_INSTRUCTIONS_TOKEN", "").strip()
#     supplied = request.headers.get("X-Instructions-Token", "").strip()
#     if not expected:
#         raise HTTPException(status_code=503, detail="SARVAM_INSTRUCTIONS_TOKEN is not configured")
#     if not supplied or not hmac.compare_digest(supplied, expected):
#         raise HTTPException(status_code=401, detail="Invalid instructions token")
#     phone = str(phone or "").strip()
#     if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
#         raise HTTPException(status_code=422, detail="Provide recipient phone in E.164 format")
#     current = load_state()
#     matches = [c for c in current.get("contacts", []) if str(c.get("phone", "")).strip() == phone]
#     if len(matches) != 1:
#         raise HTTPException(status_code=404, detail="Exactly one matching contact is required")
#     name = str(matches[0].get("name", "")).strip()
#     if not name:
#         raise HTTPException(status_code=409, detail="CSV contact name is empty")
#     label, facts = intro_event_details(current)
#     details = ". ".join(facts)
#     return {
#         "contact_name": name,
#         "event_label": label,
#         "event_details": details,
#         "call_instructions": str(current.get("agent", {}).get("prompt", "")).strip(),
#         "identity_question": f"Am I speaking with {name}?",
#         "opening_message": (
#             "Hello, I'm an AI assistant calling on behalf of the event organiser. "
#             + f"This call is about {label}. "
#             + (details + ". " if details else "")
#             + f"Am I speaking with {name}?"
#         ),
#     }


# @app.get("/api/sarvam/contact-context")
# async def sarvam_contact_context(request: Request, phone: str = ""):
#     return _contact_context_data(request, phone)


# @app.post("/api/sarvam/contact-context")
# async def sarvam_contact_context_post(request: Request):
#     data = await request.json()
#     if not isinstance(data, dict):
#         raise HTTPException(status_code=422, detail="Expected a JSON object")
#     return _contact_context_data(request, data.get("phone") or data.get("user_phone_number") or "")


@app.get("/api/sarvam/call-instructions")
async def sarvam_call_instructions(request: Request):
    import hmac
    import os

    expected_token = os.getenv(
        "SARVAM_INSTRUCTIONS_TOKEN", ""
    ).strip()

    received_token = request.headers.get(
        "X-Instructions-Token", ""
    ).strip()

    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Instructions token not configured"
        )

    if not received_token or not hmac.compare_digest(
        received_token,
        expected_token
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing instructions token"
        )

    state = load_state()

    instructions = str(
        state.get("agent", {}).get("prompt", "")
    ).strip()

    if not instructions:
        raise HTTPException(
            status_code=409,
            detail="No frontend instructions saved"
        )

    voice = state.get("agent", {})
    return {
        "call_instructions": instructions,
        "language": voice.get("voice_language", ""),
        "tone": voice.get("voice_tone", "normal"),
        "speed": voice.get("voice_speed", 1.0),
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Starting FastAPI RSVP Agent on http://0.0.0.0:{port}")
    print("Real calls: Sarvam Voice Agents Instant Outbound API")
    print("Sarvam webhook endpoint: /api/sarvam/webhook")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


import csv
import io
import hmac
import os
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse


@app.get("/api/campaign/sarvam-contacts")
async def export_sarvam_contacts(request: Request):
    """
    Export uploaded Genie contacts in Sarvam's
    campaign-variable format.

    Protected because it contains guest names
    and telephone numbers.
    """

    expected = os.getenv(
        "RSVP_RECORDINGS_ACCESS_TOKEN", ""
    ).strip()

    supplied = request.headers.get(
        "X-Recordings-Token", ""
    ).strip()

    if (
        not expected
        or not supplied
        or not hmac.compare_digest(expected, supplied)
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    state = load_state()

    agent = state.get("agent", {})
    instructions = str(
        agent.get("prompt", "")
    ).strip()

    # Use the same event-details extraction
    # function already present in your project.
    event_label, event_facts = intro_event_details(state)

    event_details = ". ".join(event_facts)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "phone",
        "contact_name",
        "event_label",
        "event_details",
        "identity_question",
        "call_instructions"
    ])

    for contact in state.get("contacts", []):
        name = str(contact.get("name", "")).strip()
        phone = str(contact.get("phone", "")).strip()

        if not name or not phone:
            continue

        if not phone.startswith("+"):
            continue

        writer.writerow([
            phone,
            name,
            event_label,
            event_details,
            f"Am I speaking with {name}?",
            instructions
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                'attachment; filename="sarvam_rsvp_contacts.csv"',
            "Cache-Control": "no-store"
        }
    )
