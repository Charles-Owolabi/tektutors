from __future__ import annotations

import os
import traceback
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "true")

REPORT = Path(__file__).resolve().parent / "cpanel_route_diagnose_report.txt"


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
                WHERE role IN ('admin', 'mentor', 'student')
                ORDER BY id ASC
                """
            )
            classrooms = app.fetch_all("SELECT id, slug, title FROM classrooms ORDER BY id ASC")

        write(f"DATABASE_ENGINE={app.DATABASE_ENGINE}")
        write(f"USER_COUNT={len(users)}")
        write(f"CLASSROOM_COUNT={len(classrooms)}")

        for path in ["/", "/about", "/programs", "/lms", "/auth/login", "/contact"]:
            try:
                response = client.get(path, follow_redirects=True)
                write(f"PUBLIC path={path} status={response.status_code} final={response.request.path}")
            except Exception:
                write(f"PUBLIC_ERROR path={path}")
                write(traceback.format_exc())

        for user in users:
            write(
                f"USER id={user['id']} email={user['email']} role={user['role']} "
                f"approved={user['is_approved']} suspended={user['is_suspended']} must_change={user['must_change_password']}"
            )
            paths = ["/dashboard"] + [f"/lms/classroom/{classroom['slug']}" for classroom in classrooms]
            for path in paths:
                try:
                    with client.session_transaction() as session:
                        session["user_id"] = int(user["id"])
                    response = client.get(path, follow_redirects=True)
                    write(f"ROUTE email={user['email']} path={path} status={response.status_code} final={response.request.path}")
                    if response.status_code >= 400:
                        write(response.get_data(as_text=True)[:1200])
                except Exception:
                    write(f"ROUTE_ERROR email={user['email']} path={path}")
                    write(traceback.format_exc())

    except Exception:
        write("FATAL_START")
        write(traceback.format_exc())
        write("FATAL_END")
        raise


if __name__ == "__main__":
    main()