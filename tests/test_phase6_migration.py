import hashlib
import sqlite3

from src.repositories.migration_runner import (
    MIGRATION_FILES,
    MIGRATION_IDS,
    _statements,
    apply_valuation_migration,
)


def test_phase6_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db = tmp_path / "missing" / "nested" / "phase6.db"
    first = apply_valuation_migration(str(db))
    second = apply_valuation_migration(str(db))

    assert "20260809_07_evidence_model_v2_screening" in first["applied_migration_ids"]
    assert second["applied"] is False
    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        versions = {
            row[0] for row in conn.execute("SELECT version_id FROM schema_migrations")
        }
        valuation_unique_indexes = set()
        for index in conn.execute(
            "PRAGMA index_list('security_valuation_observations')"
        ):
            if index[2]:
                columns = tuple(
                    row[2] for row in conn.execute(f"PRAGMA index_info('{index[1]}')")
                )
                valuation_unique_indexes.add(columns)
    assert {
        "security_valuation_observations",
        "screening_profile_revisions",
        "screening_profile_approvals",
        "screening_idempotency_keys",
    }.issubset(tables)
    assert "forward_eps_observations" in tables
    assert "deployment_plan_revisions" in tables
    assert "20260809_07_evidence_model_v2_screening" in versions
    assert (
        "symbol",
        "metric_date",
        "source_name",
        "source_dataset",
        "revision_number",
    ) in valuation_unique_indexes


def test_latest_migration_applies_to_existing_phase6_database(tmp_path):
    db = tmp_path / "phase6.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version_id TEXT PRIMARY KEY,
                checksum_sha256 TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        for migration_id, migration_file in zip(
            MIGRATION_IDS[:-1], MIGRATION_FILES[:-1]
        ):
            sql = migration_file.read_text(encoding="utf-8")
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?,?,?)",
                (
                    migration_id,
                    hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                    "2026-08-09T00:00:00.000000Z",
                ),
            )
        conn.commit()

    result = apply_valuation_migration(str(db))
    assert result["applied_migration_ids"] == [
        "20260809_08_evidence_model_v2_synthesis_snapshot"
    ]
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version_id = ?",
            ("20260809_07_evidence_model_v2_screening",),
        ).fetchone() == (1,)
