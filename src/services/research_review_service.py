"""Phase 12 queue orchestration over one deterministic SQLite read snapshot."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.domain.research_workflow import (
    ReviewAcknowledgment,
    ReviewState,
    ResearchComparisonStatus,
    ResearchQueueOrder,
    WORKFLOW_CONTRACT_VERSION,
    comparison_has_deltas,
    public_review_event,
)
from src.domain.snapshot_comparison import canonical_timestamp
from src.repositories.analysis_snapshot_repository import (
    AnalysisSnapshotRepository,
    SnapshotIntegrityError,
)
from src.repositories.research_workflow_repository import (
    ResearchWorkflowNotFoundError,
    ResearchWorkflowRepository,
)
from src.services.snapshot_comparison_service import (
    SnapshotComparisonService,
    SnapshotNotFoundError,
)


def _received_timestamp(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("request_received_at_invalid")
        return canonical_timestamp(value.astimezone(timezone.utc).isoformat())
    return canonical_timestamp(value)


class ResearchReviewService:
    def __init__(self, db_path: str = "data/cache.db", *, auto_migrate: bool = True):
        self.db_path = db_path
        self.workflow = ResearchWorkflowRepository(db_path, auto_migrate=auto_migrate)
        self.snapshots = AnalysisSnapshotRepository(db_path, auto_migrate=auto_migrate)
        self.comparison = SnapshotComparisonService(db_path, auto_migrate=auto_migrate)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
        return conn

    @staticmethod
    def validate_query_cutoff(cutoff: str, request_received_at: datetime | str) -> str:
        normalized = canonical_timestamp(cutoff)
        if normalized > _received_timestamp(request_received_at):
            raise ValueError("comparison_cutoff_in_future")
        return normalized

    @staticmethod
    def _freshness(comparison: dict[str, Any]) -> str:
        statuses = [
            context.get("freshness_status")
            for context in (
                comparison.get("base_current_context"),
                comparison.get("comparison_current_context"),
            ) if context
        ]
        if "blocked" in statuses:
            return "blocked"
        if "unknown" in statuses:
            return "unknown"
        if "stale" in statuses:
            return "stale"
        return "current" if statuses else "unknown"

    def _derive_item(
        self,
        conn: sqlite3.Connection,
        membership: dict[str, Any],
        latest_event: dict[str, Any] | None,
        latest_result: dict[str, Any] | None,
        cutoff: str,
    ) -> dict[str, Any]:
        common = {
            "watchlist_item": membership,
            "analysis_status": "insufficient_data",
            "freshness_status": "unknown",
            "comparison_status": ResearchComparisonStatus.NOT_RUN.value,
            "review_state": ReviewState.NO_SNAPSHOT.value,
            "comparison_has_deltas": None,
            "stored_delta_count": 0,
            "current_context_delta_count": 0,
            "latest_snapshot_reference": None,
            "latest_review_event_reference": public_review_event(latest_event),
            "reason_codes": [],
            "_comparison": None,
        }
        if latest_result is None:
            common["reason_codes"] = ["research_snapshot_not_found"]
            return common
        common["latest_snapshot_reference"] = {
            "snapshot_id": latest_result["snapshot_id"],
            "symbol": membership["symbol"],
        }
        if latest_event is None:
            common["review_state"] = ReviewState.BASELINE_NOT_SET.value
            common["reason_codes"] = ["review_baseline_not_set"]
            if latest_result["snapshot"]:
                common["analysis_status"] = latest_result["snapshot"]["output"].get("status")
            return common
        if cutoff < latest_event["comparison_cutoff_at"]:
            raise ValueError("comparison_cutoff_before_review_baseline")
        if latest_result["integrity_error"]:
            common["review_state"] = ReviewState.SNAPSHOT_INTEGRITY_ERROR.value
            common["reason_codes"] = ["latest_snapshot_integrity_error"]
            return common
        latest = latest_result["snapshot"]
        assert latest is not None
        common["analysis_status"] = latest["output"].get("status")
        try:
            baseline = self.snapshots.get_with_connection(
                conn, latest_event["acknowledged_snapshot_id"]
            )
        except SnapshotIntegrityError:
            common["review_state"] = ReviewState.SNAPSHOT_INTEGRITY_ERROR.value
            common["reason_codes"] = ["acknowledged_snapshot_integrity_error"]
            return common
        if baseline is None:
            common["review_state"] = ReviewState.BLOCKED.value
            common["freshness_status"] = "blocked"
            common["comparison_status"] = ResearchComparisonStatus.UNAVAILABLE.value
            common["reason_codes"] = ["acknowledged_snapshot_missing"]
            return common
        try:
            comparison = self.comparison.compare_with_connection(
                conn,
                base_snapshot_id=baseline["snapshot_id"],
                comparison_snapshot_id=latest["snapshot_id"],
                comparison_cutoff=cutoff,
            )
        except SnapshotNotFoundError:
            common["review_state"] = ReviewState.BLOCKED.value
            common["freshness_status"] = "blocked"
            common["comparison_status"] = ResearchComparisonStatus.UNAVAILABLE.value
            common["reason_codes"] = ["snapshot_comparison_unavailable"]
            return common
        common["_comparison"] = comparison
        common["reason_codes"] = comparison.get("reasons", [])
        if comparison["status"] == "incomparable_contract":
            common["review_state"] = ReviewState.INCOMPARABLE_CONTRACT.value
            common["comparison_status"] = ResearchComparisonStatus.INCOMPARABLE_CONTRACT.value
            return common
        freshness = self._freshness(comparison)
        common["freshness_status"] = freshness
        if freshness == "blocked":
            common["review_state"] = ReviewState.BLOCKED.value
            common["comparison_status"] = ResearchComparisonStatus.UNAVAILABLE.value
            return common
        if freshness == "unknown":
            common["review_state"] = ReviewState.UNKNOWN.value
            common["comparison_status"] = ResearchComparisonStatus.UNAVAILABLE.value
            return common
        stored_count = len(comparison["stored_deltas"])
        current_count = len(comparison["current_context_deltas"])
        common["stored_delta_count"] = stored_count
        common["current_context_delta_count"] = current_count
        common["comparison_status"] = ResearchComparisonStatus.COMPARABLE.value
        common["comparison_has_deltas"] = comparison_has_deltas(
            comparison_status=ResearchComparisonStatus.COMPARABLE, stored_delta_count=stored_count,
            current_context_delta_count=current_count,
        )
        common["review_state"] = (
            ReviewState.COMPARABLE_WITH_DELTAS.value
            if common["comparison_has_deltas"]
            else ReviewState.COMPARABLE_WITHOUT_DELTAS.value
        )
        return common

    def queue(
        self, *, comparison_cutoff: str, request_received_at: datetime | str,
        include_archived: bool = False, limit: int = 25,
        order: ResearchQueueOrder | str = ResearchQueueOrder.SYMBOL,
    ) -> dict[str, Any]:
        cutoff = self.validate_query_cutoff(comparison_cutoff, request_received_at)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            memberships = self.workflow.list_memberships_with_connection(
                conn, include_archived=include_archived, limit=limit, order=order
            )
            events = self.workflow.latest_review_events_with_connection(
                conn, [item["watchlist_item_id"] for item in memberships]
            )
            latest = self.snapshots.latest_for_symbols_as_of_with_connection(
                conn, [item["symbol"] for item in memberships], cutoff
            )
            items = [self._derive_item(
                conn, item, events.get(item["watchlist_item_id"]),
                latest.get(item["symbol"]), cutoff,
            ) for item in memberships]
            conn.execute("COMMIT")
            for item in items:
                item.pop("_comparison", None)
            return {
                "status": "available",
                "workflow_contract_version": WORKFLOW_CONTRACT_VERSION,
                "comparison_cutoff": cutoff,
                "items": items,
            }
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def detail(
        self, item_id: str, *, comparison_cutoff: str,
        request_received_at: datetime | str,
    ) -> dict[str, Any]:
        cutoff = self.validate_query_cutoff(comparison_cutoff, request_received_at)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            item = self.workflow.membership_with_connection(conn, item_id)
            if item is None:
                raise ResearchWorkflowNotFoundError(item_id)
            events = self.workflow.latest_review_events_with_connection(conn, [item_id])
            latest = self.snapshots.latest_for_symbols_as_of_with_connection(
                conn, [item["symbol"]], cutoff
            )
            result = self._derive_item(
                conn, item, events.get(item_id), latest.get(item["symbol"]), cutoff
            )
            comparison = result.pop("_comparison", None)
            result["comparison"] = comparison
            conn.execute("COMMIT")
            return result
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def acknowledge(
        self, item_id: str, *, snapshot_id: str, comparison_cutoff: str,
        idempotency_key: str, request_received_at: datetime | str,
    ) -> dict[str, Any]:
        received = _received_timestamp(request_received_at)
        cutoff = canonical_timestamp(comparison_cutoff)
        if cutoff > received:
            raise ValueError("comparison_cutoff_in_future")
        snapshot = self.snapshots.get(snapshot_id)
        if snapshot is None:
            raise ValueError("acknowledged_snapshot_not_found")
        if cutoff < snapshot["knowledge_cutoff_at"]:
            raise ValueError("comparison_cutoff_before_snapshot")
        event = self.workflow.append_review_event(
            ReviewAcknowledgment(item_id, snapshot_id, cutoff, idempotency_key),
            reviewed_at=received,
        )
        assert event is not None
        return public_review_event(event)
