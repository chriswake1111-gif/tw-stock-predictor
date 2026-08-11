import sqlite3

from fastapi.testclient import TestClient

from src.api.main import app
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.services.production_ingestion_service import ProductionIngestionService


def test_provider_health_and_snapshot_dependency_get_are_stored_state_only(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    ProductionIngestionService(db_path)
    snapshot = AnalysisSnapshotRepository(db_path).add(
        AnalysisSnapshot(
            symbol="2330.TW",
            knowledge_cutoff_at="2026-07-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={}, source_resource_versions=[],
            manual_approval_ids=[], output={"status": "insufficient_data"},
            created_at="2026-07-01T01:00:00Z",
        ),
        "phase10-api-snapshot",
    )
    with sqlite3.connect(db_path) as conn:
        before_snapshots = conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshots"
        ).fetchone()[0]
        before_checks = conn.execute(
            "SELECT COUNT(*) FROM snapshot_dependency_checks"
        ).fetchone()[0]

    client = TestClient(app)
    health = client.get(
        "/api/v2/data-freshness",
        params={
            "knowledge_cutoff_at": "2026-07-02T00:00:00Z",
            "provider": "twse",
        },
    )
    assert health.status_code == 200
    payload = health.json()
    assert payload["status"] == "available"
    assert {row["provider_id"] for row in payload["resources"]} == {"twse"}
    assert {row["freshness"] for row in payload["resources"]} == {"unknown"}
    assert all("credentials" not in row for row in payload["resources"])

    dependency = client.get(
        f"/api/v2/analysis/snapshots/{snapshot['snapshot_id']}/dependency-status",
        params={"comparison_cutoff": "2026-07-02T00:00:00Z"},
    )
    assert dependency.status_code == 200
    state = dependency.json()["dependency_status"]
    assert state["freshness_status"] == "unknown"
    assert state["historical_snapshot_validity"] == "unchanged"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshots"
        ).fetchone()[0] == before_snapshots
        assert conn.execute(
            "SELECT COUNT(*) FROM snapshot_dependency_checks"
        ).fetchone()[0] == before_checks


def test_provider_status_alias_and_dependency_errors(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    ProductionIngestionService(db_path)
    client = TestClient(app)
    providers = client.get(
        "/api/v2/providers/status",
        params={"knowledge_cutoff_at": "2026-07-02T00:00:00Z"},
    )
    assert providers.status_code == 200
    assert len(providers.json()["providers"]) == 4
    missing = client.get(
        "/api/v2/analysis/snapshots/not-found/dependency-status",
        params={"comparison_cutoff": "2026-07-02T00:00:00Z"},
    )
    assert missing.status_code == 404
    invalid = client.get(
        "/api/v2/data-freshness",
        params={"knowledge_cutoff_at": "2026-07-02"},
    )
    assert invalid.status_code == 422
