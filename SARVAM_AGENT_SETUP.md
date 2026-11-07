# One-time Sarvam setup (about 5 minutes, never again)

Sarvam has no API to edit an agent's prompt, so the phone agent is a thin "shell"
that simply reads what YOUR dashboard sends on every call. After this setup you
change greeting, instructions, language, tone, speed and event details only in
your RSVP dashboard (Build tab) - no more Sarvam dashboard edits.

## In Sarvam (once)
1. Input variables (send to LLM = ON): `call_instructions`, `opening_message`
   (optional extras: `contact_name`, `event_label`, `event_details`).
2. Greeting: just `@opening_message`.
3. Instructions: just `@call_instructions` (nothing else).
4. Remove any on_start API hook.
5. Output variables: `rsvp` (Yes/No/Maybe/Unknown) and `guest_names` (JSON array string of
   additional guest names, [] if none). Extraction prompts: see SETUP.md section 3.
6. Commit the agent, put the version in SARVAM_APP_VERSION, restart the app.

## What the dashboard controls on every call
| Dashboard setting | Sent to Sarvam as |
|---|---|
| Greeting (placeholders {name} {event} {date} {time} {venue}) | app_overrides.initial_bot_message |
| Language | app_overrides.initial_language_name |
| Instructions + Tone + Speed + event lines | agent_variables.call_instructions |
| CSV contact name | agent_variables.contact_name / {name} |

Use "Preview what the phone agent will receive" in Build to see the exact text.

## Limits (Sarvam side)
- The actual voice (speaker) and true audio speed are agent-level settings in Sarvam and
  cannot be changed per call by API. Tone and speed are applied as speaking-style
  instructions. Pick the voice once in Sarvam.
- Language must be supported by the agent's voice/STT in Sarvam.

## Male / Female voice (dashboard: Build -> Voice)
A call's speaker is fixed by the Sarvam agent, so create the shell agent twice (same variables,
greeting `@opening_message`, instructions `@call_instructions`) and set the speaker once:
- Female agent: speaker `ishita`   - Male agent: speaker `shubh`   (both Bulbul v3; `varun` is male but dramatic)
Commit each, then put them in .env:
  SARVAM_APP_ID_FEMALE / SARVAM_APP_VERSION_FEMALE, SARVAM_APP_ID_MALE / SARVAM_APP_VERSION_MALE
(version is optional; it falls back to SARVAM_APP_VERSION.) Choose Default / Female / Male in the
dashboard and each call is placed with the matching agent. If the chosen one is not configured the
default agent is used and the contact row shows a warning.
Inbound call-backs use the voice of your inbound agent (set its speaker in Sarvam; add
SARVAM_APP_ID_INBOUND to .env so inbound transcripts/recordings can be fetched).
The instructions also tell the AI which grammatical gender to use (matters in Hindi/Tamil).
