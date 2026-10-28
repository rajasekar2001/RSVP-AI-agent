# Get CSV names into REAL Sarvam calls (required one-time setup)

The local Genie browser test uses the uploaded CSV name automatically. **Instant Outbound does not document an inline greeting, variable, or prompt override.** The Python code alone cannot make a committed Sarvam phone agent say the name.

1. Deploy this code to Render and import CSV contacts **on Render**, not localhost. Save event information through Genie Instructions on Render, e.g. `Event name: Community Dinner`, `Event date: Friday`, `Event time: 6 PM`, `Event venue: Town Hall`.
2. Add a strong `SARVAM_INSTRUCTIONS_TOKEN` to Render Environment, separately from the Sarvam API key.
3. In Sarvam Voice Agents, create input variables `contact_name`, `event_label`, `event_details`, `identity_question`, `call_instructions`, `opening_message`.
4. Add an API tool with **on_start** trigger. Configure a GET to `https://rsvp-ai-agent.onrender.com/api/sarvam/contact-context?phone=<outbound-contact-phone>` or POST to `https://rsvp-ai-agent.onrender.com/api/sarvam/contact-context` with JSON `{"phone":"<outbound-contact-phone>"}`. Use `X-Instructions-Token` as a secure header. `phone` must be filled by an **actual supported runtime outbound recipient-phone/campaign variable**, not a hardcoded number. Check Sarvam tool variable picker and test with two numbers. If no such variable is available to Instant Outbound, this technique cannot automatically resolve the recipient: use Sarvam outbound campaign CSV variable mapping or contact Sarvam support for supported per-call agent-variable injection. **Do not assume webhook metadata is sent to the agent.**
5. Map response fields to variables. Use Sarvam's variable picker in the agent's Instructions to implement: first provide event details, ask `@identity_question`, WAIT for Yes/No, stop if wrong person, and only if confirmed ask RSVP and plus-one details. A fixed Greeting may play before this on-start-generated dialogue; set a neutral base Greeting and commit a new version.
6. Test the hook independently: send a private header (do not paste it into chat) with PowerShell `Invoke-RestMethod -Uri "https://rsvp-ai-agent.onrender.com/api/sarvam/contact-context?phone=%2B91XXXXXXXXXX" -Headers @{ "X-Instructions-Token" = $token }`. Then inspect the Sarvam real call transcript; make sure the actual name was spoken before proceeding.
7. Update Render's `SARVAM_APP_VERSION` to the exact committed version after saving Sarvam configuration.

Security: never expose the entire CSV to the agent, never put secrets in JavaScript, and use consent for automated calls.
