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
# Disable database automatic setup during import to keep it fast
os.environ["SKIP_DB_INIT"] = "true"

def main() -> int:
    try:
        print("Importing application...")
        import app
        
        print("Running scheduled reminders task...")
        with app.application.app_context():
            print("Checking and sending meeting reminders...")
            app.check_and_send_meeting_reminders()
            
            print("Checking and sending challenge due reminders...")
            app.check_and_send_challenge_due_reminders()
            
            print("Checking and syncing pending live session recordings...")
            app.sync_all_pending_recordings()
            
        print("Scheduler checks finished successfully.")
        return 0
    except Exception as exc:
        import traceback
        print(f"ERROR: Scheduler execution failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
