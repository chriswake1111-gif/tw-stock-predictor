from __future__ import annotations

import sqlite3

import pytest

from src.api.routes.v2_valuation import _build_v2_analysis
from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.research_workflow import ReviewAcknowledgment
from src.domain.valuation import (
    ApprovalResourceType,
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    ValuationApproval,
)
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.daily_research_read_repository import DailyResearchReadRepository
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.research_workflow_repository import ResearchWorkflowRepository
from src.services.daily_research_review_context_service import (
    DailyResearchReviewContextService,
)
from src.services.evidence_backup_service import EvidenceBackupService


def _authoritative_output(symbol: str, cutoff: str) -> dict:
    return {
        "status": "available",
        "symbol": symbol,
        "knowledge_cutoff_at": cutoff,
        "model": {"version": "2.0.0"},
        "valuation": {"status": "available", "target_matrix": []},
        "liquidity": {"status": "available"},
        "technical_support": {"status": "available", "scenarios": []},
        "target_confluence": {"status": "available", "overlap_ranges": []},
        "deployment_plan": {"status": "available", "plans": []},
        "screening": {"status": "available"},
        "data_quality": {"status": "available"},
    }


def _snapshot(
    repo: AnalysisSnapshotRepository,
    *,
    symbol: str,
    key: str,
    knowledge_cutoff_at: str = "2026-08-30T00:00:00Z",
    created_at: str = "2026-08-30T12:00:00Z",
    source_resource_versions: list[dict] | None = None,
) -> dict:
    return repo.add(
        AnalysisSnapshot(
            symbol=symbol,
            knowledge_cutoff_at=knowledge_cutoff_at,
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={},
            source_resource_versions=source_resource_versions or [],
            manual_approval_ids=[],
            output=_authoritative_output(symbol, knowledge_cutoff_at),
            created_at=created_at,
        ),
        key,
    )


def test_daily_dto_is_equivalent_after_evidence_backup_restore(tmp_path):
    source = str(tmp_path / "source.db")
    backup = str(tmp_path / "backup.db")
    restored = str(tmp_path / "restored.db")
    apply_valuation_migration(source)
    workflow = ResearchWorkflowRepository(source, auto_migrate=False)
    active = workflow.add_membership("2330.TW")
    archived = workflow.add_membership("6488.TWO")
    workflow.archive(archived["watchlist_item_id"])
    snapshot = _snapshot(
        AnalysisSnapshotRepository(source), symbol=active["symbol"], key="daily-backup-snapshot"
    )
    workflow.append_review_event(
        ReviewAcknowledgment(
            active["watchlist_item_id"],
            snapshot["snapshot_id"],
            "2026-08-30T00:00:00Z",
            "daily-backup-review",
        ),
        reviewed_at="2026-08-30T12:30:00Z",
    )

    source_dto = DailyResearchReviewContextService(source,).list(
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        request_received_at="2026-08-31T09:00:00Z",
        limit=25,
    )
    backed_up = EvidenceBackupService.backup(source, backup)
    restored_evidence = EvidenceBackupService.restore(backup, restored)
    restored_dto = DailyResearchReviewContextService(restored).list(
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        request_received_at="2026-08-31T09:00:00Z",
        limit=25,
    )

    assert backed_up["research_workflow_counts"] == restored_evidence["research_workflow_counts"]
    assert restored_dto == source_dto


class _NoWriteReadRepository(DailyResearchReadRepository):
    def _connect(self):
        conn = super()._connect()
        forbidden = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_TEMP_INDEX,
            sqlite3.SQLITE_CREATE_TEMP_TABLE,
            sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
            sqlite3.SQLITE_CREATE_TEMP_VIEW,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_INDEX,
            sqlite3.SQLITE_DROP_TEMP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_TRIGGER,
            sqlite3.SQLITE_DROP_TEMP_VIEW,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_VIEW,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_PRAGMA,
        }
        conn.set_authorizer(
            lambda action, *_args: sqlite3.SQLITE_DENY
            if action in forbidden
            else sqlite3.SQLITE_OK
        )
        return conn


