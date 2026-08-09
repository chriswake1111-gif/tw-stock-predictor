from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256

from fastapi.testclient import TestClient

from src.api.main import app
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.performance_validation import OutcomeResourceManifest
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.services.fixed_outcome_dataset import OutcomeBar, VerifiedOutcomeDataset


client = TestClient(app)
HASH = sha256(b"phase8-api-fixed-outcome").hexdigest()


def install_fixed_outcome_fixture(monkeypatch):
    dates = []
    current = date(2025, 1, 1)
    while len(dates) < 80:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    bars = tuple(
        OutcomeBar(day, Decimal("100"), Decimal("105"), Decimal("95"), Decimal("101"))
        for day in dates
    )
    manifest = OutcomeResourceManifest(
        manifest_id="phase8-api-test-manifest", manifest_version=1,
        dataset_name="phase8-api-fixed-fixture", provider="test fixture",
        dataset_hash=HASH, date_start=dates[0], date_end=dates[-1],
        universe_definition="phase2_fixed_14_symbol_research_cohort",
        calendar_resource={"policy": "fixed_test_calendar"}, calendar_hash=HASH,
        outcome_observed_through_session=dates[-1],
        benchmark_resource={"symbol": "^TWII"}, benchmark_hash=HASH,
        ohlc_adjustment_contract="provider_compatible_adjusted_v1",
        corporate_action_contract="fixed_test_adjusted_history",
        symbol_resources=(
            {"symbol": "2317.TW", "path": "2317.csv", "sha256": HASH, "quality_status": "available"},
            {"symbol": "2308.TW", "path": "2308.csv", "sha256": HASH, "quality_status": "quality_warning"},
        ),
        ingested_at="2026-08-09T16:00:00Z", created_at="2026-08-09T16:00:00Z",
    )
    fixed = VerifiedOutcomeDataset(
        manifest=manifest, calendar=tuple(dates),
        bars_by_symbol={"2317.TW": bars, "2308.TW": bars}, benchmark_bars=bars,
    )
    monkeypatch.setattr(
        "src.services.performance_validation_service.FixedPhase2OutcomeAdapter.load",
        lambda self, *, ingested_at: fixed,
    )


def add_snapshot(
    db_path, *, mode=CaptureMode.HISTORICAL_RECONSTRUCTION, suffix="one",
    symbol="2317.TW", with_eligible_subject=True,
):
    supporting_methods = []
    if with_eligible_subject:
        supporting_methods = [{
            "candidate_id": f"valuation:{suffix}",
            "method_family": "VAL-01",
            "rule_id": "VAL-01",
            "rule_version": "2.0.0",
            "semantic_role": "target",
            "price_low": "150",
            "price_high": "170",
            "source_resource_ids": [f"eps:{suffix}"],
            "approval_ids": ["approval-val"],
        }]
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
                    "supporting_methods": supporting_methods,
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
    install_fixed_outcome_fixture(monkeypatch)
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
    install_fixed_outcome_fixture(monkeypatch)
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
    coverage = summary["coverage"]
    assert coverage["cohort_symbol_count"] == 14
    assert coverage["cohort_symbols_represented"] == 1
    assert coverage["requested_snapshot_count"] == 1
    assert coverage["snapshots_with_eligible_subjects"] == 1
    assert coverage["snapshots_without_eligible_subjects"] == 0
    assert coverage["eligible_subject_count"] == 1
    assert coverage["evaluated_subject_count"] == 1
    assert coverage["evaluation_record_count"] == 2
    assert coverage["quality_warning_snapshot_count"] == 1
    assert coverage["quality_warning_evaluation_record_count"] == 2
    assert coverage["evaluation_record_status_counts"] == {"quality_warning": 2}
    assert all(group["denominator"] == 0 for group in summary["groups"])
    assert all(group["historical_target_reach_rate"] is None for group in summary["groups"])
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not {"prediction_probability", "success_probability", "model_ranking"}.intersection(keys(summary))


def test_run_preserves_snapshot_without_eligible_subjects(monkeypatch, tmp_path):
    install_fixed_outcome_fixture(monkeypatch)
    db_path = tmp_path / "empty-membership.db"
    key = "phase8-secret"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", key)
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase8-reviewer")
    snapshot = add_snapshot(
        db_path, suffix="empty", with_eligible_subject=False
    )
    response = client.post(
        "/api/v2/evaluations/runs", headers=headers(key, "empty-run"),
        json=request([snapshot["snapshot_id"]]),
    )
    response.raise_for_status()
    run = response.json()["evaluation_run"]
    assert run["results"] == []
    assert run["snapshot_memberships"][0]["membership_status"] == "no_eligible_subjects"
    summary = client.get(
        "/api/v2/performance/summary",
        params={"evaluation_run_id": run["evaluation_run_id"]},
    ).json()["performance_summary"]
    assert summary["coverage"]["requested_snapshot_count"] == 1
    assert summary["coverage"]["snapshots_with_eligible_subjects"] == 0
    assert summary["coverage"]["snapshots_without_eligible_subjects"] == 1
    assert summary["coverage"]["evaluation_record_count"] == 0


def test_coverage_counts_twenty_requested_snapshots_without_unit_mixing(
    monkeypatch, tmp_path
):
    install_fixed_outcome_fixture(monkeypatch)
    db_path = tmp_path / "twenty.db"
    key = "phase8-secret"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("EVIDENCE_V2_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_API_KEY", key)
    monkeypatch.setenv("EVIDENCE_V2_ADMIN_ACTOR", "phase8-reviewer")
    snapshots = [add_snapshot(db_path, suffix=f"coverage-{index}") for index in range(20)]
    response = client.post(
        "/api/v2/evaluations/runs", headers=headers(key, "twenty-run"),
        json=request([item["snapshot_id"] for item in snapshots]),
    )
    response.raise_for_status()
    run_id = response.json()["evaluation_run"]["evaluation_run_id"]
    coverage = client.get(
        "/api/v2/performance/summary", params={"evaluation_run_id": run_id}
    ).json()["performance_summary"]["coverage"]
    assert coverage["cohort_symbol_count"] == 14
    assert coverage["cohort_symbols_represented"] == 1
    assert coverage["requested_snapshot_count"] == 20
    assert coverage["snapshots_with_eligible_subjects"] == 20
    assert coverage["eligible_subject_count"] == 20
    assert coverage["evaluation_record_count"] == 40
