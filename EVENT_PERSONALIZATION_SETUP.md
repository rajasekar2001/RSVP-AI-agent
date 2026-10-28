# Event introduction + CSV-name confirmation

## Browser test
Each session now introduces the event and asks whether it is speaking to that **one** CSV recipient before asking RSVP questions. A wrong-person response never records an attendance RSVP. Event facts are read only from explicit lines in Genie instructions or the Knowledge brief: `Event name: ...`, `Event date: ...`, `Event time: ...`, `Event venue: ...`.

## Real Sarvam telephone calls: one-time configuration
The Instant Outbound endpoint accepts agent/version, phone and webhook data; it does not provide a documented per-call override of the greeting. To personalize, in Sarvam Voice Agents create input variables `contact_name`, `event_label`, `event_details`, `call_instructions`, and `identity_question`. Add an **on_start HTTP tool** calling `https://YOUR-RENDER-HOST/api/sarvam/contact-context?phone=<CURRENT_OUTBOUND_RECIPIENT_PHONE>` using your *actual supported on-start outbound recipient phone variable*, and add `X-Instructions-Token` from a secure secret. Map response fields into those variables. Test the phone substitution in Sarvam's tool tester: a phone that is not explicitly mapped cannot be guessed. The default first greeting should be short and neutral, then the agent must first state the event details, verify `@contact_name`, stop RSVP if not the correct person, and only then ask attendance. Commit the agent and update SARVAM_APP_VERSION. Do not insert unsupported fields into Instant Outbound request JSON.

For strict event-first behavior, place per-contact variables in the supported greeting (if variable substitution works with your configuration) or verify the agent's first generated turn occurs after the on_start hook. A pre-recorded fixed greeting cannot be replaced by this FastAPI endpoint alone.

IMPORTANT: Don't repeat names of other CSV contacts, don't disclose invitee information to the wrong person, and obtain consent for automated calling/recording.
