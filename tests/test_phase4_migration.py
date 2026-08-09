import sqlite3

from src.repositories.migration_runner import apply_valuation_migration


def test_phase4_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db_path = tmp_path / "missing" / "nested" / "phase4.db"
    first = apply_valuation_migration(str(db_path))
    second = apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions = {row[0] for row in conn.execute("SELECT version_id FROM schema_migrations")}

    assert first["migration_id"] == "20260809_08_evidence_model_v2_synthesis_snapshot"
    assert second["applied"] is False
    assert {"technical_anchor_revisions", "technical_anchor_approvals", "technical_anchor_idempotency_keys"}.issubset(tables)
    assert "20260809_05_evidence_model_v2_fibonacci" in versions
