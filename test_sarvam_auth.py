
import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

api_key = os.getenv("SARVAM_API_KEY", "").strip()
org_id = os.getenv("SARVAM_ORG_ID", "").strip()
workspace_id = os.getenv("SARVAM_WORKSPACE_ID", "").strip()

if not all([api_key, org_id, workspace_id]):
    raise SystemExit("Missing Sarvam environment variables")

url = (
    "https://apps.sarvam.ai/api/app-authoring/v1/"
    f"orgs/{org_id}/workspaces/{workspace_id}/deployments"
)

try:
    response = requests.get(
        url,
        headers={"X-API-Key": api_key},
        timeout=15
    )

    print("HTTP Status:", response.status_code)

    if response.status_code == 200:
        print("SUCCESS: Authentication works.")
    else:
        try:
            data = response.json()
            error = data.get("error", {})
            if isinstance(error, dict):
                print("Error:", error.get("message", "Unknown"))
                print("Details:", error.get("data", {}).get("details", ""))
            else:
                print("Error:", str(error)[:300])
        except ValueError:
            print("Non-JSON response from Sarvam.")

except requests.RequestException as error:
    print("Network error:", type(error).__name__)
