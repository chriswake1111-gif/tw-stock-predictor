import sqlite3

import pytest

import src.repositories.migration_runner as migration_runner
from src.repositories.migration_runner import apply_valuation_migration


PHASE10_MIGRATION = "20260811_11_evidence_model_v2_data_foundation"


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
    }.issubset(tables)
    assert "analysis_snapshots" in tables


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
