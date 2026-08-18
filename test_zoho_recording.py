#!/usr/bin/env python
from __future__ import annotations

import os
import sys
import requests
from pathlib import Path

# Ensure the workspace is in the python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

# Set defaults to production environment
os.environ.setdefault("APP_ENV", "production")
os.environ["SKIP_DB_INIT"] = "true"

def main() -> int:
    try:
        import app
        import zoho_meeting
        
        meeting_key = sys.argv[1] if len(sys.argv) > 1 else None
        if not meeting_key:
            print("No meeting key argument provided. Attempting to select a pending session from DB...")
            with app.application.app_context():
                db = app.get_db()
                row = db.execute("SELECT meeting_key FROM live_sessions WHERE recording_status = 'pending' LIMIT 1").fetchone()
                if row:
                    meeting_key = row[0]
                    print(f"Auto-selected pending meeting key: {meeting_key}")
                else:
                    print("No pending sessions found in DB.")
                    return 1

        print(f"Testing Zoho API for meeting key: {meeting_key}")
        
        zsoid = zoho_meeting.get_env_var("ZOHO_ZSOID")
        api_url = zoho_meeting.get_env_var("ZOHO_API_URL", "https://meeting.zoho.com").rstrip("/")
        access_token = zoho_meeting.get_access_token()
        
        print(f"ZSOID: {zsoid}")
        print(f"API URL: {api_url}")
        print(f"Access Token retrieved: {bool(access_token)}")
        
        if not access_token:
            print("Failed to get Zoho access token.")
            return 1
            
        url = f"{api_url}/meeting/api/v2/{zsoid}/recordings/{meeting_key}.json"
        headers = {
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Content-Type": "application/json;charset=UTF-8"
        }
        
        print(f"Sending GET request to Zoho: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Zoho HTTP Response Status Code: {response.status_code}")
        print(f"Zoho Response Body: {response.text}")
        
        return 0
    except Exception as exc:
        import traceback
        print(f"ERROR: {exc}")
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
