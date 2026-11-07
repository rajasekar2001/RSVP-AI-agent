
import os
import re
import logging
from urllib.parse import urlparse, quote

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("rsvp.sarvam")


def setting(name, default=""):
    return os.getenv(name, default).strip()


SARVAM_API_BASE = setting(
    "SARVAM_API_BASE", "https://apps.sarvam.ai"
).rstrip("/")

SARVAM_API_KEY = setting("SARVAM_API_KEY")
SARVAM_ORG_ID = setting("SARVAM_ORG_ID")
SARVAM_WORKSPACE_ID = setting("SARVAM_WORKSPACE_ID")
SARVAM_APP_ID = setting("SARVAM_APP_ID")
SARVAM_APP_VERSION = setting("SARVAM_APP_VERSION")
SARVAM_CONNECTION_ID = setting("SARVAM_CONNECTION_ID")
SARVAM_PHONE_NUMBER = setting("SARVAM_PHONE_NUMBER")
PUBLIC_BASE_URL = setting("PUBLIC_BASE_URL").rstrip("/")
SARVAM_WEBHOOK_TOKEN = setting("SARVAM_WEBHOOK_TOKEN")


def normalize_e164(phone):
    value = re.sub(r"[\s().-]", "", str(phone or "").strip())

    if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        raise ValueError(
            "Invalid phone number. Use +919876543210 format."
        )

    return value


