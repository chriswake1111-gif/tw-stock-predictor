import sqlite3

import pytest

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository


def snapshot(*, output=None, supersedes=None):
    return AnalysisSnapshot(
        symbol="2330.TW",
        knowledge_cutoff_at="2026-06-01T00:00:00Z",
        capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
        model_version="2.0.0",
        used_rule_versions={"VAL-01": "2.0.1"},
        source_resource_versions=[{
            "section": "valuation",
            "resource_type": "forward_eps_revision",
            "resource_id": "eps-1",
            "revision_number": 1,
            "approval_ids": ["approval-eps-1"],
        }],
        manual_approval_ids=["approval-eps-1"],
        output=output or {"status": "partial", "symbol": "2330.TW"},
        created_at="2026-08-09T00:00:00Z",
        supersedes_snapshot_id=supersedes,
    )


def test_snapshot_insert_retrieval_and_permanent_idempotency(tmp_path):
    repo = AnalysisSnapshotRepository(str(tmp_path / "snapshot.db"))
    first = repo.add(snapshot(), "key-a")
    same = repo.add(snapshot(), "key-b")
    assert first["created"] is True
    assert same["created"] is False
    assert same["snapshot_id"] == first["snapshot_id"]
    loaded = repo.get(first["snapshot_id"])
    assert loaded["output"] == {
        "status": "partial",
        "symbol": "2330.TW",
        "snapshot_id": first["snapshot_id"],
    }
    assert loaded["source_resource_versions"][0]["resource_id"] == "eps-1"
    with pytest.raises(ValueError, match="different payload"):
        repo.add(snapshot(output={"status": "available"}), "key-b")


def test_snapshot_update_and_delete_are_rejected_by_database(tmp_path):
    db_path = tmp_path / "immutable.db"
    repo = AnalysisSnapshotRepository(str(db_path))
    created = repo.add(snapshot(), "snapshot-key")
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE analysis_snapshots SET model_version = 'tampered' WHERE snapshot_id = ?",
                (created["snapshot_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "DELETE FROM analysis_snapshots WHERE snapshot_id = ?",
                (created["snapshot_id"],),
            )
    assert repo.get(created["snapshot_id"])["model_version"] == "2.0.0"


def test_snapshot_supersedes_is_explicit_and_symbol_bound(tmp_path):
    repo = AnalysisSnapshotRepository(str(tmp_path / "lineage.db"))
    original = repo.add(snapshot(), "original")
    replacement = repo.add(
        snapshot(output={"status": "available"}, supersedes=original["snapshot_id"]),
        "replacement",
    )
    assert replacement["supersedes_snapshot_id"] == original["snapshot_id"]
    assert repo.get(original["snapshot_id"])["output"]["status"] == "partial"
