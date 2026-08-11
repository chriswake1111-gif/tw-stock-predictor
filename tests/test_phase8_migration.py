import sqlite3

import pytest

import src.repositories.migration_runner as migration_runner
from src.repositories.migration_runner import apply_valuation_migration


PHASE8_MIGRATION = "20260810_09_evidence_model_v2_performance_validation"
PHASE8_REMEDIATION_MIGRATION = "20260810_10_phase8_review_remediation"
PHASE10_MIGRATION = "20260811_13_phase10_first_review_remediation"


def test_phase8_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db_path = tmp_path / "missing" / "phase8.db"
    first = apply_valuation_migration(str(db_path))
    second = apply_valuation_migration(str(db_path))
    assert PHASE8_MIGRATION in migration_runner.MIGRATION_IDS
    assert migration_runner.MIGRATION_ID == migration_runner.MIGRATION_IDS[-1]
    assert migration_runner.MIGRATION_ID == PHASE10_MIGRATION
    assert first["applied"] is True
    assert second["applied"] is False
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "evaluation_profile_revisions",
        "evaluation_profile_approvals",
        "outcome_resource_manifests",
        "evaluation_runs",
        "scenario_evaluations",
        "evaluation_run_snapshots",
        "evaluation_idempotency_keys",
    }.issubset(tables)


def test_phase8_remediation_migration_is_safe_for_existing_phase8_database(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "existing.db"
    with monkeypatch.context() as context:
        context.setattr(migration_runner, "MIGRATION_IDS", migration_runner.MIGRATION_IDS[:-1])
        context.setattr(migration_runner, "MIGRATION_FILES", migration_runner.MIGRATION_FILES[:-1])
        context.setattr(migration_runner, "MIGRATION_ID", PHASE8_MIGRATION)
        migration_runner.apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO analysis_snapshot_idempotency_keys VALUES ('keep','fp','id','2026-01-01T00:00:00Z')")
    result = apply_valuation_migration(str(db_path))
    assert result["applied_migration_ids"] == [PHASE10_MIGRATION]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT payload_fingerprint FROM analysis_snapshot_idempotency_keys WHERE idempotency_key='keep'"
        ).fetchone()[0] == "fp"
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(outcome_resource_manifests)")
        }
        assert "outcome_observed_through_session" in columns
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='evaluation_run_snapshots'"
        ).fetchone()


def test_phase8_migration_rolls_back_as_one_transaction(tmp_path, monkeypatch):
    broken = tmp_path / "broken.sql"
    broken.write_text("CREATE TABLE should_rollback(id TEXT);\nTHIS IS NOT SQL;\n", encoding="utf-8")
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
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "should_rollback" not in tables
    assert "evaluation_runs" not in tables
