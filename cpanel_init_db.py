#!/usr/bin/env python
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the workspace is in the python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

# Set defaults for database initialization on cPanel
os.environ.setdefault("APP_ENV", "production")
# Ensure initialization code runs in the script since we disabled automatic import execution
os.environ["SKIP_DB_INIT"] = "true"

def main() -> int:
    try:
        print("Importing application...")
        import app
        
        print(f"Initializing database. Engine is: {app.DATABASE_ENGINE}")
        print(f"Database config: {app.database_config_summary()}")
        
        app.initialize_storage()
        
        print("Database initialized, migrated, and seeded successfully!")
        return 0
    except Exception as exc:
        import traceback
        print(f"ERROR: Database initialization failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
