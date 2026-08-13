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
