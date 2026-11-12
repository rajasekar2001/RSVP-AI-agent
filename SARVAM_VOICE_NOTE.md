# Male / Female voice

Sarvam fixes the speaker inside each agent, and its outbound call API (app_overrides) only accepts
initial_bot_message, initial_state_name and initial_language_name - there is no per-call voice field.
So the dashboard Voice dropdown switches between two Sarvam agents:

1. In Sarvam, duplicate your RSVP shell agent. Set the voice of one to a female speaker (e.g. Ishita) and the other to a male speaker (e.g. Shubh). Same variables (call_instructions, opening_message). Commit each.
2. In the dashboard > Voice, paste the Female and Male agent IDs and versions, choose Female/Male, Save.

No .env values are needed. If the chosen gender has no agent pasted, the contact row warns and the default agent's voice is used.

## Greeting and instructions come from the dashboard, not Sarvam
Each agent you use (Female, Male, and the incoming-call agent) must be a "shell" in Sarvam:
- Input variables `call_instructions` and `opening_message` (Send to LLM = ON)
- Greeting = `@opening_message`
- Instructions = `@call_instructions` (nothing else)
- Commit, then paste the new version number in the dashboard.

A duplicated agent that still has its old script reads that script instead. The app now refuses to place the call
(and tells you which variable is missing) rather than let the AI speak the wrong script.

Incoming call-backs: the incoming agent must also be a shell and its on-start hook must call
`/api/sarvam/inbound-context` (see INBOUND_SETUP.md). Then the AI says the dashboard "missed-call greeting"
and answers event questions from the event details typed in the dashboard.
