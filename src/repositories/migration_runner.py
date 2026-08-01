"""Small transactional SQLite migration runner with checksum verification."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from src.domain.valuation import utc_now_timestamp


MIGRATION_ID = "20260801_01_evidence_model_v2_valuation"
MIGRATION_FILE = Path(__file__).resolve().parents[2] / "migrations" / f"{MIGRATION_ID}.sql"


def _statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("migration contains an incomplete SQL statement")
    return statements


def apply_valuation_migration(db_path: str) -> dict[str, str | bool]:
    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version_id TEXT PRIMARY KEY,
                    checksum_sha256 TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            existing = conn.execute(
                "SELECT checksum_sha256 FROM schema_migrations WHERE version_id = ?",
                (MIGRATION_ID,),
            ).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise RuntimeError(
                        f"migration checksum mismatch for {MIGRATION_ID}"
                    )
                conn.commit()
                return {"migration_id": MIGRATION_ID, "checksum_sha256": checksum, "applied": False}
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version_id, checksum_sha256, applied_at) VALUES (?, ?, ?)",
                (MIGRATION_ID, checksum, utc_now_timestamp()),
            )
            conn.commit()
            return {"migration_id": MIGRATION_ID, "checksum_sha256": checksum, "applied": True}
        except Exception:
            conn.rollback()
            raise
