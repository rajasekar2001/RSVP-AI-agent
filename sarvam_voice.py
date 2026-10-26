
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


def start_sarvam_outbound(phone: str, contact_name: str):
    """
    Launch a call using the exact committed Sarvam
    agent identified by SARVAM_APP_ID and VERSION.

    Note: Greeting and Instructions must be edited
    inside this agent on the Sarvam platform.
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

    payload = {
        "app_config": {
            "app_id": SARVAM_APP_ID,
            "app_version": int(SARVAM_APP_VERSION),
            "connection_config": {
                "connection_id": SARVAM_CONNECTION_ID,
                "agent_phone_number": agent_phone,
            },
        },
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
        "app_id=%s version=%s contact=%s",
        SARVAM_APP_ID,
        SARVAM_APP_VERSION,
        contact_name,
    )

    try:
        response = requests.post(
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
        SARVAM_APP_ID,
        SARVAM_APP_VERSION,
    )

    return {
        "attempt_id": attempt_id,
        "status": "calling",
        "raw": data,
    }
