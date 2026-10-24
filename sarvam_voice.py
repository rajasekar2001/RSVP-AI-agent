
import os
import re
from urllib.parse import urlparse, quote

import requests
from dotenv import load_dotenv


# --------------------------------------------------
# LOAD ENVIRONMENT VARIABLES
# --------------------------------------------------

# For local development, read the .env file.
# On Render, configure values in the Environment tab.
load_dotenv()

SARVAM_API_BASE = os.getenv(
    "SARVAM_API_BASE",
    "https://apps.sarvam.ai"
).strip().rstrip("/")

SARVAM_API_KEY = os.getenv(
    "SARVAM_API_KEY", ""
).strip()

SARVAM_ORG_ID = os.getenv(
    "SARVAM_ORG_ID", ""
).strip()

SARVAM_WORKSPACE_ID = os.getenv(
    "SARVAM_WORKSPACE_ID", ""
).strip()

SARVAM_APP_ID = os.getenv(
    "SARVAM_APP_ID", ""
).strip()

SARVAM_APP_VERSION = os.getenv(
    "SARVAM_APP_VERSION", "1"
).strip()

SARVAM_CONNECTION_ID = os.getenv(
    "SARVAM_CONNECTION_ID", ""
).strip()

SARVAM_PHONE_NUMBER = os.getenv(
    "SARVAM_PHONE_NUMBER", ""
).strip()

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", ""
).strip().rstrip("/")

SARVAM_WEBHOOK_TOKEN = os.getenv(
    "SARVAM_WEBHOOK_TOKEN", ""
).strip()


# --------------------------------------------------
# VALIDATE PHONE NUMBER
# --------------------------------------------------

def normalize_e164(phone: str) -> str:
    """
    Validate international E.164 phone numbers.

    Example:
        +919876543210
    """

    value = re.sub(
        r"[\s().-]",
        "",
        str(phone or "").strip()
    )

    if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        raise ValueError(
            "Invalid phone number. "
            "Use E.164 format, for example +919876543210."
        )

    return value


# --------------------------------------------------
# VALIDATE SARVAM CONFIGURATION
# --------------------------------------------------

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
        name
        for name, value in required.items()
        if not value or value.lower().startswith(
            ("your_", "paste_")
        )
    ]

    if missing:
        raise RuntimeError(
            "Missing Sarvam configuration: "
            + ", ".join(missing)
        )

    if not SARVAM_API_KEY.isascii():
        raise RuntimeError(
            "SARVAM_API_KEY contains non-ASCII characters."
        )

    if any(char.isspace() for char in SARVAM_API_KEY):
        raise RuntimeError(
            "SARVAM_API_KEY contains whitespace."
        )

    if any(
        char in SARVAM_API_KEY
        for char in ("\\", "[", "]", "(", ")", '"')
    ):
        raise RuntimeError(
            "SARVAM_API_KEY contains copied formatting characters."
        )

    if not re.fullmatch(
        r"[1-9]\d*",
        SARVAM_APP_VERSION
    ):
        raise RuntimeError(
            "SARVAM_APP_VERSION must be a positive integer."
        )

    for label, value in (
        ("SARVAM_API_BASE", SARVAM_API_BASE),
        ("PUBLIC_BASE_URL", PUBLIC_BASE_URL),
    ):
        parsed = urlparse(value)

        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or "[" in value
            or "]" in value
            or "\\" in value
        ):
            raise RuntimeError(
                f"{label} must be a valid plain URL."
            )

    if (
        PUBLIC_BASE_URL
        == "https://your-app.onrender.com"
    ):
        raise RuntimeError(
            "PUBLIC_BASE_URL is still a placeholder."
        )

    normalize_e164(SARVAM_PHONE_NUMBER)

    return True


def sarvam_configured():
    """
    Checks only local configuration.
    Does not verify Sarvam authentication.
    """

    try:
        validate_config()
        return True

    except (RuntimeError, ValueError):
        return False


# --------------------------------------------------
# DASHBOARD CONFIGURATION SUMMARY
# --------------------------------------------------

