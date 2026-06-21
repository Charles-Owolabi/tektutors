from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

os.environ["SKIP_DB_INIT"] = "true"

import app


TABLES = [
    "users",
    "classrooms",
    "enrollments",
    "announcements",
    "modules",
    "module_items",
    "assignments",
    "materials",
    "submissions",
    "project_suggestions",
    "attendance_sessions",
    "attendance_records",
    "course_feedback",
    "live_sessions",
]


def sqlite_rows(sqlite_db_path: Path, table_name: str) -> tuple[list[str], list[sqlite3.Row]]:
    source = sqlite3.connect(sqlite_db_path)
    source.row_factory = sqlite3.Row
    try:
        table = source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        if table is None:
            return [], []
        columns = [row["name"] for row in source.execute(f"PRAGMA table_info({table_name})").fetchall()]
        rows = source.execute(f"SELECT * FROM {table_name} ORDER BY id ASC").fetchall()
        return columns, rows
    finally:
        source.close()


def upsert_row(db: Any, table_name: str, columns: list[str], row: sqlite3.Row) -> None:
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    update_columns = [column for column in columns if column != "id"]
    if update_columns:
        assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
        conflict_clause = f"ON CONFLICT (id) DO UPDATE SET {assignments}"
    else:
        conflict_clause = "ON CONFLICT (id) DO NOTHING"

    db.execute(
        f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders}) {conflict_clause}",
        tuple(row[column] for column in columns),
    )


def reset_sequence(db: Any, table_name: str) -> None:
    db.execute(
        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
    )


def main() -> None:
    if app.DATABASE_ENGINE != "postgresql":
        raise SystemExit("Set DATABASE_URL to your PostgreSQL connection string before running this migration.")

    sqlite_db_path = Path(os.environ.get("SQLITE_DB_PATH", app.DATABASE_FILE))
    if not sqlite_db_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_db_path}")

    db = app.connect_db()
    try:
        app.initialize_database_schema(db)
        app.migrate_database_schema(db)
        for table_name in TABLES:
            columns, rows = sqlite_rows(sqlite_db_path, table_name)
            if not columns:
                print(f"Skipped missing table: {table_name}")
                continue
            for row in rows:
                upsert_row(db, table_name, columns, row)
            reset_sequence(db, table_name)
            print(f"Migrated {len(rows)} row(s): {table_name}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print("SQLite to PostgreSQL migration complete.")


if __name__ == "__main__":
    main()
