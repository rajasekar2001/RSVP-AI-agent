# RSVP / CSV identity fix

Replace app.py and static/app.js in your existing project. Keep .env, data/state.json,
sarvam_voice.py, templates and other files. Back up state.json before replacing code.

IMPORTANT: Real phone calls execute the committed Sarvam agent, not Python's
agent_reply function. The following platform configuration is required once.

## 1. Event information
Save actual facts in your frontend Instructions (Build Agent) or uploaded text:
Event name: Your actual event name
Event date: Your actual date
Event time: Your actual time
Event venue: Your actual venue
Upload your CSV with name and phone columns. Phone must be +countrycode format.

## 2. Sarvam on-start hook (before the greeting)
Configure POST https://YOUR-PUBLIC-HOST/api/sarvam/contact-context
Header: X-Instructions-Token: the value of SARVAM_INSTRUCTIONS_TOKEN in .env
Body: {"phone": "<the CURRENT outbound recipient phone system variable>"}
Select the actual recipient phone variable from Sarvam's variable picker; do not
send the agent phone, a hardcoded number, webhook metadata, or the literal text above.
Map response fields to input variables:
contact_name, event_label, event_details, opening_message, call_instructions.
Enable these input variables in LLM context.

Greeting: insert opening_message using Sarvam's @ variable picker.
Instructions: insert call_instructions using the @ picker and add:
Speak the opening message with event details first. Ask whether the recipient is
contact_name. WAIT. Identity yes does not mean attending. If wrong person, end
politely without recording RSVP. Once confirmed ask whether attending, then the
number of ADDITIONAL guests (excluding invitee), and every guest's name. Read back
the result and ask for corrections. Use only supplied event facts. Never guess.

Example for a CSV row named Raja:
Hello, I'm an AI assistant calling on behalf of the event organiser. This call is
about [event]. [actual date, time, venue]. Am I speaking with Raja?
Only say Raja for that row, Bhushan for Bhushan's row, Ganesun for Ganesun's row.

## 3. Sarvam output variables (extracted AFTER conversation)
rsvp: Enum Yes, No, Maybe, Unknown.
Extraction prompt: Extract the invitee's final attendance decision only AFTER
identity verification. Do not interpret yes to the identity question as attendance.
Yes = explicitly attending, No = explicitly declining, Maybe = explicitly unsure,
Unknown = wrong person, disconnected before answering, or no attendance answer.

guest_names: String.
Extraction prompt: Return a JSON array string of ALL explicitly provided additional
guest names, excluding the invitee. Preserve spelling; never invent names. Return
[] for explicitly no additional guests, declined attendance or names not provided.

Ensure the on-end/completion webhook sends these in final_agent_variables or
output_agent_variables to the existing token-protected /api/sarvam/webhook URL.
Commit the agent and update SARVAM_APP_VERSION in .env to that committed version.
Restart Python. Changing the Python code alone cannot update a hosted agent.

## 4. Recover the already-ended call
Use Call History -> Unlock -> enter RSVP_RECORDINGS_ACCESS_TOKEN -> Sync Sarvam Calls.
Calls already marked Call ended are now eligible. The background checker also
retries every 90 seconds. Late webhook results can now enrich a previous result.
If Sarvam never extracted outputs for the old call, the code cannot reconstruct
an accurate RSVP automatically; check its transcript and configure outputs before
the next call. A connected call is never automatically counted as attending.

## Validation
Python syntax checked. Result parsing and merge regression checks passed locally.
Live Sarvam calls were not placed; verify with one consenting test contact after
committing the platform configuration. This patch does not include credentials.
