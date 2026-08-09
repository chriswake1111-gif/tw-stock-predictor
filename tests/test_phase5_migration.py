import sqlite3

from src.repositories.migration_runner import apply_valuation_migration


def test_phase5_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db_path = tmp_path / "missing" / "nested" / "phase5.db"
    first = apply_valuation_migration(str(db_path))
    second = apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions = {row[0] for row in conn.execute("SELECT version_id FROM schema_migrations")}
    assert first["migration_id"] == "20260809_07_evidence_model_v2_screening"
    assert second["applied"] is False
    assert {"deployment_plan_revisions", "deployment_plan_approvals",
            "deployment_plan_idempotency_keys"} <= tables
    assert "technical_anchor_revisions" in tables
    assert "forward_eps_observations" in tables
    assert "20260809_06_evidence_model_v2_deployment" in versions
