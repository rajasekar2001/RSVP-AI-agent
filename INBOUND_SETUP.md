# Incoming calls (people calling the AI back) - one-time Sarvam setup

When someone misses or cuts the AI's call and rings the AI number back, Sarvam answers with an
INBOUND agent. Create it once; afterwards the greeting, instructions, event facts and call-length
limit are edited in your dashboard (Build -> "Call limits & incoming calls").

## 1. Second agent in Sarvam (keep the outbound agent unchanged)
1. Duplicate your RSVP shell agent and name it e.g. `RSVP-Inbound`.
2. Input variables (send to LLM = ON): `call_instructions`, `opening_message`
   (optional: `contact_name`, `event_label`, `event_details`).
3. Greeting = `@opening_message`. Instructions = `@call_instructions`.
4. On-start hook (API tool, trigger = on start):
   - POST  `https://YOUR-PUBLIC-HOST/api/sarvam/inbound-context?token=<SARVAM_WEBHOOK_TOKEN>`
     (or header `X-Instructions-Token`; SARVAM_INSTRUCTIONS_TOKEN also works). The dashboard's
     "Check incoming-call setup" button prints these exact URLs.
   - Body `{"phone": <the CALLER's phone number variable from Sarvam's variable picker>}`
   - Map the response fields `opening_message`, `call_instructions`, `contact_name`,
     `event_label`, `event_details` to the input variables of the same names.
5. Output variables (optional): `rsvp`, `guest_names`. Without them the app reads the RSVP from
   the transcript.
6. Commit the agent.

## 2. Inbound deployment
Deploy -> Inbounds -> Create Inbound: pick `RSVP-Inbound` + the committed version, assign the same
phone number people were called from (or any number you give them), set availability hours.
Add the call-completion webhook:
   `https://YOUR-PUBLIC-HOST/api/sarvam/inbound-webhook?token=<SARVAM_WEBHOOK_TOKEN>`

## 3. What the app does
- Caller's number matches a CSV contact -> greeted by name, told the event details, asked for the
  RSVP if not recorded yet. Result is written to that contact (status Completed,
  "Called back (inbound)" badge).
- Unknown number -> event details only, no RSVP recorded; the call still appears in Call history
  and the report as "Unknown caller".
- Inbound calls show in Call Recordings & Transcripts (marked Inbound) and in the Report.

## 50-second limit (what is and is not enforced)
- The AI is told to finish within the limit set in the dashboard (default 50 s, 20-300), with
  short sentences, one question at a time and an early wrap-up.
- Sarvam has no API to hang up a call from this app, and its "Max call length" setting is in the
  agent's Conversation settings. For a hard cut-off also set it there (use the smallest value
  allowed) on BOTH agents. Calls longer than the dashboard limit are flagged
  "over limit" in the table, call history and report.

## Call-back drops after 2-5 seconds with silence? Checklist
The caller hears nothing and the call ends when the AI never speaks first. Work through:
1. **Inbound deployment exists** for the number people ring (Deploy -> Inbounds). The outbound
   connection alone does NOT answer incoming calls.
2. **The agent speaks first**: greeting must be `@opening_message` (not empty) and the agent must
   be set to start the conversation. Give `opening_message` a default value in Sarvam's input
   variables so the greeting is never empty even if the hook is slow.
3. **The hook is reachable**: PUBLIC_BASE_URL must be https and public (not localhost/127.0.0.1).
   Ring the number, then press "Check incoming-call setup" in Build:
   - "Sarvam has called the on-start hook" = ticked -> Sarvam reached your app.
   - not ticked + "WRONG token" -> fix the token in the hook URL.
   - not ticked and nothing else -> the call died before the hook: check items 1 and 2.
4. The hook now never errors: if anything breaks it still returns a safe greeting
   ("I called you earlier but you were not able to take the call. Are you free to talk now?").
5. Keep Sarvam's silence/idle timeout at its default or higher on the inbound agent.

## Silent call that "attends" then hangs up after ~3 seconds
The call IS answered by Sarvam, but the agent has nothing to say, so both sides stay silent and it
times out. In the INBOUND agent:
1. Greeting = `@opening_message`.
2. Input variables `opening_message` and `call_instructions`: set their DEFAULT VALUE to the text shown
   under "Check incoming-call setup" -> "Safety defaults". Then the AI always speaks, even when the
   hook fails or the variable mapping is wrong.
3. Make sure the agent is set to speak first (not wait for the caller).
4. Commit a new version and re-select that version in the Inbound deployment.

## Automatic retries (outgoing)
Dashboard -> Build -> "Automatic retry for unanswered calls": retry after 60 min (5-1440), max attempts
(1-10, default 5), only inside the call hours (default 09:00-20:00, this computer's clock).
Retries happen for busy / no answer / failed calls only, one call at a time. They stop when the
person answers, gives an RSVP, calls the AI back, or the max attempts is reached. Retry calls open with
the "Missed-call greeting": "Hello {name}! ... I called you earlier but you were not able to take the call.
Are you free to talk now?" Retries start after you press Real Campaign once, and need the app
(and its public URL) to stay running.
