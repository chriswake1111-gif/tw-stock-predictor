import sqlite3

from src.repositories.migration_runner import apply_valuation_migration


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
    assert {
        "security_valuation_observations",
        "screening_profile_revisions",
        "screening_profile_approvals",
        "screening_idempotency_keys",
    }.issubset(tables)
    assert "forward_eps_observations" in tables
    assert "deployment_plan_revisions" in tables
    assert "20260809_07_evidence_model_v2_screening" in versions
