from __future__ import annotations

import argparse
from typing import Any

from werkzeug.security import generate_password_hash

from app import (
    connect_db,
    generate_temporary_password,
    is_valid_email,
    migrate_database_schema,
    normalize_email,
    send_login_details_email,
    timestamp_now,
)

LOGIN_URL = "https://www.tektutors.com.ng/auth/login"


def get_user_by_email(db: Any, email: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def approve_candidate(*, name: str, email: str, role: str, password: str) -> tuple[dict[str, Any], bool]:
    normalized_email = normalize_email(email)
    db = connect_db()
    try:
        migrate_database_schema(db)
        existing_user = get_user_by_email(db, normalized_email)
        approved_at = timestamp_now()

        if existing_user:
            db.execute(
                """
                UPDATE users
                SET
                    name = ?,
                    password_hash = ?,
                    role = ?,
                    is_approved = 1,
                    approved_at = ?,
                    must_change_password = 1,
                    reset_token = NULL,
                    reset_token_expires_at = NULL
                WHERE email = ?
                """,
                (name, generate_password_hash(password), role, approved_at, normalized_email),
            )
            created = False
        else:
            db.execute(
                """
                INSERT INTO users (
                    name, email, password_hash, role, is_approved, approved_at, created_at,
                    must_change_password, reset_token, reset_token_expires_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, 1, NULL, NULL)
                """,
                (name, normalized_email, generate_password_hash(password), role, approved_at, approved_at),
            )
            created = True

        db.commit()
        approved_user = get_user_by_email(db, normalized_email)
        if approved_user is None:
            raise RuntimeError("Candidate approval completed, but the user record could not be reloaded.")
        return approved_user, created
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Approve a candidate for LMS access, generate login details, and optionally send them by email."
    )
    parser.add_argument("--name", required=True, help="Candidate full name.")
    parser.add_argument("--email", required=True, help="Candidate email address.")
    parser.add_argument("--role", choices=("student", "mentor", "admin"), default="student", help="LMS role to assign.")
    parser.add_argument("--password", help="Optional password to assign. If omitted, a secure password is generated.")
    parser.add_argument("--send-email", action="store_true", help="Send the generated login details to the candidate by email.")
    parser.add_argument("--login-url", default=LOGIN_URL, help="Login page URL included in the email and console output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    email = normalize_email(args.email)
    if not is_valid_email(email):
        raise SystemExit("Please provide a valid email address with --email.")

    password = args.password or generate_temporary_password()
    approved_user, created = approve_candidate(name=args.name.strip(), email=email, role=args.role, password=password)

    print("Candidate approved successfully.")
    print(f"Status: {'created and approved' if created else 'updated and approved'}")
    print(f"Name: {approved_user['name']}")
    print(f"Email: {approved_user['email']}")
    print(f"Role: {approved_user['role']}")
    print(f"Login URL: {args.login_url}")
    print(f"Temporary Password: {password}")

    if args.send_email:
        send_login_details_email(
            recipient_name=approved_user["name"],
            recipient_email=approved_user["email"],
            password=password,
            role=approved_user["role"],
            login_url=args.login_url,
        )
        print("Login details sent by email.")
    else:
        print("Email not sent. Re-run with --send-email to deliver the login details automatically.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
