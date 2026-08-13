import sqlite3

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.research_workflow import ReviewAcknowledgment
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.research_workflow_repository import ResearchWorkflowRepository
from src.services.evidence_backup_service import EvidenceBackupService


def test_phase12_backup_restore_preserves_workflow_identity_and_idempotency(tmp_path):
    source = str(tmp_path / "source.db")
    backup = str(tmp_path / "backup.db")
    restored = str(tmp_path / "restored.db")
    workflow = ResearchWorkflowRepository(source)
    active = workflow.add_membership("2330")
    archived = workflow.add_membership("2317")
    workflow.archive(archived["watchlist_item_id"])
    snapshot = AnalysisSnapshotRepository(source).add(AnalysisSnapshot(
        symbol="2330.TW", knowledge_cutoff_at="2026-08-01T00:00:00Z",
        capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
        model_version="2.0.0", used_rule_versions={}, source_resource_versions=[],
        manual_approval_ids=[], output={"status": "available", "symbol": "2330.TW"},
        created_at="2026-08-02T00:00:00Z",
    ), "snapshot-key")
    command = ReviewAcknowledgment(
        active["watchlist_item_id"], snapshot["snapshot_id"],
        "2026-08-02T00:00:00Z", "review-key",
    )
    event = workflow.append_review_event(command, reviewed_at="2026-08-03T00:00:00Z")
    evidence = EvidenceBackupService.backup(source, backup)
    restored_evidence = EvidenceBackupService.restore(backup, restored)
    expected = {"membership_total": 2, "active_count": 1, "archived_count": 1, "review_event_count": 1}
    assert evidence["research_workflow_counts"] == expected
    assert restored_evidence["research_workflow_counts"] == expected
    retry = ResearchWorkflowRepository(restored).append_review_event(
        command, reviewed_at="2026-08-04T00:00:00Z"
    )
    assert retry["created"] is False and retry["review_event_id"] == event["review_event_id"]
    with sqlite3.connect(restored) as conn:
        row = conn.execute(
            "SELECT acknowledged_snapshot_id,comparison_cutoff_at,reviewed_at FROM research_review_events"
        ).fetchone()
    assert row == (
        snapshot["snapshot_id"], event["comparison_cutoff_at"], event["reviewed_at"]
    )
