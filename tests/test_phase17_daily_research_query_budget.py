from __future__ import annotations

from pathlib import Path

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.daily_research_review_context import DAILY_RESEARCH_REVIEW_CONTEXT_VERSION
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.daily_research_read_repository import DailyResearchReadRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.research_workflow_repository import ResearchWorkflowRepository
from src.services.daily_research_review_context_service import DailyResearchReviewContextService


class TracingDailyResearchReadRepository(DailyResearchReadRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.statements: list[str] = []

    def _connect(self):
        conn = super()._connect()
        conn.set_trace_callback(self.statements.append)
        return conn


def test_daily_read_stays_within_bounded_query_budget(tmp_path: Path):
    db_path = str(tmp_path / "daily-query-budget.db")
    apply_valuation_migration(db_path)
    workflow = ResearchWorkflowRepository(db_path)
    memberships = [workflow.add_membership(str(2301 + offset)) for offset in range(25)]
    snapshots = AnalysisSnapshotRepository(db_path)
    for offset, membership in enumerate(memberships):
        snapshots.add(
            AnalysisSnapshot(
                symbol=membership["symbol"],
                knowledge_cutoff_at="2026-08-30T00:00:00Z",
                capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
                model_version="2.0.0",
                used_rule_versions={},
                source_resource_versions=[],
                manual_approval_ids=[],
                output={"status": "available", "symbol": membership["symbol"]},
                created_at=f"2026-08-30T00:{offset:02d}:00Z",
            ),
            f"daily-query-budget-{offset}",
        )
    reader = TracingDailyResearchReadRepository(db_path)
    service = DailyResearchReviewContextService(
        db_path,
        read_repository=reader,
    )

    response = service.list(
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        request_received_at="2026-08-31T09:00:00Z",
        limit=25,
    )
    reader.statements.clear()
    repeat = service.list(
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        request_received_at="2026-08-31T09:00:00Z",
        limit=25,
    )

    assert response["contract_version"] == DAILY_RESEARCH_REVIEW_CONTEXT_VERSION
    assert len(response["items"]) == 25
    assert repeat == response
    statements = [statement for statement in reader.statements if statement.strip()]
    assert len(statements) <= 32, statements
