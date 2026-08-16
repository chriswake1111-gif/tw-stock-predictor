import sqlite3
from datetime import datetime, timezone

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.services.research_review_service import ResearchReviewService


NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _snapshot(created_at="2026-08-02T00:00:00Z", marker="same"):
    return AnalysisSnapshot(
        symbol="2330.TW", knowledge_cutoff_at="2026-08-01T00:00:00Z",
        capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
        model_version="2.0.0", used_rule_versions={}, source_resource_versions=[],
        manual_approval_ids=[], output={"status": "available", "symbol": "2330.TW", "marker": marker},
        created_at=created_at,
    )


def test_queue_no_snapshot_and_baseline_not_set(tmp_path):
    service = ResearchReviewService(str(tmp_path / "states.db"))
    service.workflow.add_membership("2330")
    assert service.queue(comparison_cutoff="2026-08-10T00:00:00Z", request_received_at=NOW)["items"][0]["review_state"] == "no_snapshot"
    AnalysisSnapshotRepository(service.db_path).add(_snapshot(), "snap")
    item = service.queue(comparison_cutoff="2026-08-10T00:00:00Z", request_received_at=NOW)["items"][0]
    assert item["review_state"] == "baseline_not_set"
    assert item["comparison_has_deltas"] is None


def test_ac57_same_snapshot_stale_is_false_not_temporal_change(tmp_path, monkeypatch):
    service = ResearchReviewService(str(tmp_path / "ac57.db"))
    membership = service.workflow.add_membership("2330")
    snapshot = AnalysisSnapshotRepository(service.db_path).add(_snapshot(), "snap")
    service.acknowledge(
        membership["watchlist_item_id"], snapshot_id=snapshot["snapshot_id"],
        comparison_cutoff="2026-08-03T00:00:00Z", idempotency_key="review",
        request_received_at=NOW,
    )
    monkeypatch.setattr(service.comparison, "compare_with_connection", lambda *args, **kwargs: {
        "status": "available", "stored_deltas": [], "current_context_deltas": [],
        "base_current_context": {"freshness_status": "stale"},
        "comparison_current_context": {"freshness_status": "stale"},
        "reasons": [],
    })
    item = service.queue(
        comparison_cutoff="2026-08-10T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert item["review_state"] == "comparable_without_deltas"
    assert item["comparison_has_deltas"] is False
    assert item["freshness_status"] == "stale"


def test_actual_latest_ordering_and_future_cutoff_fail_closed(tmp_path):
    service = ResearchReviewService(str(tmp_path / "latest.db"))
    service.workflow.add_membership("2330")
    repo = AnalysisSnapshotRepository(service.db_path)
    older = repo.add(_snapshot(marker="old"), "old")
    newer = repo.add(_snapshot(created_at="2026-08-04T00:00:00Z", marker="new"), "new")
    item = service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert item["latest_snapshot_reference"]["snapshot_id"] == newer["snapshot_id"]
    assert item["latest_snapshot_reference"]["snapshot_id"] != older["snapshot_id"]
    try:
        service.queue(comparison_cutoff="2026-08-14T00:00:00Z", request_received_at=NOW)
    except ValueError as exc:
        assert str(exc) == "comparison_cutoff_in_future"
    else:
        raise AssertionError("future cutoff must fail closed")


def test_actual_latest_tie_uses_snapshot_id_descending(tmp_path):
    service = ResearchReviewService(str(tmp_path / "tie.db"))
    service.workflow.add_membership("2330")
    repo = AnalysisSnapshotRepository(service.db_path)
    first = repo.add(_snapshot(marker="first"), "tie-first")
    second = repo.add(_snapshot(marker="second"), "tie-second")
    expected = max(first["snapshot_id"], second["snapshot_id"])
    item = service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert item["latest_snapshot_reference"]["snapshot_id"] == expected


def test_actual_latest_integrity_failure_does_not_fall_back(tmp_path):
    service = ResearchReviewService(str(tmp_path / "integrity.db"))
    membership = service.workflow.add_membership("2330")
    repo = AnalysisSnapshotRepository(service.db_path)
    older = repo.add(_snapshot(marker="old"), "old")
    newer = repo.add(_snapshot(created_at="2026-08-04T00:00:00Z", marker="new"), "new")
    service.acknowledge(
        membership["watchlist_item_id"], snapshot_id=older["snapshot_id"],
        comparison_cutoff="2026-08-03T00:00:00Z", idempotency_key="review",
        request_received_at=NOW,
    )
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("DROP TRIGGER analysis_snapshots_no_update")
        conn.execute(
            "UPDATE analysis_snapshots SET output_json=? WHERE snapshot_id=?",
            ('{"status":"available","symbol":"2330.TW","tampered":true}', newer["snapshot_id"]),
        )
    item = service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert item["latest_snapshot_reference"]["snapshot_id"] == newer["snapshot_id"]
    assert item["latest_snapshot_reference"]["snapshot_id"] != older["snapshot_id"]
    assert item["review_state"] == "snapshot_integrity_error"
    assert item["comparison_has_deltas"] is None


def test_no_review_event_precedes_latest_integrity_failure(tmp_path):
    service = ResearchReviewService(str(tmp_path / "precedence.db"))
    service.workflow.add_membership("2330")
    snapshot = AnalysisSnapshotRepository(service.db_path).add(_snapshot(), "snapshot")
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("DROP TRIGGER analysis_snapshots_no_update")
        conn.execute(
            "UPDATE analysis_snapshots SET output_json=? WHERE snapshot_id=?",
            ('{"status":"available","tampered":true}', snapshot["snapshot_id"]),
        )
    item = service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert item["review_state"] == "baseline_not_set"
    assert item["comparison_status"] == "not_run"
    assert item["comparison_has_deltas"] is None


def test_incompatible_latest_is_used_without_compatible_fallback(tmp_path, monkeypatch):
    service = ResearchReviewService(str(tmp_path / "incompatible.db"))
    membership = service.workflow.add_membership("2330")
    repo = AnalysisSnapshotRepository(service.db_path)
    baseline = repo.add(_snapshot(marker="baseline"), "baseline")
    latest = repo.add(_snapshot(created_at="2026-08-04T00:00:00Z", marker="latest"), "latest")
    service.acknowledge(
        membership["watchlist_item_id"], snapshot_id=baseline["snapshot_id"],
        comparison_cutoff="2026-08-03T00:00:00Z", idempotency_key="review",
        request_received_at=NOW,
    )
    captured = {}
    def incomparable(*args, **kwargs):
        captured.update(kwargs)
        return {
            "status": "incomparable_contract", "stored_deltas": [],
            "current_context_deltas": [], "base_current_context": None,
            "comparison_current_context": None, "reasons": ["contract_mismatch"],
        }
    monkeypatch.setattr(service.comparison, "compare_with_connection", incomparable)
    item = service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert captured["comparison_snapshot_id"] == latest["snapshot_id"]
    assert item["review_state"] == "incomparable_contract"
    assert item["comparison_status"] == "incomparable_contract"
    assert item["comparison_has_deltas"] is None


def test_baseline_integrity_failure_and_missing_baseline_fail_closed(tmp_path):
    service = ResearchReviewService(str(tmp_path / "baseline.db"))
    membership = service.workflow.add_membership("2330")
    repo = AnalysisSnapshotRepository(service.db_path)
    baseline = repo.add(_snapshot(marker="baseline"), "baseline")
    latest = repo.add(
        _snapshot(created_at="2026-08-04T00:00:00Z", marker="valid-latest"),
        "valid-latest",
    )
    service.acknowledge(
        membership["watchlist_item_id"], snapshot_id=baseline["snapshot_id"],
        comparison_cutoff="2026-08-03T00:00:00Z", idempotency_key="review",
        request_received_at=NOW,
    )
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("DROP TRIGGER analysis_snapshots_no_update")
        conn.execute(
            "UPDATE analysis_snapshots SET output_json=? WHERE snapshot_id=?",
            ('{"status":"available","tampered":true}', baseline["snapshot_id"]),
        )
    assert repo.get(latest["snapshot_id"])["output"]["marker"] == "valid-latest"
    integrity = service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert integrity["latest_snapshot_reference"]["snapshot_id"] == latest["snapshot_id"]
    assert integrity["review_state"] == "snapshot_integrity_error"
    assert integrity["comparison_has_deltas"] is None

    missing_service = ResearchReviewService(str(tmp_path / "missing-baseline.db"))
    missing_item = missing_service.workflow.add_membership("2330")
    missing_repo = AnalysisSnapshotRepository(missing_service.db_path)
    missing_snapshot = missing_repo.add(_snapshot(), "baseline")
    missing_service.acknowledge(
        missing_item["watchlist_item_id"], snapshot_id=missing_snapshot["snapshot_id"],
        comparison_cutoff="2026-08-03T00:00:00Z", idempotency_key="review",
        request_received_at=NOW,
    )
    missing_repo.add(
        _snapshot(created_at="2026-08-04T00:00:00Z", marker="latest"), "latest"
    )
    with sqlite3.connect(missing_service.db_path) as conn:
        conn.execute("DROP TRIGGER analysis_snapshots_no_delete")
        conn.execute("DELETE FROM analysis_snapshots WHERE snapshot_id=?", (missing_snapshot["snapshot_id"],))
    missing = missing_service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )["items"][0]
    assert missing["review_state"] == "blocked"
    assert missing["reason_codes"] == ["acknowledged_snapshot_missing"]
    assert missing["comparison_has_deltas"] is None


