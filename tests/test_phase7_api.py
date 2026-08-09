import sqlite3

from fastapi.testclient import TestClient

from src.api.main import app
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository


client = TestClient(app)


def headers(key, idempotency):
    return {
        "X-Admin-API-Key": key,
        "Idempotency-Key": idempotency,
    }


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "phase7-api.db"))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", "phase7-secret")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase7-admin")
    return "phase7-secret"


def profile_payload():
    return {
        "logical_profile_id": "phase7-default",
        "revision_number": 1,
        "scope": "global",
        "allowed_method_families": ["VAL-01", "FB-03", "FB-04"],
        "overlap_tolerance": "0.01",
        "evidence_strength_policy": [
            {"minimum_independent_target_components": 2, "label": "moderate"},
            {"minimum_independent_target_components": 3, "label": "high"},
        ],
        "calculation_quantum": "0.0001",
        "display_quantum": "0.01",
        "available_at": "2026-08-01T00:00:00Z",
        "rationale": "reviewed deterministic overlap policy",
    }


def create_approved_inputs(monkeypatch, tmp_path):
    key = configure(monkeypatch, tmp_path)
    eps = client.post(
        "/api/v2/forward-eps",
        headers=headers(key, "eps-create"),
        json={
            "logical_series_id": "eps-series",
            "revision_number": 1,
            "symbol": "2330.TW",
            "fiscal_year": 2027,
            "eps_low": 5,
            "eps_base": 5,
            "eps_high": 5,
            "source_name": "manual-research",
            "source_type": "manual",
            "published_at": "2026-08-01",
            "available_at": "2026-08-01T00:00:00Z",
        },
    ).json()["observation"]["id"]
    client.post(
        f"/api/v2/forward-eps/{eps}/approval",
        headers=headers(key, "eps-approve"),
        json={
            "decision": "approved",
            "rule_id": "VAL-02",
            "rationale": "reviewed forward estimate",
            "available_at": "2026-08-01T01:00:00Z",
        },
    ).raise_for_status()
    pe = client.post(
        "/api/v2/pe-scenarios",
        headers=headers(key, "pe-create"),
        json={
            "logical_series_id": "pe-series",
            "revision_number": 1,
            "label": "approved symbol PE",
            "pe_value": 20,
            "rationale": "company specific review",
            "scope": "symbol",
            "symbol": "2330.TW",
            "available_at": "2026-08-01T00:00:00Z",
        },
    ).json()["pe_scenario"]["id"]
    client.post(
        f"/api/v2/pe-scenarios/{pe}/approval",
        headers=headers(key, "pe-approve"),
        json={
            "decision": "approved",
            "rule_id": "VAL-04",
            "rationale": "reviewed company PE",
            "available_at": "2026-08-01T01:00:00Z",
        },
    ).raise_for_status()
    anchor = client.post(
        "/api/v2/anchors",
        headers=headers(key, "anchor-create"),
        json={
            "logical_anchor_set_id": "anchor-series",
            "revision_number": 1,
            "symbol": "2330.TW",
            "evidence_basis_rule_id": "FB-03",
            "anchors": [
                {"role": "origin", "price": 50, "market_date": "2026-01-01"},
                {"role": "swing_end", "price": 75, "market_date": "2026-02-01"},
                {"role": "projection_origin", "price": 75, "market_date": "2026-03-01"},
            ],
            "available_at": "2026-08-01T00:00:00Z",
            "source": "manual-review",
        },
    ).json()["anchor_set"]["id"]
    client.post(
        f"/api/v2/anchors/{anchor}/approval",
        headers=headers(key, "anchor-approve"),
        json={
            "decision": "approved",
            "rule_id": "FB-03",
            "rationale": "reviewed equal move anchor",
            "approved_at": "2026-08-01T01:00:00Z",
        },
    ).raise_for_status()
    profile_response = client.post(
        "/api/v2/synthesis-profiles",
        headers=headers(key, "profile-create"),
        json=profile_payload(),
    )
    profile_response.raise_for_status()
    profile_id = profile_response.json()["synthesis_profile"]["id"]
    client.post(
        f"/api/v2/synthesis-profiles/{profile_id}/approval",
        headers=headers(key, "profile-approve"),
        json={
            "decision": "approved",
            "rationale": "human approved TGT-01 operationalization",
            "approved_at": "2026-08-01T01:00:00Z",
        },
    ).raise_for_status()
    return key, profile_id


