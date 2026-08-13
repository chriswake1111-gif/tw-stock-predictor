import sqlite3

import pytest

from src.repositories.migration_runner import (
    MIGRATION_ID,
    MIGRATION_IDS,
    apply_valuation_migration,
)


def test_phase7_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db_path = tmp_path / "missing" / "phase7.db"
    first = apply_valuation_migration(str(db_path))
    second = apply_valuation_migration(str(db_path))
    assert "20260809_08_evidence_model_v2_synthesis_snapshot" in MIGRATION_IDS
    assert "20260810_09_evidence_model_v2_performance_validation" in MIGRATION_IDS
    assert MIGRATION_ID == MIGRATION_IDS[-1]
    assert MIGRATION_ID == "20260813_14_evidence_model_v2_research_review_queue"
    assert first["applied"] is True
    assert second["applied"] is False
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "synthesis_profile_revisions",
        "synthesis_profile_approvals",
        "synthesis_idempotency_keys",
        "analysis_snapshots",
        "analysis_snapshot_idempotency_keys",
    }.issubset(tables)


def test_phase7_migration_checksum_is_enforced(tmp_path):
    db_path = tmp_path / "checksum.db"
    apply_valuation_migration(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum_sha256 = 'tampered' WHERE version_id = ?",
            (MIGRATION_ID,),
        )
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        apply_valuation_migration(str(db_path))
