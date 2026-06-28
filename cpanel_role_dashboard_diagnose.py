from __future__ import annotations

import os
import traceback
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "true")

REPORT = Path(__file__).resolve().parent / "cpanel_role_dashboard_report.txt"


def write(line: str) -> None:
    print(line)
    with REPORT.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def main() -> None:
    if REPORT.exists():
        REPORT.unlink()

    try:
        import app

        app.application.testing = True
        client = app.application.test_client()

        with app.application.app_context():
            users = app.fetch_all(
                """
                SELECT id, email, role, is_approved, is_suspended, must_change_password
                FROM users
                ORDER BY id ASC
                """
            )

        write(f"USER_COUNT={len(users)}")
        for user in users:
            if user["role"] not in {"admin", "mentor", "student"}:
                continue
            write(
                f"TEST_USER id={user['id']} email={user['email']} role={user['role']} "
                f"approved={user['is_approved']} suspended={user['is_suspended']} "
                f"must_change={user['must_change_password']}"
            )
            try:
                with client.session_transaction() as session:
                    session["user_id"] = int(user["id"])
                response = client.get("/dashboard", follow_redirects=True)
                write(f"RESULT email={user['email']} status={response.status_code} path={response.request.path}")
                if response.status_code >= 400:
                    write(response.get_data(as_text=True)[:1500])
            except Exception:
                write(f"ERROR email={user['email']}")
                write(traceback.format_exc())

    except Exception:
        write("FATAL_START")
        write(traceback.format_exc())
        write("FATAL_END")
        raise


if __name__ == "__main__":
    main()