# Male / Female voice

Sarvam fixes the speaker inside each agent, and its outbound call API (app_overrides) only accepts
initial_bot_message, initial_state_name and initial_language_name - there is no per-call voice field.
So the dashboard Voice dropdown switches between two Sarvam agents:

1. In Sarvam, duplicate your RSVP shell agent. Set the voice of one to a female speaker (e.g. Ishita) and the other to a male speaker (e.g. Shubh). Same variables (call_instructions, opening_message). Commit each.
2. In the dashboard > Voice, paste the Female and Male agent IDs and versions, choose Female/Male, Save.

No .env values are needed. If the chosen gender has no agent pasted, the contact row warns and the default agent's voice is used.
