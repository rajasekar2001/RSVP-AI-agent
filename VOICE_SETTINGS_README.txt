ADDITIVE VOICE SETTINGS UPDATE

Files changed: app.py, static/app.js, templates/index.html.
Original campaign, Sarvam API module, RSVP tests, and webhook logic are retained.

HOW TO RUN
1. Copy your existing .env into the extracted project folder (never commit or share this file).
2. Keep your original data/state.json only if you want saved contacts/results carried over. Otherwise app creates its defaults.
3. pip install -r requirements.txt
4. python app.py
5. Open the displayed local port, Build -> Voice preferences.
6. Select Language, Tone, Speed, then Save changes.

No language selection: browser keeps English (en-IN), matching prior behavior.
Tamil: browser demo greeting and RSVP stage messages are localized into Tamil; speech recognition handles several simple Tamil responses. More advanced multilingual recognition/conversation requires a language-capable AI dialog model.
Other language choices: browser speech synthesis/recognition locale changes; the existing hardcoded English browser script is not automatically translated.
Tone: browser pitch approximation only. Speed: browser text-to-speech playback rate.

Real Sarvam calls: The authenticated /api/sarvam/call-instructions endpoint now includes language, tone, speed in JSON, alongside call_instructions. Applying a runtime language or rate to the Sarvam telephony agent requires supported Sarvam-side mapping/configuration; this update does not pretend the Instant Outbound API has an undocumented per-call speed/prompt field. Sarvam Greeting remains separate.

SECURITY: Uploaded source archive contained an .env with secrets and state.json with guest data. These are intentionally OMITTED. Revoke any secrets previously shared publicly. Avoid placing .env in Git.
