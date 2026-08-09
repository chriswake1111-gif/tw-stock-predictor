from fastapi.testclient import TestClient

from src.api.main import app
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository


client = TestClient(app)


def add_snapshot(
    db_path, *, mode=CaptureMode.HISTORICAL_RECONSTRUCTION, suffix="one", symbol="2317.TW"
):
    return AnalysisSnapshotRepository(str(db_path)).add(
        AnalysisSnapshot(
            symbol=symbol,
            knowledge_cutoff_at="2025-01-01T02:00:00Z",
            capture_mode=mode,
            model_version="2.0.0",
            used_rule_versions={"VAL-01": "2.0.0"},
            source_resource_versions=[],
            manual_approval_ids=["approval-val"],
            output={
                "status": "partial",
                "target_confluence": {
                    "supporting_methods": [{
                        "candidate_id": f"valuation:{suffix}",
                        "method_family": "VAL-01",
                        "rule_id": "VAL-01",
                        "rule_version": "2.0.0",
                        "semantic_role": "target",
                        "price_low": "150",
                        "price_high": "170",
                        "source_resource_ids": [f"eps:{suffix}"],
                        "approval_ids": ["approval-val"],
                    }],
                    "overlap_ranges": [],
                },
            },
            created_at="2026-08-10T00:00:00Z",
        ),
        f"snapshot-{suffix}",
    )


def headers(key, idempotency="phase8-run"):
    return {"X-Admin-API-Key": key, "Idempotency-Key": idempotency}


def request(snapshot_ids):
    return {
        "snapshot_ids": snapshot_ids,
        "evaluation_profile_acknowledgement": "phase8_mvp_v1",
    }


def test_phase8_run_write_is_default_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "closed.db"))
    monkeypatch.delenv("EVIDENCE_V2_WRITES_ENABLED", raising=False)
    response = client.post("/api/v2/evaluations/runs", headers=headers("bad"), json=request(["x"]))
    assert response.status_code == 503
    assert response.json()["detail"] == "evidence_v2_writes_disabled"


def test_phase8_protected_run_exact_get_and_origin_separated_summary(monkeypatch, tmp_path):
    db_path = tmp_path / "phase8.db"
    key = "phase8-secret"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", key)
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase8-reviewer")
    historical = add_snapshot(db_path, suffix="historical")
    prospective = add_snapshot(
        db_path, mode=CaptureMode.LIVE_REFRESH, suffix="prospective"
    )
    bad = client.post(
        "/api/v2/evaluations/runs", headers=headers("wrong"),
        json=request([historical["snapshot_id"]]),
    )
    assert bad.status_code == 401
    response = client.post(
        "/api/v2/evaluations/runs", headers=headers(key),
        json=request([historical["snapshot_id"], prospective["snapshot_id"]]),
    )
    assert response.status_code == 200, response.text
    run = response.json()["evaluation_run"]
    assert run["created"] is True
    assert run["evaluation_profile"]["approval_id"]
    assert run["outcome_manifest"]["dataset_hash"]
    assert "payload_fingerprint" not in str(run)
    duplicate = client.post(
        "/api/v2/evaluations/runs", headers=headers(key),
        json=request([historical["snapshot_id"], prospective["snapshot_id"]]),
    )
    duplicate.raise_for_status()
    assert duplicate.json()["evaluation_run"]["created"] is False
    same_semantic_new_key = client.post(
        "/api/v2/evaluations/runs", headers=headers(key, "phase8-run-alias"),
        json=request([historical["snapshot_id"], prospective["snapshot_id"]]),
    )
    same_semantic_new_key.raise_for_status()
    assert same_semantic_new_key.json()["evaluation_run"]["evaluation_run_id"] == run["evaluation_run_id"]
    changed_payload = client.post(
        "/api/v2/evaluations/runs", headers=headers(key),
        json=request([historical["snapshot_id"]]),
    )
    assert changed_payload.status_code == 409
    assert "idempotency key" in changed_payload.json()["detail"]
    run_id = run["evaluation_run_id"]
    stored = client.get(f"/api/v2/evaluations/runs/{run_id}")
    results = client.get(f"/api/v2/evaluations/runs/{run_id}/results")
    stored.raise_for_status()
    results.raise_for_status()
    assert stored.json()["evaluation_run"]["results"] == results.json()["results"]
    summary = client.get(
        "/api/v2/performance/summary", params={"evaluation_run_id": run_id}
    )
    summary.raise_for_status()
    origins = {
        item["evaluation_origin"]
        for item in summary.json()["performance_summary"]["groups"]
    }
    assert origins == {"prospective_snapshot", "historical_reconstruction"}
    assert all("numerator" in item and "denominator" in item and "n" in item for item in summary.json()["performance_summary"]["groups"])


def test_phase8_profile_acknowledgement_fails_closed(monkeypatch, tmp_path):
    db_path = tmp_path / "closed-input.db"
    key = "phase8-secret"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", key)
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase8-reviewer")
    snapshot = add_snapshot(db_path)
    payload = request([snapshot["snapshot_id"]])
    payload["evaluation_profile_acknowledgement"] = "not-acknowledged"
    response = client.post(
        "/api/v2/evaluations/runs", headers=headers(key, "bad-profile"), json=payload
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "phase8_evaluation_profile_acknowledgement_required"


def test_quality_warning_stays_in_coverage_but_not_target_rate_denominator(monkeypatch, tmp_path):
    db_path = tmp_path / "warning.db"
    key = "phase8-secret"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", key)
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase8-reviewer")
    snapshot = add_snapshot(db_path, symbol="2308.TW", suffix="warning")
    response = client.post(
        "/api/v2/evaluations/runs", headers=headers(key, "warning-run"),
        json=request([snapshot["snapshot_id"]]),
    )
    response.raise_for_status()
    run_id = response.json()["evaluation_run"]["evaluation_run_id"]
    summary = client.get(
        "/api/v2/performance/summary", params={"evaluation_run_id": run_id}
    ).json()["performance_summary"]
    assert summary["coverage"]["eligible_snapshots"] == 14
    assert summary["coverage"]["analyzable_snapshots"] == 1
    assert summary["coverage"]["quality_warning"] == 2
    assert all(group["denominator"] == 0 for group in summary["groups"])
    assert all(group["historical_target_reach_rate"] is None for group in summary["groups"])
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not {"prediction_probability", "success_probability", "model_ranking"}.intersection(keys(summary))