def test_daily_read_passes_sqlite_no_write_authorizer(tmp_path):
    db_path = str(tmp_path / "authorizer.db")
    apply_valuation_migration(db_path)
    ResearchWorkflowRepository(db_path, auto_migrate=False).add_membership("2330.TW")
    response = DailyResearchReviewContextService(
        db_path,
        read_repository=_NoWriteReadRepository(db_path),
    ).list(
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        request_received_at="2026-08-31T09:00:00Z",
    )
    assert len(response["items"]) == 1


def test_daily_k_projection_ignores_post_k_dependency_approval_change(tmp_path):
    db_path = str(tmp_path / "post-k-freshness.db")
    apply_valuation_migration(db_path)
    workflow = ResearchWorkflowRepository(db_path, auto_migrate=False)
    workflow.add_membership("2330.TW")
    eps_repo = ForwardEPSRepository(db_path, auto_migrate=False)
    k_cutoff = "2026-08-01T00:00:00Z"
    request_time = "2026-08-10T00:00:00Z"
    first = eps_repo.add_forward_eps(
        ForwardEPSObservation(
            logical_series_id="2330-2027-broker-a",
            revision_number=1,
            symbol="2330.TW",
            fiscal_year=2027,
            eps_base=51,
            source_name="Broker A",
            source_type=ForwardEPSSourceType.BROKER_REPORT,
            published_at="2026-07-31",
            available_at="2026-07-31T08:00:00Z",
        ),
        "post-k-k-eps",
        ingested_at="2026-07-31T08:00:00Z",
    )
    first_approval = eps_repo.add_approval(
        ValuationApproval(
            approval_id="approval-k-eps",
            resource_type=ApprovalResourceType.FORWARD_EPS,
            resource_id=first["id"],
            decision=ApprovalStatus.APPROVED,
            rule_id="VAL-02",
            evidence_level="A",
            project_operationalization=False,
            approved_by="test-admin",
            rationale="source reviewed",
            available_at="2026-07-31T08:01:00Z",
        ),
        "post-k-k-approval",
        ingested_at="2026-07-31T08:01:00Z",
    )
    snapshot = _snapshot(
        AnalysisSnapshotRepository(db_path),
        symbol="2330.TW",
        key="post-k-snapshot",
        knowledge_cutoff_at=k_cutoff,
        created_at="2026-07-31T12:00:00Z",
        source_resource_versions=[{
            "section": "valuation",
            "resource_type": "forward_eps_revision",
            "resource_id": first["id"],
            "logical_resource_id": first["logical_series_id"],
            "revision_number": first["revision_number"],
            "available_at": first["available_at"],
            "ingested_at": first["ingested_at"],
            "approval_ids": [first_approval["approval_id"]],
        }],
    )
    service = DailyResearchReviewContextService(db_path)
    before = service.list(
        market_date="2026-08-01",
        knowledge_cutoff_at=k_cutoff,
        request_received_at=request_time,
    )

    second = eps_repo.add_forward_eps(
        ForwardEPSObservation(
            logical_series_id="2330-2027-broker-a",
            revision_number=2,
            revision_of=first["id"],
            symbol="2330.TW",
            fiscal_year=2027,
            eps_base=52,
            source_name="Broker A",
            source_type=ForwardEPSSourceType.BROKER_REPORT,
            published_at="2026-08-05",
            available_at="2026-08-05T08:00:00Z",
        ),
        "post-k-revision",
        ingested_at="2026-08-05T08:00:00Z",
    )
    eps_repo.add_approval(
        ValuationApproval(
            approval_id="approval-post-k-revision",
            resource_type=ApprovalResourceType.FORWARD_EPS,
            resource_id=second["id"],
            decision=ApprovalStatus.APPROVED,
            rule_id="VAL-02",
            evidence_level="A",
            project_operationalization=False,
            approved_by="test-admin",
            rationale="post-cutoff source reviewed",
            available_at="2026-08-05T08:01:00Z",
        ),
        "post-k-revision-approval",
        ingested_at="2026-08-05T08:01:00Z",
    )

    after = service.list(
        market_date="2026-08-01",
        knowledge_cutoff_at=k_cutoff,
        request_received_at=request_time,
    )
    assert snapshot["snapshot_id"] == before["items"][0]["latest_snapshot_reference"]["snapshot_id"]
    assert before == after
    assert before["items"][0]["freshness_status"] == "current"
    assert "snapshot_stale" not in before["items"][0]["reason_codes"]


