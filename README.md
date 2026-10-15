# RSVP Agent — FastAPI single-process version

This version replaces Flask and the separate `voice_bot.py` server with **one FastAPI application**.

## One command

```bash
python app.py
```

That one process serves:

- Frontend HTML/CSS/JavaScript
- Browser RSVP demo APIs
- Knowledge PDF/TXT upload
- Contacts CSV upload
- Exotel status callback
- Real Campaign API
- Exotel realtime audio WebSocket at `/media`
- OpenAI realtime audio bridge
- RSVP saving and automatic next-contact calling

## Install

```bash
pip install -r requirements.txt
```

## Browser demo

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Real calling

Copy `.env.example` to `.env` and enter your own Exotel and OpenAI credentials.

Your public endpoints must resolve to the **same FastAPI server**:

```text
PUBLIC_BASE_URL=https://your-domain.com
PUBLIC_WS_URL=wss://your-domain.com/media
```

Then run only:

```bash
python app.py
```

You do **not** run `voice_bot.py` in this version.

## Project structure

```text
rsvp_agent_fastapi_single/
├── app.py
├── exotel_realtime.py
├── requirements.txt
├── .env.example
├── sample_contacts.csv
├── sample_event.txt
├── data/
├── templates/
│   └── index.html
└── static/
    ├── app.js
    └── style.css
```

## Important

`127.0.0.1` is fine for browser testing but Exotel cannot connect to localhost. Real calls require a public HTTPS/WSS endpoint, and your Exotel account must be properly configured for outbound calling.
