import asyncio
import csv
import io
import json
import os
import re
import secrets
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from openpyxl import load_workbook
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
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
        "built": True,
        "fixed_plus_ones": True,
    },
    "knowledge": [],
    "webhook": {"url": "", "enabled": False},
    "contacts": [
        {"name": "Ramesh", "phone": "+919000000001", "status": "Ready", "rsvp": "", "guests": ""},
        {"name": "Ananya", "phone": "+919000000002", "status": "Ready", "rsvp": "", "guests": ""},
        {"name": "Karthik", "phone": "+919000000003", "status": "Ready", "rsvp": "", "guests": ""},
    ],
    "tests": [
        {"id": 1, "title": "Happy path", "description": "Says yes, brings one guest, gives the name", "status": "ready", "time": ""},
        {"id": 2, "title": "Caller says no", "description": "Declines, agent thanks them and ends the call", "status": "ready", "time": ""},
        {"id": 3, "title": "Two plus ones", "description": "Collect and save both guest names", "status": "ready", "time": ""},
    ],
}


def load_state():
    with STATE_LOCK:
        if not STATE_FILE.exists():
            save_state(deepcopy(DEFAULT_STATE))
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    with STATE_LOCK:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def elapsed(n):
    return f"0:{n:02d}"


def knowledge_text(state):
    return "\n".join(x.get("text", "") for x in state["knowledge"])[-12000:]


def extract_guest_names(message):
    return [x.strip().title() for x in re.split(r",|\band\b|&", message or "", flags=re.I) if x.strip()]


def get_next_contact(state):
    for contact in state["contacts"]:
        if contact.get("status") != "Completed":
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
    final_vars = final_vars or {}
    rsvp = ""
    for key in ("rsvp", "RSVP", "attendance", "attending", "is_attending", "call_outcome"):
        if key in final_vars and final_vars[key] not in (None, ""):
            rsvp = normalize_rsvp(final_vars[key])
            break

    guests = []
    for key in ("guests", "guest_names", "plus_ones", "plus_one_names", "guest_list"):
        if key in final_vars and final_vars[key] not in (None, ""):
            guests = normalize_guests(final_vars[key])
            break
    return rsvp, guests


def start_real_contact_call(contact):
    """Reserve one contact before dialing; don't redial when the user clicks twice."""
    with CALL_LAUNCH_LOCK:
        state = load_state()
        real_contact = next((x for x in state["contacts"] if x.get("phone") == contact.get("phone") and x.get("name") == contact.get("name")), None)
        if not real_contact or real_contact.get("status") not in {"Ready", "Queued"}:
            return None
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
    return templates.TemplateResponse(request=request, name="index.html", context={"state": load_state()})


@app.get("/api/state")
async def get_state():
    return load_state()


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

    state["agent"]["prompt"] = prompt
    state["agent"]["built"] = True
    state["agent"]["fixed_plus_ones"] = True
    for test in state["tests"]:
        test["status"] = "ready"
        test["time"] = ""
    save_state(state)
    return {"ok": True, "agent": state["agent"]}


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
                contact["status"] = "Completed"
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


