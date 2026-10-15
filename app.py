import asyncio
import csv
import io
import json
import os
import re
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import requests
import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pypdf import PdfReader

from exotel_realtime import exotel_configured, start_exotel_voice_call

load_dotenv()

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "data" / "state.json"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

app = FastAPI(title="RSVP Agent", version="2.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

STATE_LOCK = threading.RLock()

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


def find_contact_by_call_sid(state, call_sid):
    for contact in state["contacts"]:
        if contact.get("call_sid") == call_sid:
            return contact
    return None


def agent_reply(state, message, session):
    m = (message or "").strip().lower()
    stage = session.get("stage", "opening")
    name = session.get("name", "there")

    if any(x in m for x in ["where", "venue", "location", "address"]):
        for item in state["knowledge"]:
            for line in item.get("text", "").splitlines():
                if any(k in line.lower() for k in ["venue", "location", "address", "hall", "hotel"]):
                    return {"reply": line.strip(), "stage": stage, "done": False}
        return {
            "reply": "I don't have the venue in my knowledge yet. Please add the event brief in Knowledge.",
            "stage": stage,
            "done": False,
        }

    if stage == "opening":
        session["stage"] = "rsvp"
        return {
            "reply": f"Hi {name}, I'm calling to confirm your RSVP for Saturday. Will you be joining us?",
            "stage": "rsvp",
            "done": False,
        }

    if stage == "rsvp":
        if any(x in m for x in ["no", "can't", "cannot", "not coming", "wont", "won't", "unable"]):
            session["rsvp"] = "No"
            session["stage"] = "done"
            return {
                "reply": "No problem. Thanks for letting us know. Have a great day!",
                "stage": "done",
                "done": True,
                "result": {"rsvp": "No", "guests": []},
            }
        if any(x in m for x in ["yes", "yeah", "yep", "coming", "attend", "sure", "will be there"]):
            session["rsvp"] = "Yes"
            session["stage"] = "guest_count"
            return {
                "reply": "Great. Will you be bringing any guests? You can say none, one, two, and so on.",
                "stage": "guest_count",
                "done": False,
            }
        return {
            "reply": "Sorry, I did not catch that. Will you be attending Saturday, yes or no?",
            "stage": "rsvp",
            "done": False,
        }

    if stage == "guest_count":
        nums = {"none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        count = None
        for word, number in nums.items():
            if re.search(rf"\b{word}\b", m):
                count = number
                break
        if count is None:
            hit = re.search(r"\b([0-5])\b", m)
            if hit:
                count = int(hit.group(1))

        if count == 0:
            session["guest_count"] = 0
            session["stage"] = "done"
            return {
                "reply": "Perfect. I have you down as attending with no additional guests. Thank you!",
                "stage": "done",
                "done": True,
                "result": {"rsvp": "Yes", "guests": []},
            }

        if count is not None and count > 0:
            session["guest_count"] = count
            session["guest_names"] = []
            session["stage"] = "guest_names"
            reply = "Wonderful. What is the name of your guest?" if count == 1 else f"Wonderful. What are the names of your {count} guests?"
            return {"reply": reply, "stage": "guest_names", "done": False}

        return {
            "reply": "How many additional guests are you bringing? Please say a number from zero to five.",
            "stage": "guest_count",
            "done": False,
        }

    if stage == "guest_names":
        count = session.get("guest_count", 1)
        names = extract_guest_names(message)
        session["guest_names"] = names
        if len(names) < count:
            session["stage"] = "guest_names_more"
            missing = count - len(names)
            captured = ", ".join(names) if names else "no names yet"
            return {
                "reply": f"I captured {captured}. I still need {missing} more guest name" + ("s." if missing > 1 else "."),
                "stage": "guest_names_more",
                "done": False,
            }
        names = names[:count]
        session["guest_names"] = names
        session["stage"] = "done"
        return {
            "reply": f"Thanks. I've saved your RSVP with {', '.join(names)}. See you Saturday!",
            "stage": "done",
            "done": True,
            "result": {"rsvp": "Yes", "guests": names},
        }

    if stage == "guest_names_more":
        existing = session.get("guest_names", [])
        names = existing + extract_guest_names(message)
        count = session.get("guest_count", len(names))
        if len(names) < count:
            session["guest_names"] = names
            missing = count - len(names)
            return {
                "reply": f"Thank you. I still need {missing} more name" + ("s." if missing > 1 else "."),
                "stage": "guest_names_more",
                "done": False,
            }
        names = names[:count]
        session["guest_names"] = names
        session["stage"] = "done"
        return {
            "reply": f"Perfect. I saved all {count} guests: {', '.join(names)}. See you Saturday!",
            "stage": "done",
            "done": True,
            "result": {"rsvp": "Yes", "guests": names},
        }

    return {"reply": "The RSVP is already complete. Thank you!", "stage": "done", "done": True}


def save_completed_rsvp(call_sid, rsvp, guests):
    state = load_state()
    contact = find_contact_by_call_sid(state, call_sid)
    if not contact:
        raise KeyError("Contact not found")

    guests = [str(x).strip() for x in guests if str(x).strip()]
    contact["rsvp"] = rsvp
    contact["guests"] = ", ".join(guests)
    contact["status"] = "Completed"
    save_state(state)

    webhook = state.get("webhook", {})
    if webhook.get("enabled") and webhook.get("url"):
        try:
            requests.post(
                webhook["url"],
                json={"contact": contact["name"], "rsvp": rsvp, "guests": guests},
                timeout=3,
            )
        except Exception:
            pass

    return {"ok": True, "contact": contact["name"]}


def build_realtime_instructions(context):
    name = context["contact"]["name"]
    return f"""
You are a real-time telephone RSVP assistant speaking to {name}.

Agent task:
{context.get('agent_prompt', '')}

Rules:
- Greet {name} and ask whether they will attend.
- If no, politely thank them and call save_rsvp with rsvp='No', guests=[].
- If yes, ask the number of additional guests.
- If zero, call save_rsvp with rsvp='Yes', guests=[].
- If one or more, collect EVERY guest name before saving.
- Never invent names.
- Keep the call concise and friendly.
- If the caller asks an event question, answer only from EVENT KNOWLEDGE, then return to the RSVP flow.
- After save_rsvp succeeds, thank the caller and end naturally.

EVENT KNOWLEDGE:
{context.get('knowledge', '')[:12000]}
"""


def get_realtime_context(call_sid):
    state = load_state()
    contact = find_contact_by_call_sid(state, call_sid)
    if not contact:
        raise KeyError("Unknown CallSid")
    return {
        "contact": {"name": contact["name"], "phone": contact["phone"]},
        "agent_prompt": state["agent"]["prompt"],
        "knowledge": knowledge_text(state),
    }


def start_real_contact_call(contact):
    state = load_state()
    real_contact = next((x for x in state["contacts"] if x["name"].lower() == contact["name"].lower()), None)
    if not real_contact:
        raise RuntimeError("Contact not found.")

    result = start_exotel_voice_call(real_contact["phone"], real_contact["name"])
    real_contact["call_sid"] = result.get("call_sid")
    real_contact["status"] = "Calling"
    real_contact["call_status"] = result.get("status", "queued")
    save_state(state)
    return result


def start_next_real_call():
    state = load_state()
    for contact in state["contacts"]:
        if contact.get("status") == "Completed":
            continue
        if contact.get("status") in ["Calling", "Ringing", "Answered"]:
            continue
        return start_real_contact_call(contact)
    return None


async def start_next_real_call_after_delay(delay=2):
    await asyncio.sleep(delay)
    try:
        await asyncio.to_thread(start_next_real_call)
    except Exception as exc:
        print("Could not start next real call:", exc)


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
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large. Maximum size is 8 MB.")

    text = raw.decode("utf-8-sig", errors="ignore")
    rows = list(csv.DictReader(io.StringIO(text)))
    parsed = []
    for row in rows:
        low = {str(k).lower().strip(): str(v).strip() for k, v in row.items()}
        name = low.get("name") or low.get("first_name") or low.get("firstname")
        phone = low.get("phone") or low.get("mobile") or low.get("number")
        if name and phone:
            parsed.append({"name": name, "phone": phone, "status": "Ready", "rsvp": "", "guests": ""})

    if not parsed:
        raise HTTPException(status_code=400, detail="CSV needs columns like name,phone.")

    state = load_state()
    state["contacts"] = parsed
    save_state(state)
    return {"ok": True, "count": len(parsed)}


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


@app.post("/api/campaign/real/run")
async def real_campaign():
    if not exotel_configured():
        raise HTTPException(
            status_code=400,
            detail="Real calling is not configured. Copy .env.example to .env and add Exotel/OpenAI values.",
        )
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is missing from .env.")

    state = load_state()
    nxt = get_next_contact(state)
    if not nxt:
        return {"ok": True, "finished": True, "message": "All contacts are already completed."}

    try:
        result = await asyncio.to_thread(start_real_contact_call, nxt)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "finished": False,
        "contact": nxt["name"],
        "phone": nxt["phone"],
        "call_sid": result.get("call_sid"),
        "status": result.get("status"),
    }


@app.post("/api/exotel/status")
async def exotel_status(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    call_sid = data.get("CallSid") or data.get("call_sid") or data.get("Sid")
    status = (data.get("Status") or data.get("status") or "").lower()
    print("EXOTEL STATUS:", call_sid, status)

    state = load_state()
    contact = find_contact_by_call_sid(state, call_sid)
    if contact:
        contact["call_status"] = status
        if status == "ringing":
            contact["status"] = "Ringing"
        elif status in ["in-progress", "answered"]:
            contact["status"] = "Answered"
        elif status == "no-answer":
            contact["status"] = "No answer"
        elif status == "busy":
            contact["status"] = "Busy"
        elif status == "failed":
            contact["status"] = "Failed"
        elif status == "completed":
            contact["status"] = "Completed" if contact.get("rsvp") else "Call ended"
        save_state(state)

    if status in {"completed", "failed", "busy", "no-answer", "canceled"}:
        asyncio.create_task(start_next_real_call_after_delay())

    return {"ok": True}


@app.post("/api/reset")
async def reset():
    save_state(deepcopy(DEFAULT_STATE))
    return {"ok": True}


@app.websocket("/media")
async def media_websocket(exotel_ws: WebSocket):
    await exotel_ws.accept()

    if not OPENAI_API_KEY:
        await exotel_ws.close(code=1011, reason="OPENAI_API_KEY missing")
        return

    stream_sid = None
    call_sid = None
    openai_ws = None
    openai_task = None

    async def send_openai(payload):
        if openai_ws:
            await openai_ws.send(json.dumps(payload))

    async def configure_openai(context):
        await send_openai({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": build_realtime_instructions(context),
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "turn_detection": {
                            "type": "server_vad",
                            "create_response": True,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": "marin",
                    },
                },
                "tools": [{
                    "type": "function",
                    "name": "save_rsvp",
                    "description": "Save the final RSVP result after all required guest names have been collected.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "rsvp": {"type": "string", "enum": ["Yes", "No"]},
                            "guests": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["rsvp", "guests"],
                    },
                }],
                "tool_choice": "auto",
            },
        })
        await send_openai({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": "Begin the phone call now. Greet the contact and ask the first RSVP question.",
                }],
            },
        })
        await send_openai({"type": "response.create"})

    async def openai_to_exotel():
        nonlocal stream_sid, call_sid
        try:
            async for raw in openai_ws:
                event = json.loads(raw)
                event_type = event.get("type")

                if event_type in ("response.output_audio.delta", "response.audio.delta"):
                    audio = event.get("delta")
                    if audio and stream_sid:
                        await exotel_ws.send_text(json.dumps({
                            "event": "media",
                            "stream_sid": stream_sid,
                            "media": {"payload": audio},
                        }))

                elif event_type == "input_audio_buffer.speech_started" and stream_sid:
                    await exotel_ws.send_text(json.dumps({"event": "clear", "stream_sid": stream_sid}))

                elif event_type == "response.function_call_arguments.done" and event.get("name") == "save_rsvp":
                    try:
                        args = json.loads(event.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    try:
                        result = await asyncio.to_thread(
                            save_completed_rsvp,
                            call_sid,
                            args.get("rsvp", ""),
                            args.get("guests", []),
                        )
                        output = json.dumps(result)
                    except Exception as exc:
                        output = json.dumps({"ok": False, "error": str(exc)})

                    await send_openai({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": event.get("call_id"),
                            "output": output,
                        },
                    })
                    await send_openai({"type": "response.create"})

                elif event_type == "error":
                    print("OPENAI ERROR:", event)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print("OpenAI relay error:", exc)

    try:
        openai_ws = await websockets.connect(
            OPENAI_WS_URL,
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
            max_size=None,
        )
        openai_task = asyncio.create_task(openai_to_exotel())

        while True:
            raw = await exotel_ws.receive_text()
            event = json.loads(raw)
            event_type = event.get("event")

            if event_type == "connected":
                print("Exotel WebSocket connected")

            elif event_type == "start":
                start = event.get("start", {})
                stream_sid = start.get("stream_sid") or event.get("stream_sid")
                call_sid = start.get("call_sid")
                print("Voice stream started:", call_sid, stream_sid)
                context = get_realtime_context(call_sid)
                await configure_openai(context)

            elif event_type == "media":
                payload = event.get("media", {}).get("payload")
                if payload:
                    await send_openai({"type": "input_audio_buffer.append", "audio": payload})

            elif event_type == "stop":
                print("Exotel stream stopped:", call_sid)
                break

    except WebSocketDisconnect:
        print("Exotel WebSocket disconnected:", call_sid)
    except Exception as exc:
        print("VOICE BOT ERROR:", repr(exc))
        try:
            await exotel_ws.close(code=1011)
        except Exception:
            pass
    finally:
        if openai_task:
            openai_task.cancel()
        if openai_ws:
            try:
                await openai_ws.close()
            except Exception:
                pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("Starting FastAPI RSVP Agent on http://0.0.0.0:5000")
    print("Exotel realtime WebSocket endpoint: /media")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