def config_summary():
    """
    Return non-secret configuration information.
    Never return the API key.
    """

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
            "Configured means environment variables "
            "are present and locally valid. "
            "It does not confirm API authentication."
        ),
    }


# --------------------------------------------------
# READ SARVAM ERROR RESPONSE SAFELY
# --------------------------------------------------

def get_sarvam_error(response):
    """
    Extract error details returned by Sarvam.
    Do not include authentication headers or secrets.
    """

    try:
        body = response.json()

    except ValueError:
        return (
            "Sarvam returned a non-JSON error response."
        )

    if not isinstance(body, dict):
        return "Unexpected Sarvam error response."

    error = body.get("error", {})

    if isinstance(error, dict):
        message = error.get("message", "")
        error_data = error.get("data", {})

        if isinstance(error_data, dict):
            details = error_data.get("details", "")
        else:
            details = ""

        code = error.get("code", "")

        parts = [
            str(item)
            for item in (message, details)
            if item
        ]

        if code:
            parts.append(f"Code: {code}")

        if parts:
            return " | ".join(parts)[:1000]

    detail = body.get("detail") or body.get("message")

    if detail:
        return str(detail)[:1000]

    return "No additional error details returned."


# --------------------------------------------------
# START REAL SARVAM OUTBOUND CALL
# --------------------------------------------------

def start_sarvam_outbound(
    phone: str,
    contact_name: str
):
    """
    Starts an outbound call through Sarvam Voice Agents.

    Called by the FastAPI RSVP campaign endpoint.
    """

    validate_config()

    user_phone = normalize_e164(phone)

    agent_phone = normalize_e164(
        SARVAM_PHONE_NUMBER
    )

    app_version = int(SARVAM_APP_VERSION)

    # Official Sarvam Voice Agents Instant Outbound API.
    url = (
        f"{SARVAM_API_BASE}"
        f"/api/outbounds/v1"
        f"/orgs/{SARVAM_ORG_ID}"
        f"/workspaces/{SARVAM_WORKSPACE_ID}"
        f"/outbounds"
    )

    # Sarvam calls this URL after the attempt finishes.
    webhook_url = (
        f"{PUBLIC_BASE_URL}/api/sarvam/webhook"
    )

    # Only use this if app.py verifies the same token.
    if SARVAM_WEBHOOK_TOKEN:
        webhook_url += (
            "?token="
            + quote(SARVAM_WEBHOOK_TOKEN, safe="")
        )

    payload = {
        "app_config": {
            "app_id": SARVAM_APP_ID,
            "app_version": app_version,
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
                "contact_name": str(
                    contact_name or ""
                ).strip(),
                "user_phone_number": user_phone,
                "source": "rsvp-fastapi",
            },
        },
    }

    headers = {
        "X-API-Key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }

    # Send request to Sarvam.
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )

    except requests.Timeout as exc:
        raise RuntimeError(
            "Sarvam API request timed out "
            "after 30 seconds."
        ) from exc

    except requests.RequestException as exc:
        raise RuntimeError(
            "Could not connect to Sarvam API: "
            f"{type(exc).__name__}"
        ) from exc

    # Preserve actual Sarvam error details.
    if not response.ok:

        error_message = get_sarvam_error(response)

        if response.status_code == 401:
            raise RuntimeError(
                "Sarvam HTTP 401 Unauthorized: "
                + error_message
            )

        if response.status_code == 403:
            raise RuntimeError(
                "Sarvam HTTP 403 Forbidden: "
                + error_message
            )

        if response.status_code == 422:
            raise RuntimeError(
                "Sarvam HTTP 422 Invalid configuration: "
                + error_message
            )

        raise RuntimeError(
            f"Sarvam HTTP {response.status_code}: "
            + error_message
        )

    # Parse successful response.
    try:
        data = response.json()

    except ValueError as exc:
        raise RuntimeError(
            "Sarvam returned an invalid JSON response."
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "Sarvam returned an unexpected JSON format."
        )

    attempt_id = data.get("attempt_id")

    if not attempt_id:
        raise RuntimeError(
            "Sarvam did not return attempt_id. "
            "Check the response format and outbound API."
        )

    return {
        "attempt_id": attempt_id,
        "status": "calling",
        "raw": data,
    }