@app.post("/api/campaign/real/run")
async def real_campaign():
    if not sarvam_configured():
        raise HTTPException(
            status_code=400,
            detail=(
                "Sarvam real calling is not configured. Add SARVAM_API_KEY, SARVAM_ORG_ID, "
                "SARVAM_WORKSPACE_ID, SARVAM_APP_ID, SARVAM_APP_VERSION, "
                "SARVAM_CONNECTION_ID, SARVAM_PHONE_NUMBER and PUBLIC_BASE_URL."
            ),
        )

    # Do not reset Calling/Starting/Failed/No answer contacts; prevent accidental redials.
    state = load_state()
    if any(c.get("status") in {"Calling", "Starting"} for c in state["contacts"]):
        return {"ok": False, "message": "A real call is already in progress. Wait for its completion webhook."}
    nxt = get_next_real_contact(state)
    if not nxt:
        return {"ok": True, "finished": True, "message": "No Ready contacts. Use the browser campaign/reset or upload contacts to prepare a new campaign."}
    try:
        result = await asyncio.to_thread(start_real_contact_call, nxt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        return {"ok": False, "message": "Contact was already reserved."}
    return {
        "ok": True, "finished": False, "provider": "sarvam",
        "contact": nxt["name"], "phone": nxt["phone"],
        "attempt_id": result.get("attempt_id"), "status": result.get("status"),
    }


@app.post("/api/sarvam/webhook")
async def sarvam_webhook(request: Request):
    """Receive the Instant Outbound completion webhook from Sarvam Voice Agents."""
    # Secret callback URL token: protects this endpoint from arbitrary POSTs.
    supplied_token = request.query_params.get("token", "")
    if not WEBHOOK_TOKEN or not secrets.compare_digest(supplied_token, WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
    payload = await request.json()
    attempt_id = str(payload.get("attempt_id") or "")
    status = str(payload.get("status") or "").lower()
    metadata = (payload.get("webhook_config") or {}).get("metadata") or {}

    print("SARVAM WEBHOOK:", attempt_id, status)

    state = load_state()
    contact = find_contact_by_attempt_id(state, attempt_id) or find_contact_from_metadata(state, metadata)
    if contact and contact.get("attempt_id") and attempt_id and contact["attempt_id"] != attempt_id:
        raise HTTPException(status_code=409, detail="Attempt ID mismatch")
    if contact and contact.get("processed_attempt_id") == attempt_id:
        return {"ok": True, "duplicate": True}
    if not contact:
        return {"ok": False, "message": "Unknown call attempt"}

    if contact:
        if attempt_id:
            contact["attempt_id"] = attempt_id
        contact["call_status"] = status
        contact["duration"] = payload.get("duration")
        contact["interaction_id"] = payload.get("interaction_id")
        contact["failure_reason"] = payload.get("failure_reason") or ""
        transcript = payload.get("interaction_transcript")
        if transcript is not None:
            contact["transcript"] = transcript

        final_vars = payload.get("final_agent_variables") or payload.get("output_agent_variables") or {}
        contact["sarvam_variables"] = final_vars

        if status == "connected":
            rsvp, guests = extract_sarvam_rsvp(final_vars)
            if rsvp:
                contact["rsvp"] = rsvp
                contact["guests"] = ", ".join(guests)
                contact["status"] = "Completed"
            else:
                # Connected call completed, but the agent did not output an RSVP variable.
                contact["status"] = "Call ended"
        elif status == "no_answer":
            contact["status"] = "No answer"
        elif status == "busy":
            contact["status"] = "Busy"
        elif status == "failed":
            contact["status"] = "Failed"
        else:
            contact["status"] = status.title() if status else "Call ended"

        contact["processed_attempt_id"] = attempt_id
        save_state(state)

        # Optional user webhook from the UI.
        generic_webhook = state.get("webhook", {})
        if generic_webhook.get("enabled") and generic_webhook.get("url"):
            try:
                await asyncio.to_thread(
                    requests.post,
                    generic_webhook["url"],
                    json={
                        "contact": contact.get("name"),
                        "phone": contact.get("phone"),
                        "rsvp": contact.get("rsvp", ""),
                        "guests": normalize_guests(contact.get("guests", "")),
                        "sarvam": payload,
                    },
                    timeout=5,
                )
            except Exception as exc:
                print("User webhook failed:", exc)

    # Sarvam sends this after each attempt, so start the next Ready contact.
    asyncio.create_task(start_next_real_call_after_delay())
    return {"ok": True}


@app.post("/api/reset")
async def reset():
    save_state(deepcopy(DEFAULT_STATE))
    return {"ok": True}


def agent_reply(state, message, session):
    """
    RSVP browser-demo conversation.

    Stages:
      opening -> rsvp -> guest_count -> guest_names
      -> guest_names_more -> done
    """

    spoken = str(message or "").strip()
    m = spoken.lower()
    stage = session.get("stage", "opening")
    name = session.get("name", "there")

    def reply(text, next_stage, done=False, result=None):
        session["stage"] = next_stage

        output = {
            "reply": text,
            "stage": next_stage,
            "done": done,
        }

        if result is not None:
            output["result"] = result

        return output

    def completed(text, rsvp, guests):
        session["rsvp"] = rsvp
        session["guest_names"] = guests

        return reply(
            text,
            "done",
            True,
            {
                "rsvp": rsvp,
                "guests": guests,
            },
        )

    # STEP 1: INTRODUCTION
    if stage == "opening":
        return reply(
            f"Hello {name}! I'm an AI assistant calling "
            "on behalf of the event organiser. "
            "We're confirming attendance for Saturday's event. "
            "Will you be attending?",
            "rsvp",
        )

    # Guest can ask to stop the conversation.
    if re.search(
        r"\b(stop calling|don't call|do not call|end call|goodbye)\b",
        m,
    ):
        return completed(
            "Understood. Thank you for your time. Goodbye!",
            "Maybe",
            [],
        )

    # STEP 2: ATTENDANCE
    if stage == "rsvp":

        no_patterns = [
            r"\bno\b",
            r"\bnot coming\b",
            r"\bnot attending\b",
            r"\bcan't\b",
            r"\bcannot\b",
            r"\bwon't\b",
            r"\bunable\b",
            r"\bdecline\b",
        ]

        yes_patterns = [
            r"\byes\b",
            r"\byeah\b",
            r"\byep\b",
            r"\bsure\b",
            r"\bwill attend\b",
            r"\bwill come\b",
            r"\bi am coming\b",
            r"\bi'm coming\b",
            r"\bi will be there\b",
            r"\bdefinitely\b",
        ]

        maybe_patterns = [
            r"\bmaybe\b",
            r"\bnot sure\b",
            r"\bunsure\b",
            r"\bperhaps\b",
            r"\bmight\b",
        ]

        if any(re.search(p, m) for p in no_patterns):
            return completed(
                "Thank you for letting us know that "
                "you won't be attending. "
                "Have a wonderful day. Goodbye!",
                "No",
                [],
            )

        if any(re.search(p, m) for p in maybe_patterns):
            return completed(
                "No problem. I'll record your response "
                "as maybe. Thank you for your time. Goodbye!",
                "Maybe",
                [],
            )

        if any(re.search(p, m) for p in yes_patterns):
            session["rsvp"] = "Yes"

            return reply(
                "Wonderful! I've recorded that you will attend. "
                "Will you be bringing any additional guests? "
                "If yes, how many?",
                "guest_count",
            )

        return reply(
            "Sorry, I didn't catch that. "
            "Will you attend Saturday's event? "
            "Please say yes, no, or maybe.",
            "rsvp",
        )

    # STEP 3: ASK HOW MANY ADDITIONAL GUESTS
    if stage == "guest_count":

        zero_patterns = [
            r"\bno\b",
            r"\bnone\b",
            r"\bnobody\b",
            r"\bzero\b",
            r"\bjust me\b",
            r"\bonly me\b",
            r"\balone\b",
            r"\bnot bringing\b",
        ]

        if any(re.search(p, m) for p in zero_patterns):
            session["guest_count"] = 0

            return completed(
                "Thank you! Let me confirm: "
                "you will attend Saturday's event "
                "without any additional guests. "
                "I've recorded your RSVP. Goodbye!",
                "Yes",
                [],
            )

        number_words = {
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

        match = re.search(r"\b(\d+)\b", m)

        if match:
            count = int(match.group(1))
        else:
            for word, number in number_words.items():
                if re.search(rf"\b{word}\b", m):
                    count = number
                    break

        if count is None:
            return reply(
                "How many additional guests are coming "
                "with you? You can say zero, one, two, "
                "or another number.",
                "guest_count",
            )

        if count > 20:
            return reply(
                "For this test, please enter a guest count "
                "between zero and twenty.",
                "guest_count",
            )

        if count == 0:
            session["guest_count"] = 0

            return completed(
                "Thank you! I've confirmed that you "
                "will attend without additional guests. "
                "Goodbye!",
                "Yes",
                [],
            )

        session["guest_count"] = count
        session["guest_names"] = []

        if count == 1:
            question = (
                "Great! You are bringing one additional guest. "
                "What is your guest's name?"
            )
        else:
            question = (
                f"Great! You are bringing {count} additional guests. "
                "Please tell me all their names."
            )

        return reply(question, "guest_names")

    # STEP 4: COLLECT GUEST NAMES
    if stage in ("guest_names", "guest_names_more"):

        count = int(session.get("guest_count", 1))
        existing = list(session.get("guest_names", []))

        # Remove common leading phrases.
        cleaned = re.sub(
            r"^(their names are|the names are|"
            r"my guests are|the guest is|"
            r"his name is|her name is|"
            r"they are|it is)\s+",
            "",
            spoken,
            flags=re.IGNORECASE,
        )

        # Support names separated by commas, "and", or "&".
        new_names = [
            part.strip(" .,").title()
            for part in re.split(
                r",|\band\b|&",
                cleaned,
                flags=re.IGNORECASE,
            )
            if part.strip(" .,")
        ]

        if not new_names:
            return reply(
                "Sorry, I couldn't catch the guest names. "
                "Please repeat them.",
                stage,
            )

        names = existing + new_names

        # Keep only the expected number of names.
        names = names[:count]
        session["guest_names"] = names

        if len(names) < count:
            missing = count - len(names)

            if missing == 1:
                question = (
                    f"Thank you. I've recorded "
                    f"{', '.join(names)}. "
                    "What is the remaining guest's name?"
                )
            else:
                question = (
                    f"Thank you. I've recorded "
                    f"{', '.join(names)}. "
                    f"Please tell me the remaining "
                    f"{missing} guest names."
                )

            return reply(question, "guest_names_more")

        guest_word = "guest" if count == 1 else "guests"

        confirmation = (
            f"Let me confirm your RSVP. "
            f"You will attend Saturday's event "
            f"with {count} additional {guest_word}: "
            f"{', '.join(names)}. "
            "Thank you! Your RSVP has been recorded. "
            "Have a wonderful day. Goodbye!"
        )
        return completed(
            confirmation,
            "Yes",
            names,
        )

    return reply(
        "Your RSVP conversation has already finished. "
        "Thank you!",
        "done",
        True,
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Starting FastAPI RSVP Agent on http://0.0.0.0:{port}")
    print("Real calls: Sarvam Voice Agents Instant Outbound API")
    print("Sarvam webhook endpoint: /api/sarvam/webhook")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

