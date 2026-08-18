#!/usr/bin/env python
from __future__ import annotations

import os
import sys
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
        print("Connecting to database...")
        with app.application.app_context():
            db = app.get_db()
            sessions = db.execute(
                """
                SELECT id, topic, start_time, meeting_key, recording_status, 
                       recording_play_url, recording_download_url
                FROM live_sessions
                ORDER BY start_time DESC
                """
            ).fetchall()
            
            print(f"Total live sessions in DB: {len(sessions)}")
            print("-" * 80)
            for s in sessions:
                s_dict = dict(s)
                print(f"ID: {s_dict['id']}")
                print(f"Topic: {s_dict['topic']}")
                print(f"Start Time: {s_dict['start_time']}")
                print(f"Meeting Key: {s_dict['meeting_key']}")
                print(f"Recording Status: {s_dict['recording_status']}")
                print(f"Play URL: {s_dict['recording_play_url']}")
                print(f"Download URL: {s_dict['recording_download_url']}")
                print("-" * 80)
        return 0
    except Exception as exc:
        import traceback
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
