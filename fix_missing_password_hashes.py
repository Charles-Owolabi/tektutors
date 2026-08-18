from __future__ import annotations

import os

from werkzeug.security import generate_password_hash

os.environ.setdefault("SKIP_DB_INIT", "true")

import app

TEMP_PASSWORD = "TektutorsTemp123!"


def main() -> None:
    db = app.connect_db()
    try:
        app.migrate_database_schema(db)
        rows = db.execute(
            """
            SELECT id, email
            FROM users
            WHERE password_hash IS NULL OR password_hash = ''
            ORDER BY id ASC
            """
        ).fetchall()
        for row in rows:
            db.execute(
                """
                UPDATE users
                SET password_hash = ?, is_approved = 1, is_suspended = 0, must_change_password = 1
                WHERE id = ?
                """,
                (generate_password_hash(TEMP_PASSWORD), int(row["id"])),
            )
            print(f"Reset missing password hash for {row['email']}")
        db.commit()
        print(f"Done. Updated {len(rows)} user(s). Temporary password: {TEMP_PASSWORD}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()