def test_comparison_state_matrix_uses_typed_unavailable(tmp_path, monkeypatch):
    service = ResearchReviewService(str(tmp_path / "matrix.db"))
    membership = service.workflow.add_membership("2330")
    snapshot = AnalysisSnapshotRepository(service.db_path).add(_snapshot(), "snapshot")
    service.acknowledge(
        membership["watchlist_item_id"], snapshot_id=snapshot["snapshot_id"],
        comparison_cutoff="2026-08-03T00:00:00Z", idempotency_key="review",
        request_received_at=NOW,
    )
    cases = (
        ("blocked", [], [], "blocked", "unavailable", None),
        ("unknown", [], [], "unknown", "unavailable", None),
        ("current", [{"change_type": "stored_changed"}], [], "comparable_with_deltas", "comparable", True),
        ("current", [], [{"change_type": "dependency_changed"}], "comparable_with_deltas", "comparable", True),
    )
    for freshness, stored, current, review_state, comparison_status, has_deltas in cases:
        monkeypatch.setattr(service.comparison, "compare_with_connection", lambda *args, **kwargs: {
            "status": "available", "stored_deltas": stored,
            "current_context_deltas": current,
            "base_current_context": {"freshness_status": freshness},
            "comparison_current_context": {"freshness_status": freshness},
            "reasons": [],
        })
        item = service.queue(
            comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
        )["items"][0]
        assert item["review_state"] == review_state
        assert item["comparison_status"] == comparison_status
        assert item["comparison_has_deltas"] is has_deltas


