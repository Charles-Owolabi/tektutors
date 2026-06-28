from __future__ import annotations

import os
import traceback
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "true")

REPORT = Path(__file__).resolve().parent / "cpanel_db_diagnose_report.txt"


def write(line: str) -> None:
    print(line)
    with REPORT.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def main() -> None:
    if REPORT.exists():
        REPORT.unlink()

    try:
        import app

        write(f"DATABASE_ENGINE={app.DATABASE_ENGINE}")
        write(f"DATABASE_URL_SET={bool(app.DATABASE_URL)}")

        db = app.connect_db()
        try:
            db.execute("SELECT 1").fetchone()
            write("DB_CONNECTION=OK")

            for table in ["users", "classrooms", "enrollments", "assignments", "live_sessions"]:
                try:
                    row = db.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
                    write(f"TABLE_{table}_COUNT={row['count']}")
                except Exception as exc:
                    write(f"TABLE_{table}_ERROR={exc}")

            users = db.execute(
                """
                SELECT id, name, email, role, is_approved, is_suspended,
                       must_change_password,
                       CASE WHEN password_hash IS NULL OR password_hash = '' THEN 0 ELSE 1 END AS has_password_hash
                FROM users
                ORDER BY id ASC
                LIMIT 25
                """
            ).fetchall()
            write("USERS_SAMPLE_START")
            for user in users:
                write(
                    f"id={user['id']} email={user['email']} role={user['role']} "
                    f"approved={user['is_approved']} suspended={user['is_suspended']} "
                    f"must_change={user['must_change_password']} has_hash={user['has_password_hash']}"
                )
            write("USERS_SAMPLE_END")
        finally:
            db.close()
    except Exception:
        write("ERROR_START")
        write(traceback.format_exc())
        write("ERROR_END")
        raise


if __name__ == "__main__":
    main()