def test_v2_refresh_analysis_supports_caller_owned_connection(tmp_path):
    db_path = str(tmp_path / "preloaded-analysis.db")
    apply_valuation_migration(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        result = _build_v2_analysis(
            "2330.TW",
            knowledge_cutoff_at="2026-08-31T09:00:00Z",
            auto_migrate=False,
            connection=conn,
        )
        assert result["symbol"] == "2330.TW"
        assert result["snapshot_id"] is None
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def _eligible_context(item_id: str) -> dict:
    return {
        "items": [{
            "watchlist_reference": {"watchlist_item_id": item_id},
            "status": "available",
            "review_blocked": False,
            "quality": {
                "phase14_status": "available",
                "phase15_status": "available",
                "phase16_status": "available",
            },
            "reason_codes": [],
            "provenance": {
                "current_reference": {
                    "contract_version": "daily_research_context_reference_v1",
                    "context_digest": "test-digest",
                }
            },
        }]
    }


def _refresh_analysis(symbol: str, cutoff: str) -> dict:
    return {
        "status": "available",
        "symbol": symbol,
        "knowledge_cutoff_at": cutoff,
        "model": {"version": "2.0.0"},
        "valuation": {"status": "available"},
        "liquidity": {"status": "available"},
        "technical_support": {"status": "available"},
        "screening": {"status": "available"},
        "target_confluence": {"status": "available"},
        "rules_used": [],
        "invalidation_conditions": [],
    }


def test_daily_refresh_uses_one_transaction_and_rolls_back_failed_builder(tmp_path):
    db_path = str(tmp_path / "refresh.db")
    apply_valuation_migration(db_path)
    item = ResearchWorkflowRepository(db_path, auto_migrate=False).add_membership("2330.TW")
    calls: list[tuple[int, bool]] = []

    def failing_builder(symbol, cutoff, _context, connection):
        calls.append((id(connection), connection.in_transaction))
        raise RuntimeError("forced_refresh_failure")

    service = DailyResearchReviewContextService(
        db_path,
        analysis_builder=failing_builder,
    )
    service._response = lambda _conn, **_kwargs: _eligible_context(
        item["watchlist_item_id"]
    )
    with pytest.raises(RuntimeError, match="forced_refresh_failure"):
        service.refresh_snapshot(
            item["watchlist_item_id"],
            market_date="2026-08-31",
            loaded_knowledge_cutoff_at="2026-08-31T00:00:00Z",
            expected_snapshot_id=None,
            advance_knowledge_cutoff=True,
            request_received_at="2026-08-31T09:00:00Z",
            idempotency_key="daily-refresh-rollback",
        )
    assert calls and calls[0][1] is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshot_idempotency_keys"
        ).fetchone()[0] == 0


def test_daily_refresh_commits_snapshot_and_ledger_atomically(tmp_path):
    db_path = str(tmp_path / "refresh-success.db")
    apply_valuation_migration(db_path)
    item = ResearchWorkflowRepository(db_path, auto_migrate=False).add_membership("2330.TW")
    calls: list[tuple[int, bool]] = []

    def successful_builder(symbol, cutoff, _context, connection):
        calls.append((id(connection), connection.in_transaction))
        return _refresh_analysis(symbol, cutoff)

    service = DailyResearchReviewContextService(
        db_path,
        analysis_builder=successful_builder,
    )
    service._response = lambda _conn, **_kwargs: _eligible_context(
        item["watchlist_item_id"]
    )
    result = service.refresh_snapshot(
        item["watchlist_item_id"],
        market_date="2026-08-31",
        loaded_knowledge_cutoff_at="2026-08-31T00:00:00Z",
        expected_snapshot_id=None,
        advance_knowledge_cutoff=True,
        request_received_at="2026-08-31T09:00:00Z",
        idempotency_key="daily-refresh-success",
    )
    assert result["created"] is True
    assert calls and calls[0][1] is True
    with sqlite3.connect(db_path) as conn:
        snapshot_count = conn.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0]
        ledger_count = conn.execute(
            "SELECT COUNT(*) FROM analysis_snapshot_idempotency_keys"
        ).fetchone()[0]
    assert (snapshot_count, ledger_count) == (1, 1)
