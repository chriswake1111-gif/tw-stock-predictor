import sqlite3

import pytest

import src.repositories.migration_runner as migration_runner
from src.repositories.migration_runner import apply_valuation_migration


PHASE10_MIGRATION = "20260811_13_phase10_first_review_remediation"


def test_phase10_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db_path = tmp_path / "missing" / "phase10.db"
    first = apply_valuation_migration(str(db_path))
    second = apply_valuation_migration(str(db_path))
    assert migration_runner.MIGRATION_ID == PHASE10_MIGRATION
    assert first["applied_migration_ids"][-1] == PHASE10_MIGRATION
    assert second["applied"] is False
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "data_providers", "data_resources", "ingestion_runs",
        "ingestion_run_items", "raw_resource_revisions", "data_quality_issues",
        "trading_calendar_revisions", "snapshot_dependency_checks",
        "ingestion_resource_locks",
        "ingestion_lock_recovery_events", "resource_publication_evidence",
    }.issubset(tables)
    assert "analysis_snapshots" in tables
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "lease_expires_at" in {
            row[1] for row in conn.execute(
                "PRAGMA table_info(ingestion_resource_locks)"
            )
        }
        assert "publication_evidence_id" in {
            row[1] for row in conn.execute("PRAGMA table_info(cbc_m1b_monthly)")
        }


def test_raw_revision_metadata_identity_upgrade_preserves_foreign_keys(tmp_path):
    db_path = tmp_path / "raw-upgrade.db"
    with pytest.MonkeyPatch.context() as context:
        context.setattr(migration_runner, "MIGRATION_IDS", migration_runner.MIGRATION_IDS[:-1])
        context.setattr(migration_runner, "MIGRATION_FILES", migration_runner.MIGRATION_FILES[:-1])
        context.setattr(migration_runner, "MIGRATION_ID", migration_runner.MIGRATION_IDS[-2])
        migration_runner.apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO data_providers VALUES (?,?,?,?,?,?,?,?)",
            (
                "test", "Test", "authoritative", "official", "test", 1,
                "2026-08-11T00:00:00Z", "provider-fingerprint",
            ),
        )
        conn.execute(
            "INSERT INTO data_resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "test.raw", "test", "TEST_RAW", "monetary_statistic", "TW", "daily",
                "{}", "parser", "1", "1", "archive_normalized", 1,
                "2026-08-11T00:00:00Z", "resource-fingerprint",
            ),
        )
        conn.execute(
            """
            INSERT INTO raw_resource_revisions VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                "raw-1", "fingerprint-1", "test", "test.raw", "2026-08-11",
                None, None, "2026-08-11T01:00:00Z", "2026-08-11T01:00:00Z",
                "a" * 64, "1", "b" * 64, "archive_normalized", None,
                "awaiting_review", "awaiting_review", None, "candidate",
            ),
        )
        conn.execute(
            "INSERT INTO ingestion_runs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "run-legacy-lock", "2026-08-11T00:00:00Z", None, "manual",
                "phase10.1", '["test.raw"]', "internal.test", "running", None,
            ),
        )
        conn.execute(
            "INSERT INTO ingestion_resource_locks VALUES (?,?,?)",
            ("test.raw", "run-legacy-lock", "2026-08-11T00:00:00Z"),
        )
    result = apply_valuation_migration(str(db_path))
    assert result["applied_migration_ids"] == [PHASE10_MIGRATION]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT eligibility_status FROM raw_resource_revisions WHERE raw_resource_revision_id='raw-1'"
        ).fetchone()[0] == "awaiting_review"
        assert conn.execute(
            "SELECT lease_expires_at FROM ingestion_resource_locks WHERE resource_id='test.raw'"
        ).fetchone()[0] == "2026-08-11T00:00:00Z"


def test_phase10_migration_upgrades_current_main_schema_without_rewrite(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "upgrade.db"
    with monkeypatch.context() as context:
        context.setattr(migration_runner, "MIGRATION_IDS", migration_runner.MIGRATION_IDS[:-1])
        context.setattr(migration_runner, "MIGRATION_FILES", migration_runner.MIGRATION_FILES[:-1])
        context.setattr(migration_runner, "MIGRATION_ID", migration_runner.MIGRATION_IDS[-2])
        migration_runner.apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO analysis_snapshot_idempotency_keys VALUES (?,?,?,?)",
            ("keep", "fp", "missing-parent-allowed-before-fk-check", "2026-01-01T00:00:00Z"),
        )
    result = apply_valuation_migration(str(db_path))
    assert result["applied_migration_ids"] == [PHASE10_MIGRATION]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT payload_fingerprint FROM analysis_snapshot_idempotency_keys WHERE idempotency_key='keep'"
        ).fetchone()[0] == "fp"


def test_phase10_migration_rolls_back_as_one_transaction(tmp_path, monkeypatch):
    broken = tmp_path / "broken.sql"
    broken.write_text(
        "CREATE TABLE phase10_should_rollback(id TEXT);\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        migration_runner,
        "MIGRATION_FILES",
        (*migration_runner.MIGRATION_FILES[:-1], broken),
    )
    db_path = tmp_path / "rollback.db"
    with pytest.raises(sqlite3.Error):
        apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "phase10_should_rollback" not in tables
    assert "analysis_snapshots" not in tables
