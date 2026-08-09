import sqlite3

import pytest

import src.repositories.migration_runner as migration_runner
from src.repositories.migration_runner import apply_valuation_migration


PHASE8_MIGRATION = "20260810_09_evidence_model_v2_performance_validation"


def test_phase8_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db_path = tmp_path / "missing" / "phase8.db"
    first = apply_valuation_migration(str(db_path))
    second = apply_valuation_migration(str(db_path))
    assert migration_runner.MIGRATION_ID == PHASE8_MIGRATION
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
        "evaluation_idempotency_keys",
    }.issubset(tables)


def test_phase8_migration_is_safe_for_existing_phase7_database(tmp_path):
    db_path = tmp_path / "existing.db"
    apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO analysis_snapshot_idempotency_keys VALUES ('keep','fp','id','2026-01-01T00:00:00Z')")
    apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT payload_fingerprint FROM analysis_snapshot_idempotency_keys WHERE idempotency_key='keep'"
        ).fetchone()[0] == "fp"


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
