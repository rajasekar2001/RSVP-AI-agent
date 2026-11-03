# Make the live AI call introduce the event and confirm the CSV name

This FastAPI project initiates **Sarvam instant outbound calls**. Sarvam's outbound API accepts the recipient's phone number, but does **not** accept a per-call greeting or agent variables. The webhook `metadata.contact_name` is only returned *after* the call; it cannot personalize the live conversation.

Therefore, configure the **committed Sarvam agent** once, using the existing authenticated FastAPI contact-context endpoint as an **on-start** API tool. Changes made only to the Genie/FastAPI prompt do not alter the live Sarvam agent.

## 1. Upload your actual contacts and event information

Use the dashboard to upload a CSV with one contact per number:

```csv
name,phone
Raja,+919876543210
Bhushan,+919876543211
Ganesun,+919876543212
```

Use real telephone numbers when dialing. In the dashboard's agent instructions, include structured event facts, for example:

```
Event name: Family Celebration
Event date: Saturday, 14 November 2026
Event time: 6:30 PM
Event venue: Community Hall, Chennai
```

You can also upload an event brief containing `Date:`, `Time:`, `Venue:` and `Address:` lines.

## 2. Set a secret for the endpoint

Set `SARVAM_INSTRUCTIONS_TOKEN` in the backend's environment to a long random secret. Do **not** expose this token in a frontend or public link. Keep `PUBLIC_BASE_URL` pointed at your publicly accessible HTTPS backend. Restart/redeploy the backend after changing environment variables.

The lookup is `POST https://YOUR-BACKEND/api/sarvam/contact-context`.

Header: `X-Instructions-Token: YOUR_SECRET` (configure as a secret in Sarvam).

Body: `{ "phone": "<recipient phone mapped from Sarvam's outbound contact data>" }`.

The phone value must be the **recipient's exact E.164 number**, not the agent's number. Verify in Sarvam's tool Test panel that the selected outbound-call variable really contains it. Sarvam documents User Identifier primarily for inbound calls; do not assume it contains the outbound recipient's number. If no outbound recipient number is exposed for instant calls, use a **Sarvam CSV campaign** instead (step 5).

Successful response, unique for each phone:

```json
{
  "contact_name": "Raja",
  "event_label": "Family Celebration",
  "event_details": "Date: Saturday, 14 November 2026. Time: 6:30 PM. Venue: Community Hall, Chennai",
  "identity_question": "Am I speaking with Raja?",
  "opening_message": "Hello, I'm an AI assistant calling on behalf of the event organiser. I'm calling about Family Celebration. Date: Saturday, 14 November 2026. Time: 6:30 PM. Venue: Community Hall, Chennai. Am I speaking with Raja?",
  "identity_status": "unconfirmed",
  "call_instructions": "..."
}
```

## 3. Configure the committed Sarvam agent

1. Under **Variables**, create input variables `contact_name`, `event_label`, `event_details`, `opening_message`, `identity_question`, `call_instructions`. Keep these in agent context. Mark name/phone personal data where possible.
2. Under **Tools**, create an authenticated HTTP API tool named `load_rsvp_contact`, configure it to run **on_start**, and point it to the lookup endpoint above. Map the returned fields into their matching variables using **Save reply into variables**. Test it with a real CSV contact's phone number. Do not start a live call if mapping or lookup fails.
3. Under **Instruction → Greeting**, insert the `opening_message` variable using Sarvam's **@ variable picker**. It must be a real variable chip, not literal text like `@opening_message`. This is the spoken first turn. Add translations if the agent supports multiple languages.
4. Under **Instructions**, insert the `call_instructions` variable using the picker. Include the fallback below as an explicit rule.
5. **Commit** the agent to a new version and set `SARVAM_APP_VERSION` to that committed version in the FastAPI environment. The running backend always calls the configured version, not the uncommitted draft.

Recommended fallback instructions: "If `contact_name` or `opening_message` is missing, do not guess a name or request attendance. Say you cannot verify the invitation details and end politely. Do not treat an answer of 'yes' to the name question as an RSVP."

## 4. Expected call sequence

**AI:** Hello, I'm an AI assistant calling on behalf of the event organiser. I'm calling about Family Celebration. The event is on Saturday, 14 November at 6:30 PM at Community Hall, Chennai. **Am I speaking with Raja?**

**Person:** Yes.

**AI:** Thank you, Raja. **Will you attend the event?**

**Person:** Yes.

**AI:** Will you bring any additional guests? If so, how many?

**Person:** Two.

**AI:** What are the names of both guests?

**Person:** Bhushan and Ganesun.

**AI:** So Raja is attending with Bhushan and Ganesun. Is that correct?

The AI should **not** ask "Are you Raja or Bhushan or Ganesun?" on every call. These are three separate CSV contacts; the question must use the **one name associated with that phone number**.

## 5. Alternative: run a Sarvam CSV campaign

If the instant outbound call does not expose the recipient phone to on-start tools, download `/api/campaign/sarvam-contacts` using its configured authorization token. The export now contains `opening_message` and full `call_instructions` for each contact. In Sarvam's campaign creator, upload that CSV and map its columns to identically named agent variables. Set the Greeting to the `opening_message` variable, test a row, and launch the campaign from Sarvam. Do **not** also launch the same contacts with FastAPI's instant-call button, to avoid double calls.

Reference docs:

- https://docs.sarvam.ai/voice-agents/api/instant-outbound/create
- https://docs.sarvam.ai/voice-agents/build/variables-personalization
- https://docs.sarvam.ai/voice-agents/build/tools/https-tool
- https://docs.sarvam.ai/voice-agents/deploy/campaigns/create-campaign

**Important:** This code supplies personalization context. Sarvam must be configured and tested as described above before any live call can reliably speak the desired opening.
