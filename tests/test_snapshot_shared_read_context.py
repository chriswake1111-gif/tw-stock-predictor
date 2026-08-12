import sqlite3

import pytest

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import (
    AnalysisSnapshotRepository,
    SnapshotIntegrityError,
)
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.services.data_freshness_service import DataFreshnessService


def add_snapshot(repo: AnalysisSnapshotRepository):
    return repo.add(
        AnalysisSnapshot(
            symbol="2330.TW",
            knowledge_cutoff_at="2026-08-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={},
            source_resource_versions=[],
            manual_approval_ids=[],
            output={"status": "available", "symbol": "2330.TW"},
            created_at="2026-08-01T00:01:00Z",
        ),
        "shared-read-snapshot",
    )


def connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_snapshot_and_freshness_can_share_caller_owned_connection(tmp_path):
    db_path = str(tmp_path / "shared.db")
    snapshots = AnalysisSnapshotRepository(db_path)
    stored = add_snapshot(snapshots)
    service = DataFreshnessService(db_path)
    with connection(db_path) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        loaded = snapshots.get_with_connection(conn, stored["snapshot_id"])
        result = service.snapshot_dependency_freshness_with_connection(
            conn, loaded, "2026-08-02T00:00:00Z"
        )
    assert result["snapshot_id"] == stored["snapshot_id"]
    assert result["freshness_status"] == "unknown"
    assert result["reasons"] == ["snapshot_has_no_dependencies"]


def test_connection_aware_foundation_reads_do_not_open_hidden_connections(tmp_path, monkeypatch):
    db_path = str(tmp_path / "foundation.db")
    repo = DataFoundationRepository(db_path)
    with connection(db_path) as conn:
        monkeypatch.setattr(repo, "_connect", lambda: (_ for _ in ()).throw(AssertionError("hidden connection")))
        assert repo.provider_health_as_of_with_connection(
            conn, "2026-08-01T00:00:00Z"
        ) == []
        assert repo.latest_publication_evidence_as_of_with_connection(
            conn, "cbc.m1b", "2026-07", "2026-08-01T00:00:00Z"
        ) is None


def test_connection_aware_snapshot_read_raises_typed_integrity_error(tmp_path):
    db_path = str(tmp_path / "integrity.db")
    repo = AnalysisSnapshotRepository(db_path)
    stored = add_snapshot(repo)
    with connection(db_path) as conn:
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='analysis_snapshots'"
        ).fetchall()
        for trigger in triggers:
            conn.execute(f'DROP TRIGGER "{trigger[0]}"')
        conn.execute(
            "UPDATE analysis_snapshots SET output_json = ? WHERE snapshot_id = ?",
            ('{"status":"corrupted"}', stored["snapshot_id"]),
        )
        with pytest.raises(SnapshotIntegrityError):
            repo.get_with_connection(conn, stored["snapshot_id"])