def test_list_is_summary_detail_has_comparison_and_one_read_connection(tmp_path, monkeypatch):
    service = ResearchReviewService(str(tmp_path / "summary-detail.db"))
    membership = service.workflow.add_membership("2330")
    snapshot = AnalysisSnapshotRepository(service.db_path).add(_snapshot(), "snapshot")
    service.acknowledge(
        membership["watchlist_item_id"], snapshot_id=snapshot["snapshot_id"],
        comparison_cutoff="2026-08-03T00:00:00Z", idempotency_key="review",
        request_received_at=NOW,
    )
    response = {
        "status": "available", "stored_deltas": [], "current_context_deltas": [],
        "base_current_context": {"freshness_status": "current"},
        "comparison_current_context": {"freshness_status": "current"},
        "reasons": [],
    }
    monkeypatch.setattr(service.comparison, "compare_with_connection", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        service.comparison, "_connect",
        lambda: (_ for _ in ()).throw(AssertionError("hidden comparison connection")),
    )
    original_connect = service._connect
    connections = []
    def counted_connect():
        connections.append("opened")
        return original_connect()
    monkeypatch.setattr(service, "_connect", counted_connect)
    queue = service.queue(
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW
    )
    assert "comparison" not in queue["items"][0]
    assert len(connections) == 1
    detail = service.detail(
        membership["watchlist_item_id"],
        comparison_cutoff="2026-08-05T00:00:00Z", request_received_at=NOW,
    )
    assert detail["comparison"] == response
    assert len(connections) == 2