def test_phase7_writes_are_default_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "closed.db"))
    monkeypatch.delenv("EVIDENCE_V2_WRITES_ENABLED", raising=False)
    response = client.post(
        "/api/v2/synthesis-profiles",
        headers=headers("wrong", "closed"),
        json=profile_payload(),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "evidence_v2_writes_disabled"


def test_analysis_confluence_refresh_and_exact_snapshot_retrieval(monkeypatch, tmp_path):
    key, profile_id = create_approved_inputs(monkeypatch, tmp_path)
    analysis = client.get(
        "/api/v2/analysis/2330.TW",
        params={
            "knowledge_cutoff_at": "2026-08-10T00:00:00Z",
            "synthesis_profile_revision_id": profile_id,
        },
    )
    analysis.raise_for_status()
    body = analysis.json()
    assert body["snapshot_id"] is None
    assert body["target_confluence"]["status"] == "available"
    assert body["target_confluence"]["support_count"] == 2
    assert body["target_confluence"]["independent_method_count"] == 2
    assert body["target_confluence"]["summary_policy"] == "maximum_cluster_strength"
    refresh = client.post(
        "/api/v2/analysis/2330.TW/refresh",
        params={"knowledge_cutoff_at": "2026-08-10T00:00:00Z"},
        headers=headers(key, "snapshot-create"),
        json={"synthesis_profile_revision_id": profile_id},
    )
    refresh.raise_for_status()
    snapshot = refresh.json()["snapshot"]
    assert snapshot["capture_mode"] == "historical_reconstruction"
    assert snapshot["output"]["snapshot_id"] == snapshot["snapshot_id"]
    assert "payload_fingerprint" not in snapshot
    assert "idempotency_key" not in snapshot
    loaded = client.get(
        f"/api/v2/analysis/snapshots/{snapshot['snapshot_id']}"
    )
    loaded.raise_for_status()
    assert loaded.json()["snapshot"]["output"] == snapshot["output"]
    assert loaded.json()["snapshot"]["output_sha256"] == snapshot["output_sha256"]


def test_refresh_idempotency_key_is_permanently_bound(monkeypatch, tmp_path):
    key = configure(monkeypatch, tmp_path)
    first = client.post(
        "/api/v2/analysis/2330.TW/refresh",
        params={"knowledge_cutoff_at": "2026-08-10T00:00:00Z"},
        headers=headers(key, "bound-refresh"),
        json={},
    )
    first.raise_for_status()
    conflict = client.post(
        "/api/v2/analysis/2317.TW/refresh",
        params={"knowledge_cutoff_at": "2026-08-10T00:00:00Z"},
        headers=headers(key, "bound-refresh"),
        json={},
    )
    assert conflict.status_code == 409


def test_server_controls_live_capture_mode_and_rejects_caller_override(monkeypatch, tmp_path):
    key = configure(monkeypatch, tmp_path)
    live = client.post(
        "/api/v2/analysis/2330.TW/refresh",
        headers=headers(key, "live-refresh"),
        json={},
    )
    live.raise_for_status()
    assert live.json()["snapshot"]["capture_mode"] == "live_refresh"
    forged = client.post(
        "/api/v2/analysis/2330.TW/refresh",
        headers=headers(key, "forged-mode"),
        json={"capture_mode": "live_refresh"},
    )
    assert forged.status_code == 422


def test_get_analysis_rejects_conflicting_synthesis_selectors(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "get-conflict.db"))
    response = client.get(
        "/api/v2/analysis/2330.TW",
        params={
            "logical_synthesis_profile_id": "profile-a",
            "synthesis_profile_revision_id": "profile-b-revision",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "synthesis_profile_selectors_are_mutually_exclusive"
    )


def test_refresh_selector_conflict_creates_no_snapshot_or_idempotency_binding(
    monkeypatch, tmp_path
):
    key = configure(monkeypatch, tmp_path)
    db_path = tmp_path / "phase7-api.db"
    AnalysisSnapshotRepository(str(db_path))
    with sqlite3.connect(db_path) as conn:
        before_snapshots = conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshots"
        ).fetchone()[0]
        before_bindings = conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshot_idempotency_keys"
        ).fetchone()[0]
    response = client.post(
        "/api/v2/analysis/2330.TW/refresh",
        headers=headers(key, "selector-conflict-refresh"),
        json={
            "logical_synthesis_profile_id": "profile-a",
            "synthesis_profile_revision_id": "profile-b-revision",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "synthesis_profile_selectors_are_mutually_exclusive"
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshots"
        ).fetchone()[0] == before_snapshots
        assert conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshot_idempotency_keys"
        ).fetchone()[0] == before_bindings
