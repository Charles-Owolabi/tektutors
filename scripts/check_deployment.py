from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def warn(message: str) -> None:
    print(f"WARN: {message}")


def ok(message: str) -> None:
    print(f"OK: {message}")


def check_git_clean(project_root: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        warn(f"Could not inspect git status: {exc}")
        return

    if result.stdout.strip():
        warn("Git worktree has uncommitted changes. Commit or intentionally package the release before deployment.")
        print(result.stdout.strip())
    else:
        ok("Git worktree is clean.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production deployment readiness.")
    parser.add_argument("--check-db", action="store_true", help="Open a database connection and run SELECT 1.")
    parser.add_argument("--skip-git", action="store_true", help="Skip the git cleanliness check.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    os.environ.setdefault("APP_ENV", "production")
    os.environ.setdefault("SKIP_DB_INIT", "true")

    if not args.skip_git:
        check_git_clean(project_root)

    try:
        import app
    except Exception as exc:
        fail(f"Application import failed: {exc}")

    ok("Application imports with production configuration.")

    data_dir = project_root / "data"
    if not data_dir.exists():
        fail("data/ directory is missing.")
    if not os.access(data_dir, os.W_OK):
        fail("data/ directory is not writable by the current user.")
    ok("data/ directory exists and is writable.")

    client = app.application.test_client()
    response = client.get("/healthz")
    if response.status_code != 200:
        fail(f"/healthz returned HTTP {response.status_code}.")
    ok("/healthz returns HTTP 200.")


    login_page = client.get("/auth/login")
    if b'name="_csrf_token"' not in login_page.data:
        fail("Rendered login form does not include a CSRF token.")
    ok("Rendered POST forms include CSRF tokens.")

    csrf_response = client.post("/auth/login", data={"email": "nobody@example.com", "password": "bad"})
    if csrf_response.status_code != 400:
        fail(f"Missing CSRF token was not rejected; got HTTP {csrf_response.status_code}.")
    ok("Missing CSRF token is rejected.")

    if args.check_db:
        try:
            with app.application.app_context():
                app.get_db().execute("SELECT 1")
        except Exception as exc:
            fail(f"Database check failed: {exc}")
        ok("Database connection succeeded.")

        page = client.get("/")
        if page.status_code != 200:
            fail(f"Home page returned HTTP {page.status_code}.")
        ok("Home page returns HTTP 200.")

    print("Deployment readiness checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