def validate_config():
    required = {
        "SARVAM_API_KEY": SARVAM_API_KEY,
        "SARVAM_ORG_ID": SARVAM_ORG_ID,
        "SARVAM_WORKSPACE_ID": SARVAM_WORKSPACE_ID,
        "SARVAM_APP_ID": SARVAM_APP_ID,
        "SARVAM_APP_VERSION": SARVAM_APP_VERSION,
        "SARVAM_CONNECTION_ID": SARVAM_CONNECTION_ID,
        "SARVAM_PHONE_NUMBER": SARVAM_PHONE_NUMBER,
        "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    }

    missing = [
        key for key, value in required.items()
        if not value or value.lower().startswith(
            ("your_", "paste_", "replace_")
        )
    ]

    if missing:
        raise RuntimeError(
            "Missing settings: " + ", ".join(missing)
        )

    if not re.fullmatch(r"[1-9]\d*", SARVAM_APP_VERSION):
        raise RuntimeError(
            "SARVAM_APP_VERSION must match an actual "
            "committed Sarvam agent version."
        )

    if not SARVAM_API_KEY.isascii():
        raise RuntimeError("Invalid API key characters.")

    if any(c.isspace() for c in SARVAM_API_KEY):
        raise RuntimeError("API key contains whitespace.")

    if any(
        c in SARVAM_API_KEY
        for c in ("\\", "[", "]", "(", ")", '"')
    ):
        raise RuntimeError("API key contains invalid formatting.")

    normalize_e164(SARVAM_PHONE_NUMBER)

    for name, value in (
        ("SARVAM_API_BASE", SARVAM_API_BASE),
        ("PUBLIC_BASE_URL", PUBLIC_BASE_URL),
    ):
        parsed = urlparse(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or "\\" in value
            or "[" in value
            or "]" in value
        ):
            raise RuntimeError(f"Invalid {name}")

    if not PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError(
            "PUBLIC_BASE_URL must be a public HTTPS URL."
        )

    if "your-app.onrender.com" in PUBLIC_BASE_URL:
        raise RuntimeError(
            "Replace the placeholder Render URL."
        )

    return True


def sarvam_configured():
    try:
        return validate_config()
    except (ValueError, RuntimeError):
        return False


def config_summary():
    # Safe values only. Never reveal API key or webhook token.
    return {
        "configured": sarvam_configured(),
        "org_id": SARVAM_ORG_ID,
        "workspace_id": SARVAM_WORKSPACE_ID,
        "app_id": SARVAM_APP_ID,
        "app_version": SARVAM_APP_VERSION,
        "connection_id": SARVAM_CONNECTION_ID,
        "agent_phone_number": SARVAM_PHONE_NUMBER,
        "public_base_url": PUBLIC_BASE_URL,
        "note": (
            "Real calls use this Sarvam app_id and "
            "committed app_version, not local Genie instructions."
        ),
    }


def get_sarvam_error(response):
    try:
        body = response.json()
    except ValueError:
        return "Non-JSON response from Sarvam"

    if not isinstance(body, dict):
        return "Unexpected response from Sarvam"

    error = body.get("error", body.get("detail", body))

    if isinstance(error, dict):
        message = str(error.get("message", ""))
        extra = error.get("data") or {}
        details = (
            str(extra.get("details", ""))
            if isinstance(extra, dict) else ""
        )
        return " | ".join(
            x for x in (message, details) if x
        )[:500]

    return str(error)[:500]


def voice_agent(gender: str, agents=None):
    """Pick the Sarvam agent (and version) for the dashboard's Male/Female choice.

    A call's speaker voice is a property of the Sarvam agent version, so a male and a
    female voice are two agents created once in Sarvam (same shell, different speaker).
    Returns (app_id, version, note). `note` is non-empty when the requested voice is not
    configured and the default agent is used instead.
    """
    g = str(gender or "").strip().lower()
    agents = agents or {}
    if g in {"male", "female"}:
        key = g.upper()
        # Dashboard-entered agent wins; .env is only an optional fallback.
        app_id = str(agents.get(f"{g}_app_id") or "").strip() or setting(f"SARVAM_APP_ID_{key}")
        version = str(agents.get(f"{g}_app_version") or "").strip() or setting(f"SARVAM_APP_VERSION_{key}") or SARVAM_APP_VERSION
        if app_id and re.fullmatch(r"[1-9]\d*", str(version)):
            return app_id, str(version), ""
        return SARVAM_APP_ID, SARVAM_APP_VERSION, (
            f"{g.title()} voice needs a {g.title()} Sarvam agent: Sarvam cannot change the voice per call, so the default agent's voice was used. Paste the {g.title()} agent ID in the dashboard (Voice section)."
        )
    return SARVAM_APP_ID, SARVAM_APP_VERSION, ""


def voice_agents_configured():
    return {g: bool(setting(f"SARVAM_APP_ID_{g.upper()}")) for g in ("female", "male")}


SARVAM_LANGUAGES = {
    "bengali", "gujarati", "kannada", "malayalam", "tamil", "telugu", "punjabi",
    "sanskrit", "odia", "marathi", "hindi", "english", "assamese",
}
# Default call language. Without this Sarvam uses the agent's own default
# (Hindi "Namaste, I am Shubh...") which is what you heard in the recording.
SARVAM_INITIAL_LANGUAGE = setting("SARVAM_INITIAL_LANGUAGE", "English")
# Set SARVAM_USE_INITIAL_BOT_MESSAGE=false to keep the greeting configured in the dashboard.
SARVAM_USE_INITIAL_BOT_MESSAGE = setting("SARVAM_USE_INITIAL_BOT_MESSAGE", "true").lower() not in {"0", "false", "no"}


def start_sarvam_outbound(phone: str, contact_name: str, context: dict | None = None):
    """
    Launch a call using the exact committed Sarvam
    agent identified by SARVAM_APP_ID and VERSION.

    `context` (from build_real_call_context) is sent per call as:
      - app_config.agent_variables  -> input variables usable as @contact_name,
        @event_details, @call_instructions ... in the agent prompt
      - app_config.app_overrides.initial_bot_message -> the first sentence the
        agent speaks (replaces the dashboard greeting for THIS call only)
      - app_config.app_overrides.initial_language_name -> call language
    """
    validate_config()

    user_phone = normalize_e164(phone)
    agent_phone = normalize_e164(SARVAM_PHONE_NUMBER)

    url = (
        f"{SARVAM_API_BASE}/api/outbounds/v1"
        f"/orgs/{SARVAM_ORG_ID}"
        f"/workspaces/{SARVAM_WORKSPACE_ID}"
        "/outbounds"
    )

    webhook_url = (
        f"{PUBLIC_BASE_URL}/api/sarvam/webhook"
    )

    if SARVAM_WEBHOOK_TOKEN:
        webhook_url += (
            "?token=" + quote(SARVAM_WEBHOOK_TOKEN, safe="")
        )

    context = context or {}
    gender = str(context.get("voice_gender") or "").lower()
    chosen_app_id, chosen_version, agent_note = voice_agent(gender, context)
    # Dashboard Male/Female -> per-call speaker (text_to_speech_config override). A dedicated
    # male/female agent in .env (advanced, optional) takes priority; no .env is needed otherwise.
    dedicated_agent = bool(gender) and chosen_app_id != SARVAM_APP_ID
    speaker = "" if dedicated_agent else str(context.get("speaker_name") or "").strip().lower()
    if speaker and not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", speaker):
        speaker = ""
    voice_note = agent_note
    app_config = {
        "app_id": chosen_app_id,
        "app_version": int(chosen_version),
        "connection_config": {
            "connection_id": SARVAM_CONNECTION_ID,
            "agent_phone_number": agent_phone,
        },
    }
    context = context or {}
    allowed_vars = {
        v.strip() for v in setting(
            "SARVAM_AGENT_VARIABLES", "call_instructions,opening_message"
        ).split(",") if v.strip()
    }
    agent_variables = {
        k: str(v) for k, v in context.items()
        if k in allowed_vars and v not in (None, "")
    }
    if agent_variables:
        app_config["agent_variables"] = agent_variables
    overrides = {}
    if SARVAM_USE_INITIAL_BOT_MESSAGE and context.get("opening_message"):
        overrides["initial_bot_message"] = str(context["opening_message"])
    locale_map = {"en-in": "English", "ta-in": "Tamil", "hi-in": "Hindi",
                  "te-in": "Telugu", "ml-in": "Malayalam", "kn-in": "Kannada",
                  "mr-in": "Marathi", "bn-in": "Bengali", "gu-in": "Gujarati",
                  "pa-in": "Punjabi", "od-in": "Odia"}
    raw_lang = str(context.get("language") or "").strip()
    language = locale_map.get(raw_lang.lower()) or raw_lang.title() or SARVAM_INITIAL_LANGUAGE.strip().title()
    if language.lower() in SARVAM_LANGUAGES:
        overrides["initial_language_name"] = language
    tts = {}
    if speaker:
        tts["speaker_name"] = speaker
    try:
        pace = float(context.get("speech_pace") or 0)
    except (TypeError, ValueError):
        pace = 0.0
    if pace and abs(pace - 1.0) > 0.01:
        tts["speech_settings"] = {"pace": round(max(0.85, min(2.0, pace)), 2)}
    if tts:
        overrides["text_to_speech_config"] = tts
    if overrides:
        app_config["app_overrides"] = overrides

    payload = {
        "app_config": app_config,
        "user_config": {
            "user_phone_number": user_phone,
        },
        "webhook_config": {
            "url": webhook_url,
            "metadata": {
                "contact_name": str(contact_name or "").strip(),
                "user_phone_number": user_phone,
                "source": "rsvp-fastapi",
            },
        },
    }

    # Report exactly which agent version is requested.
    # Never log secret API credentials.
    logger.warning(
        "Starting Sarvam RSVP call: "
        "app_id=%s version=%s voice=%s speaker=%s contact=%s",
        chosen_app_id,
        chosen_version,
        (context or {}).get("voice_gender") or "default",
        speaker or "agent-default",
        contact_name,
    )

    def _post():
        try:
            return requests.post(
                url,
                headers={
                    "X-API-Key": SARVAM_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                "Sarvam network error: "
                + type(exc).__name__
            ) from exc

    response = _post()
    dropped = []

    # Sarvam answers 422 "Agent variables '{...}' not found in agent variables of app"
    # when a variable was not created in the agent. Drop exactly those and retry once,
    # so the call still goes out (greeting + language overrides still apply).
    if response.status_code == 422 and app_config.get("agent_variables"):
        message = get_sarvam_error(response)
        match = re.search(r"not found in agent variables", message)
        if match:
            dropped = [
                n for n in re.findall(r"'(\w+)'", message.split("not found")[0])
                if n in app_config["agent_variables"]
            ]
            if dropped:
                for name in dropped:
                    app_config["agent_variables"].pop(name, None)
                if not app_config["agent_variables"]:
                    app_config.pop("agent_variables")
                logger.warning(
                    "Sarvam agent is missing variables %s - retrying without them. "
                    "Create them in the Sarvam agent so the script is delivered.",
                    dropped,
                )
                response = _post()

    # If Sarvam refuses the per-call voice override, place the call with the agent's own voice
    # (and say so) instead of failing it.
    if response.status_code in (400, 422) and "text_to_speech_config" in app_config.get("app_overrides", {}):
        message = get_sarvam_error(response)
        if re.search(r"text_to_speech|speaker|speech_settings|app_overrides|extra|unexpected|not permitted|unrecognized|unknown",
                     message, re.I):
            app_config["app_overrides"].pop("text_to_speech_config", None)
            if not app_config["app_overrides"]:
                app_config.pop("app_overrides")
            voice_note = ("Sarvam did not accept a per-call voice (" + message[:120] + "): the agent's own voice was used. "
                          "Set the voice in the Sarvam agent, or add SARVAM_APP_ID_FEMALE / SARVAM_APP_ID_MALE agents.")
            logger.warning("Per-call voice rejected by Sarvam; retrying with the agent's voice. %s", message[:200])
            response = _post()

    if not response.ok:
        raise RuntimeError(
            f"Sarvam HTTP {response.status_code}: "
            + get_sarvam_error(response)
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Sarvam returned invalid JSON."
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Sarvam response.")

    attempt_id = data.get("attempt_id")

    if not attempt_id:
        raise RuntimeError(
            "Sarvam did not return an attempt_id."
        )

    logger.warning(
        "Sarvam call accepted: attempt_id=%s "
        "app_id=%s version=%s",
        attempt_id,
        chosen_app_id,
        chosen_version,
    )

    return {
        "attempt_id": attempt_id,
        "status": "calling",
        "dropped_variables": dropped,
        "voice_note": voice_note,
        "app_id": chosen_app_id,
        "raw": data,
    }
