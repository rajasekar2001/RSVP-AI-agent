import os
import requests
from dotenv import load_dotenv

load_dotenv()

EXOTEL_API_KEY = os.getenv('EXOTEL_API_KEY', '')
EXOTEL_API_TOKEN = os.getenv('EXOTEL_API_TOKEN', '')
EXOTEL_ACCOUNT_SID = os.getenv('EXOTEL_ACCOUNT_SID', '')
EXOTEL_CALLER_ID = os.getenv('EXOTEL_CALLER_ID', '')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
PUBLIC_WS_URL = os.getenv('PUBLIC_WS_URL', '')


def exotel_configured():
    return all([EXOTEL_API_KEY, EXOTEL_API_TOKEN, EXOTEL_ACCOUNT_SID, EXOTEL_CALLER_ID, PUBLIC_BASE_URL, PUBLIC_WS_URL])


def start_exotel_voice_call(phone, contact_name):
    if not exotel_configured():
        raise RuntimeError('Exotel configuration is incomplete. Check your .env file.')

    url = f'https://api.in.exotel.com/v1/Accounts/{EXOTEL_ACCOUNT_SID}/Calls/connect'
    stream_url = PUBLIC_WS_URL
    stream_url += ('&' if '?' in stream_url else '?') + 'sample-rate=24000'
    callback_url = PUBLIC_BASE_URL + '/api/exotel/status'

    data = [
        ('From', phone),
        ('CallerId', EXOTEL_CALLER_ID),
        ('StreamUrl', stream_url),
        ('StreamType', 'bidirectional'),
        ('Record', 'false'),
        ('TimeLimit', '600'),
        ('CustomField', contact_name[:128]),
        ('StatusCallback', callback_url),
        ('StatusCallbackEvents[]', 'ringing'),
        ('StatusCallbackEvents[]', 'answered'),
        ('StatusCallbackEvents[]', 'terminal'),
    ]

    response = requests.post(url, auth=(EXOTEL_API_KEY, EXOTEL_API_TOKEN), data=data, timeout=20)
    if not response.ok:
        raise RuntimeError(f'Exotel error {response.status_code}: {response.text}')

    payload = response.json()
    call = payload.get('Call', {})
    return {'call_sid': call.get('Sid'), 'status': call.get('Status', 'queued'), 'raw': payload}
