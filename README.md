# RSVP AI Agent — FastAPI + Sarvam Voice Agents

This version removes Twilio/Exotel/OpenAI realtime plumbing from the application. Real telephone calls are placed through **Sarvam Voice Agents Instant Outbound API**. Browser Chat/Voice testing still works locally inside the UI.

## Architecture

Browser demo:

```text
Browser -> FastAPI -> local RSVP state machine -> RSVP sheet
```

Real telephone campaign:

```text
Contacts in FastAPI
    -> Sarvam Instant Outbound API
    -> Sarvam Voice Agent + telephony connection
    -> Customer mobile phone
    -> Sarvam completes conversation
    -> Sarvam POSTs completion webhook to FastAPI
    -> RSVP/result saved in state.json
    -> FastAPI automatically starts next Ready contact
```

There is **no `/media` WebSocket** in this project and no `OPENAI_API_KEY`, `TWILIO_*`, or `EXOTEL_*` configuration.

## What you need in Sarvam

Before Real Campaign can work, configure these in the Sarvam Voice Agents dashboard:

1. **Create an RSVP Voice Agent** and commit a version.
2. In the agent, make sure the final agent variables include:
   - `rsvp` — preferably `Yes` or `No`
   - `guests` — preferably a list of guest names
3. Go to **Deploy -> Phone Numbers**.
4. Either:
   - Rent a number from Sarvam, or
   - Connect an existing telephony provider.
5. Copy the connection ID and outbound phone number.
6. Create a workspace API key.
7. Copy your organisation ID and workspace ID.

### Recommended Sarvam agent instructions

Use a prompt similar to:

```text
You are an RSVP calling assistant.

Ask whether the person will attend the event.

If NO:
- set rsvp = "No"
- set guests = []
- thank them and end politely.

If YES:
- set rsvp = "Yes"
- ask how many additional guests they are bringing.
- if zero, set guests = [].
- if one or more, collect every guest name.
- never invent guest names.
- save every guest name in guests.

Before ending, confirm the RSVP briefly.
```

If your Sarvam agent uses different output-variable names, update `extract_sarvam_rsvp()` in `app.py`.

## Environment variables

Copy `.env.example` to `.env` for local development:

```env
SARVAM_API_KEY=your_workspace_api_key
SARVAM_ORG_ID=your_org_id
SARVAM_WORKSPACE_ID=your_workspace_id
SARVAM_APP_ID=your_agent_id
SARVAM_APP_VERSION=1
SARVAM_CONNECTION_ID=your_connection_id
SARVAM_PHONE_NUMBER=+9180XXXXXXXX
PUBLIC_BASE_URL=https://your-app.onrender.com
```

### Where to find IDs

- Organisation ID: **Settings -> Organisation -> General**
- Workspace ID: **Settings -> Workspace -> General**
- Agent ID: copy from your Voice Agent in Sarvam
- Agent version: use a committed version number
- Connection ID / phone number: **Deploy -> Phone Numbers**
- API key: create a workspace API key in Sarvam settings

## Install and run locally

```bash
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

Browser Chat/Voice mode works locally. **Real Sarvam calls require a public HTTPS URL** because Sarvam must POST the completion webhook back to this app.

## Render deployment

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Then set Render Environment variables to your real Sarvam values and set:

```env
PUBLIC_BASE_URL=https://YOUR-RENDER-SERVICE.onrender.com
```

Do not commit `.env` to GitHub.

## Real campaign behavior

Click **Campaigns -> Real Campaign**.

FastAPI sends the first Ready contact to Sarvam:

```text
POST https://apps.sarvam.ai/api/outbounds/v1/orgs/{org_id}/workspaces/{workspace_id}/outbounds
```

Sarvam returns an `attempt_id`. The contact becomes `Calling`.

After the attempt finishes, Sarvam POSTs to:

```text
POST /api/sarvam/webhook
```

The webhook may contain:

- call status (`connected`, `no_answer`, `busy`, `failed`)
- duration
- interaction ID
- transcript
- final agent variables

The application stores these fields and, when `rsvp` is present, marks the contact `Completed`. It then waits briefly and starts the next Ready contact automatically.

## Important: local Build/Knowledge vs Sarvam real agent

The **Build**, **Knowledge**, and browser **Test Agent** features in this project are still local/demo features. They are useful for developing the RSVP flow in the web app.

A real Sarvam telephone call runs the committed **Sarvam Voice Agent** identified by:

```env
SARVAM_APP_ID
SARVAM_APP_VERSION
```

Therefore, make the real Sarvam agent prompt/knowledge match what you want in production. Uploaded PDF knowledge in this local web app is not automatically uploaded into Sarvam's own Knowledge Base.

## API routes

```text
GET  /                         Web UI
GET  /api/state                Current local state
POST /api/build                Save local demo instructions
POST /api/knowledge            Upload local demo PDF/TXT knowledge
POST /api/contacts             Import CSV contacts
POST /api/session/start        Start browser test session
POST /api/session/message      Continue browser test session
POST /api/campaign/run         Prepare browser demo campaign
POST /api/campaign/real/run    Start Sarvam real-call sequence
GET  /api/sarvam/config        Check non-secret Sarvam configuration
POST /api/sarvam/webhook       Sarvam instant-outbound completion webhook
POST /api/reset                Reset local demo state
```

## Contact CSV

Use E.164 phone numbers:

```csv
name,phone
Raja,+919876543210
Ananya,+919812345678
```

## Test safely

For the first real-call test:

1. Upload only one number you control and have permission to call.
2. Check `/api/sarvam/config` and confirm `configured` is `true`.
3. Click **Real Campaign**.
4. Watch Render logs.
5. After the call ends, refresh Campaigns and check status, RSVP, and guests.

## Sarvam documentation used by this integration

- Instant Outbound API: https://docs.sarvam.ai/voice-agents/api/instant-outbound/create
- Instant Outbound webhook: https://docs.sarvam.ai/voice-agents/api/instant-outbound/webhook-payload
- Phone numbers / telephony: https://docs.sarvam.ai/voice-agents/deploy/telephony
- Rent from Sarvam: https://docs.sarvam.ai/voice-agents/deploy/telephony/rent-from-sarvam
