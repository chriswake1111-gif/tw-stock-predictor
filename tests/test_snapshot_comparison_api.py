import sqlite3

from fastapi.testclient import TestClient

from src.api.main import app
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository


client = TestClient(app)


def add_snapshot(db_path, *, suffix, symbol="2330.TW", malformed=False):
    minute = 1 if suffix == "base" else 2
    output = {
        "status": "partial", "marker": suffix,
        "symbol": symbol,
        "knowledge_cutoff_at": "2026-08-01T00:00:00Z",
        "model": {"version": "2.0.0"},
        "data_quality": {"status": "partial"},
        "valuation": {"status": "partial", "target_matrix": []},
        "liquidity": {"status": "insufficient_data"},
        "technical_support": {"status": "needs_human_input", "scenarios": []},
        "target_confluence": {"status": "insufficient_data", "overlap_ranges": []},
        "deployment_plan": {"status": "needs_human_input", "plans": []},
        "screening": {"status": "insufficient_data"},
    }
    if malformed:
        output.pop("model")
    return AnalysisSnapshotRepository(db_path).add(
        AnalysisSnapshot(
            symbol=symbol,
            knowledge_cutoff_at="2026-08-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={}, source_resource_versions=[], manual_approval_ids=[],
            output=output,
            created_at=f"2026-08-01T00:{minute:02d}:00Z",
        ),
        f"api-comparison-{suffix}",
    )


def compare_params(base, comparison, cutoff="2026-08-02T08:00:00+08:00"):
    return {
        "base_snapshot_id": base["snapshot_id"],
        "comparison_snapshot_id": comparison["snapshot_id"],
        "comparison_cutoff": cutoff,
    }


def test_static_compare_route_required_params_timezone_and_utc(monkeypatch, tmp_path):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    base = add_snapshot(db_path, suffix="base")
    comparison = add_snapshot(db_path, suffix="comparison")
    missing = client.get("/api/v2/analysis/snapshots/compare", params={
        "base_snapshot_id": base["snapshot_id"],
        "comparison_snapshot_id": comparison["snapshot_id"],
    })
    assert missing.status_code == 422
    assert missing.json()["detail"] == "comparison_cutoff_required"
    naive = client.get(
        "/api/v2/analysis/snapshots/compare",
        params=compare_params(base, comparison, "2026-08-02T00:00:00"),
    )
    assert naive.status_code == 422
    assert naive.json()["detail"] == "comparison_cutoff_invalid"
    loaded = client.get(
        "/api/v2/analysis/snapshots/compare",
        params=compare_params(base, comparison),
    )
    loaded.raise_for_status()
    assert loaded.json()["comparison_cutoff"] == "2026-08-02T00:00:00Z"


def test_not_found_and_incomparable_are_distinct(monkeypatch, tmp_path):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    base = add_snapshot(db_path, suffix="base")
    other = add_snapshot(db_path, suffix="other", symbol="2317.TW")
    missing = client.get(
        "/api/v2/analysis/snapshots/compare",
        params={**compare_params(base, other), "comparison_snapshot_id": "missing"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "snapshot_not_found"
    incompatible = client.get(
        "/api/v2/analysis/snapshots/compare", params=compare_params(base, other)
    )
    assert incompatible.status_code == 200
    assert incompatible.json()["status"] == "incomparable_contract"
    assert incompatible.json()["reasons"] == ["different_symbol"]


def test_malformed_snapshot_contract_is_http_200_fail_closed(monkeypatch, tmp_path):
    db_path = str(tmp_path / "malformed-api.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    base = add_snapshot(db_path, suffix="base")
    malformed = add_snapshot(db_path, suffix="malformed", malformed=True)
    response = client.get(
        "/api/v2/analysis/snapshots/compare", params=compare_params(base, malformed)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "incomparable_contract"
    assert body["reasons"] == ["unsupported_comparison_snapshot_contract"]
    assert body["stored_deltas"] == []
    assert body["base_current_context"] is None
    assert body["comparison_current_context"] is None
    assert body["current_context_deltas"] == []


def test_integrity_failure_is_server_error_for_compare_and_detail(monkeypatch, tmp_path):
    db_path = str(tmp_path / "corrupt.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    base = add_snapshot(db_path, suffix="base")
    comparison = add_snapshot(db_path, suffix="comparison")
    with sqlite3.connect(db_path) as conn:
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='analysis_snapshots'"
        ).fetchall()
        for trigger in triggers:
            conn.execute(f'DROP TRIGGER "{trigger[0]}"')
        conn.execute(
            "UPDATE analysis_snapshots SET output_json = ? WHERE snapshot_id = ?",
            ('{"status":"corrupted"}', comparison["snapshot_id"]),
        )
    compared = client.get(
        "/api/v2/analysis/snapshots/compare", params=compare_params(base, comparison)
    )
    assert compared.status_code == 500
    assert compared.json()["detail"] == "invalid_snapshot_integrity"
    detail = client.get(f"/api/v2/analysis/snapshots/{comparison['snapshot_id']}")
    assert detail.status_code == 500
    assert detail.json()["detail"] == "invalid_snapshot_integrity"
