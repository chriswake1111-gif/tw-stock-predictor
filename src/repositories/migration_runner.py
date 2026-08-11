"""Small transactional SQLite migration runner with checksum verification."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from src.domain.valuation import utc_now_timestamp


MIGRATION_IDS = (
    "20260801_01_evidence_model_v2_valuation",
    "20260801_02_evidence_model_v2_approval_hardening",
    "20260801_03_pe_evidence_basis",
    "20260801_04_evidence_model_v2_liquidity",
    "20260809_05_evidence_model_v2_fibonacci",
    "20260809_06_evidence_model_v2_deployment",
    "20260809_07_evidence_model_v2_screening",
    "20260809_08_evidence_model_v2_synthesis_snapshot",
    "20260810_09_evidence_model_v2_performance_validation",
    "20260810_10_phase8_review_remediation",
    "20260811_11_evidence_model_v2_data_foundation",
    "20260811_12_raw_revision_metadata_identity",
    "20260811_13_phase10_first_review_remediation",
)
MIGRATION_ID = MIGRATION_IDS[-1]
MIGRATION_FILES = tuple(
    Path(__file__).resolve().parents[2] / "migrations" / f"{migration_id}.sql"
    for migration_id in MIGRATION_IDS
)


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


def apply_valuation_migration(db_path: str) -> dict[str, Any]:
    database_path = Path(db_path)
    if db_path != ":memory:":
        database_path.resolve().parent.mkdir(parents=True, exist_ok=True)
    migrations = []
    for migration_id, migration_file in zip(MIGRATION_IDS, MIGRATION_FILES):
        sql = migration_file.read_text(encoding="utf-8")
        migrations.append((
            migration_id,
            sql,
            hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        ))
    applied_ids: list[str] = []
    with sqlite3.connect(db_path) as conn:
        # Rebuild migrations run with FK enforcement paused, then fail closed on
        # explicit checks of the rebuilt table and its dependants before commit.
        # This prevents ALTER TABLE from rewriting child references to a
        # temporary table name while tolerating unrelated legacy FK debt.
        conn.execute("PRAGMA foreign_keys = OFF")
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
            for migration_id, sql, checksum in migrations:
                existing = conn.execute(
                    "SELECT checksum_sha256 FROM schema_migrations WHERE version_id = ?",
                    (migration_id,),
                ).fetchone()
                if existing:
                    if existing[0] != checksum:
                        raise RuntimeError(
                            f"migration checksum mismatch for {migration_id}"
                        )
                    continue
                for statement in _statements(sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version_id, checksum_sha256, applied_at) VALUES (?, ?, ?)",
                    (migration_id, checksum, utc_now_timestamp()),
                )
                applied_ids.append(migration_id)
            checked_tables = (
                "raw_resource_revisions",
                "data_quality_issues",
                "trading_calendar_revisions",
                "resource_publication_evidence",
                "ingestion_lock_recovery_events",
                "ingestion_resource_locks",
                "cbc_m1b_monthly",
            )
            existing_tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            violations = [
                row
                for table in checked_tables
                if table in existing_tables
                for row in conn.execute(f"PRAGMA foreign_key_check({table})").fetchall()
            ]
            if violations:
                raise sqlite3.IntegrityError(
                    f"migration foreign key check failed: {violations[:3]}"
                )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            return {
                "migration_id": MIGRATION_ID,
                "checksum_sha256": migrations[-1][2],
                "applied": bool(applied_ids),
                "applied_migration_ids": applied_ids,
            }
        except Exception:
            conn.rollback()
            raise
