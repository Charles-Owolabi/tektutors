import os
import json
import time
from datetime import datetime
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TOKEN_FILE = os.path.join(DATA_DIR, "zoho_token.json")

def get_env_var(name, default=None):
    return os.environ.get(name, default)

def get_access_token():
    """
    Retrieves a cached OAuth access token or refreshes it using the refresh token if expired.
    """
    client_id = get_env_var("ZOHO_CLIENT_ID")
    client_secret = get_env_var("ZOHO_CLIENT_SECRET")
    refresh_token = get_env_var("ZOHO_REFRESH_TOKEN")
    accounts_url = get_env_var("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")

    # If any of the required credentials are missing, we cannot get a token.
    # We will log a warning/error and return None, permitting local mode (or mock mode) without crashes.
    if not (client_id and client_secret and refresh_token):
        print("Zoho Meeting integration is not configured in .env (missing ID, Secret, or Refresh Token). Running in mock/offline mode.")
        return None

    # Try loading cached token
    token_data = None
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                token_data = json.load(f)
        except Exception as e:
            print(f"Error reading cached Zoho token: {e}")

    # Check if cached token is still valid (with a 5-minute safety buffer)
    now = time.time()
    if token_data and token_data.get("access_token") and token_data.get("expires_at", 0) > now + 300:
        return token_data["access_token"]

    # If expired or missing, request a new one
    print("Zoho access token expired or missing. Refreshing token...")
    url = f"{accounts_url}/oauth/v2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }

    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        access_token = data.get("access_token")
        if not access_token:
            print(f"Error in Zoho OAuth response payload: {data}")
            return None

        expires_in = data.get("expires_in", 3600)
        expires_at = now + expires_in

        # Ensure data directory exists before saving
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"access_token": access_token, "expires_at": expires_at}, f)

        print("Zoho token refreshed and cached successfully.")
        return access_token
    except Exception as e:
        print(f"Failed to refresh Zoho access token: {e}")
        return None

def create_meeting(topic, agenda, start_time_iso, duration_mins):
    """
    Creates a new live training meeting on Zoho.
    :param topic: Meeting topic/title
    :param agenda: Meeting agenda/description
    :param start_time_iso: Start time string in YYYY-MM-DDTHH:MM format
    :param duration_mins: Duration in minutes
    :return: dict containing 'meetingKey', 'joinLink', 'startLink' or None on failure.
    """
    zsoid = get_env_var("ZOHO_ZSOID")
    api_url = get_env_var("ZOHO_API_URL", "https://meeting.zoho.com").rstrip("/")
    timezone = get_env_var("ZOHO_MEETING_TIMEZONE", "Africa/Lagos")

    if not zsoid:
        print("ZOHO_ZSOID is not configured in .env. Mocking meeting creation.")
        return mock_create_meeting(topic, agenda, start_time_iso, duration_mins)

    access_token = get_access_token()
    if not access_token:
        print("No Zoho access token available. Mocking meeting creation.")
        return mock_create_meeting(topic, agenda, start_time_iso, duration_mins)

    # Parse and format the start time for Zoho: "Jun 19, 2020 07:00 PM"
    try:
        dt = datetime.fromisoformat(start_time_iso)
        formatted_start_time = dt.strftime("%b %d, %Y %I:%M %p")
    except Exception as e:
        print(f"Error parsing start time ISO '{start_time_iso}': {e}")
        return None

    duration_ms = int(duration_mins) * 60 * 1000
    url = f"{api_url}/api/v2/{zsoid}/sessions.json"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json;charset=UTF-8"
    }
    
    zuid = get_env_var("ZOHO_ZUID")
    
    payload = {
        "session": {
            "topic": topic,
            "agenda": agenda or "",
            "startTime": formatted_start_time,
            "duration": duration_ms,
            "timezone": timezone
        }
    }
    
    if zuid:
        try:
            payload["session"]["presenter"] = int(zuid)
        except ValueError:
            payload["session"]["presenter"] = zuid

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        session = data.get("session")
        if not session:
            print(f"No session object in Zoho API create response: {data}")
            return None

        return {
            "meetingKey": str(session.get("meetingKey")),
            "joinLink": session.get("joinLink"),
            "startLink": session.get("startLink")
        }
    except Exception as e:
        print(f"Failed to create Zoho Meeting: {e}")
        # Log response body if possible for debugging
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Response from Zoho API: {response.text}")
        return None

def delete_meeting(meeting_key):
    """
    Deletes a scheduled meeting on Zoho.
    """
    zsoid = get_env_var("ZOHO_ZSOID")
    api_url = get_env_var("ZOHO_API_URL", "https://meeting.zoho.com").rstrip("/")

    if not zsoid or not meeting_key or meeting_key.startswith("mock-"):
        print(f"Mock delete meeting key: {meeting_key}")
        return True

    access_token = get_access_token()
    if not access_token:
        print("No Zoho access token available. Skipping delete on Zoho.")
        return True

    url = f"{api_url}/api/v2/{zsoid}/sessions/{meeting_key}.json"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json;charset=UTF-8"
    }

    try:
        response = requests.delete(url, headers=headers, timeout=10)
        # 204 or 404 indicates successful deletion or meeting already gone
        if response.status_code in (204, 404):
            return True
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Failed to delete Zoho Meeting '{meeting_key}': {e}")
        return False

def fetch_meeting_recording(meeting_key):
    """
    Fetches the recording for a completed meeting by its meeting key.
    :return: tuple (download_url, play_url) if available, or (None, None).
    """
    zsoid = get_env_var("ZOHO_ZSOID")
    api_url = get_env_var("ZOHO_API_URL", "https://meeting.zoho.com").rstrip("/")

    if not zsoid or not meeting_key or meeting_key.startswith("mock-"):
        # For mock sessions, after they are "completed", we can return a mock recording
        return "https://download.zoho.com/mock-download-recording", "https://meeting.zoho.com/mock-play-recording"

    access_token = get_access_token()
    if not access_token:
        return None, None

    url = f"{api_url}/meeting/api/v2/{zsoid}/recordings/{meeting_key}.json"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json;charset=UTF-8"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        # If token is invalid or expired, Zoho returns 401. Refresh token and retry once.
        if response.status_code == 401:
            # Remove cached token to force refresh
            try:
                os.remove(TOKEN_FILE)
            except Exception:
                pass
            new_token = get_access_token()
            if not new_token:
                return None, None
            headers["Authorization"] = f"Zoho-oauthtoken {new_token}"
            response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 400 or response.status_code == 404:
            # Typically means recording is not generated yet or no recording exists
            return None, None

        response.raise_for_status()
        data = response.json()

        recordings = data.get("recordings", [])
        if not recordings:
            return None, None

        # Get the first recording in the list
        recording = recordings[0]
        download_url = recording.get("downloadUrl")
        play_url = recording.get("playUrl") or recording.get("shareUrl")

        return download_url, play_url
    except Exception as e:
        print(f"Failed to fetch recording for Zoho Meeting '{meeting_key}': {e}")
        return None, None

def mock_create_meeting(topic, agenda, start_time_iso, duration_mins):
    """
    Mocks meeting creation when Zoho integration is not configured.
    """
    import random
    mock_key = f"mock-{random.randint(100000000, 999999999)}"
    print(f"Creating mock Zoho Meeting: Key={mock_key}")
    return {
        "meetingKey": mock_key,
        "joinLink": f"/lms/meeting/mock/{mock_key}?role=student",
        "startLink": f"/lms/meeting/mock/{mock_key}?role=host"
    }
