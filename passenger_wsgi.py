from __future__ import annotations

import os
import traceback
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


def describe_database_url(value: str | None) -> str:
    if not value:
        return "not set"
    parsed = urlparse(value)
    host = parsed.hostname or "unknown"
    port = parsed.port or 5432
    database_name = parsed.path.lstrip("/") or "unknown"
    return f"host={host} port={port} database={database_name}"


try:
    from app import application
except Exception:
    app_dir = Path(__file__).resolve().parent
    env_file = app_dir / ".env"
    files_in_dir = [f.name for f in app_dir.iterdir() if f.is_file()]
    project_env = dotenv_values(env_file) if env_file.exists() else {}
    diagnostics = [
        "Passenger startup failed.",
        f"project_env_database={describe_database_url(project_env.get('DATABASE_URL'))}",
        f"runtime_DATABASE_URL={describe_database_url(os.environ.get('DATABASE_URL'))}",
        f"runtime_POSTGRES_URL={describe_database_url(os.environ.get('POSTGRES_URL'))}",
        f"runtime_POSTGRESQL_URL={describe_database_url(os.environ.get('POSTGRESQL_URL'))}",
        f"env_file_exists={env_file.exists()}",
        f"files_in_directory={files_in_dir}",
        "",
        traceback.format_exc(),
    ]
    log_path = app_dir / "passenger_wsgi_error.log"
    log_path.write_text("\n".join(diagnostics), encoding="utf-8")
    raise