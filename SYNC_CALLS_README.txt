FIX FOR CALLING STATUS AFTER A FINISHED CALL

The existing webhook is still the primary path. This build also checks Sarvam Analytics
for a finished attempt and updates ONLY the existing attempt when verified.

1. Deploy all files to Render (including app.py, static/app.js, templates/index.html).
2. Ensure Render Environment sets your existing SARVAM_API_KEY, IDs,
   SARVAM_WEBHOOK_TOKEN and RSVP_RECORDINGS_ACCESS_TOKEN. Do not commit .env.
3. On the Render dashboard, go to Campaigns > Call Recordings & Transcripts.
4. Click Unlock call history, enter RSVP_RECORDINGS_ACCESS_TOKEN.
5. Click Sync finished calls. The backend securely queries Sarvam Analytics.
6. It also automatically checks active calls roughly every 90 seconds while running.

Analytics data can lag at least 60 seconds. If the attempt is not available yet,
look in Render logs and Sarvam Monitor > Call Logs.

For robust production persistence, migrate state.json to Postgres and ensure one scheduler.
Calls are never marked complete merely because time elapsed.
