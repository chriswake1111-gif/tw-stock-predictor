"""Read-only Phase 11 orchestration for immutable snapshot comparison."""

from __future__ import annotations

import sqlite3
from typing import Any

from src.domain.snapshot_comparison import (
    COMPARISON_POLICY_VERSION,
    COMPARISON_SNAPSHOT_CONTRACT,
    ChangeCategory,
    CurrentContextChangeType,
    SnapshotDelta,
    SnapshotReference,
    canonical_timestamp,
    canonical_value,
    delta_sort_key,
)
from src.engine.snapshot_comparator import SnapshotComparator, compatibility_reason
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.services.data_freshness_service import DataFreshnessService


class SnapshotNotFoundError(LookupError):
    pass


class SnapshotComparisonService:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        self.snapshots = AnalysisSnapshotRepository(db_path)
        self.freshness = DataFreshnessService(db_path)
        self.comparator = SnapshotComparator()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
        return conn

    @staticmethod
    def _context_identity(item: dict[str, Any]) -> str:
        logical = item.get("logical_resource_id")
        stable = logical if logical not in {None, ""} else item.get("snapshot_resource_id")
        return "|".join(
            [str(item.get("section", "")), str(item.get("resource_type", "")), str(stable)]
        )

    @staticmethod
    def _context_delta(
        *, change_type: CurrentContextChangeType, identity: str,
        field_path: str, before: Any, after: Any,
        section: str = "dependencies", resource_type: str | None = None,
    ) -> SnapshotDelta | None:
        canonical_before = canonical_value(before)
        canonical_after = canonical_value(after)
        if canonical_before == canonical_after:
            return None
        return SnapshotDelta(
            category=ChangeCategory.CURRENT_CONTEXT,
            change_type=change_type.value,
            section=section,
            resource_type=resource_type,
            canonical_identity=identity,
            field_path=field_path,
            before=canonical_before,
            after=canonical_after,
        )

    def _compare_contexts(
        self, base: dict[str, Any], comparison: dict[str, Any]
    ) -> list[dict[str, Any]]:
        deltas: list[SnapshotDelta] = []
        overall = self._context_delta(
            change_type=CurrentContextChangeType.FRESHNESS_STATUS_CHANGED,
            identity="snapshot_dependency_freshness",
            field_path="freshness_status",
            before=base["freshness_status"],
            after=comparison["freshness_status"],
        )
        if overall:
            deltas.append(overall)
        left = {
            self._context_identity(item): item
            for item in base.get("checked_dependencies", [])
        }
        right = {
            self._context_identity(item): item
            for item in comparison.get("checked_dependencies", [])
        }
        for identity in sorted(left.keys() | right.keys()):
            before = left.get(identity, {})
            after = right.get(identity, {})
            exemplar = after or before
            section = str(exemplar.get("section", "dependencies"))
            resource_type = exemplar.get("resource_type")
            status_before = before.get("status", {"state": "missing"})
            status_after = after.get("status", {"state": "missing"})
            if status_before != status_after:
                if status_after == "blocked":
                    change_type = CurrentContextChangeType.DEPENDENCY_BLOCKED
                elif status_after == "unknown":
                    change_type = CurrentContextChangeType.DEPENDENCY_UNKNOWN
                else:
                    change_type = CurrentContextChangeType.FRESHNESS_STATUS_CHANGED
                delta = self._context_delta(
                    change_type=change_type, identity=identity,
                    field_path="checked_dependencies.status",
                    before=status_before, after=status_after,
                    section=section, resource_type=resource_type,
                )
                if delta:
                    deltas.append(delta)
            if before.get("effective_approval_status") != after.get("effective_approval_status"):
                change_type = (
                    CurrentContextChangeType.APPROVAL_REVOKED
                    if after.get("effective_approval_status") == "revoked"
                    else CurrentContextChangeType.APPROVAL_ELIGIBILITY_CHANGED
                )
                delta = self._context_delta(
                    change_type=change_type, identity=identity,
                    field_path="checked_dependencies.effective_approval_status",
                    before=before.get("effective_approval_status"),
                    after=after.get("effective_approval_status"),
                    section=section, resource_type=resource_type,
                )
                if delta:
                    deltas.append(delta)
            if before.get("candidate_awaiting_review") != after.get("candidate_awaiting_review"):
                delta = self._context_delta(
                    change_type=CurrentContextChangeType.APPROVAL_ELIGIBILITY_CHANGED,
                    identity=identity,
                    field_path="checked_dependencies.candidate_awaiting_review",
                    before=before.get("candidate_awaiting_review"),
                    after=after.get("candidate_awaiting_review"),
                    section=section, resource_type=resource_type,
                )
                if delta:
                    deltas.append(delta)
            publication_fields = (
                "bound_publication_evidence_id",
                "latest_visible_publication_evidence_id",
                "bound_evidence_status",
                "publication_binding_status",
            )
            publication_before = {field: before.get(field) for field in publication_fields}
            publication_after = {field: after.get(field) for field in publication_fields}
            if publication_before != publication_after and (
                publication_before["bound_publication_evidence_id"]
                or publication_after["bound_publication_evidence_id"]
                or publication_before["latest_visible_publication_evidence_id"]
                or publication_after["latest_visible_publication_evidence_id"]
            ):
                delta = self._context_delta(
                    change_type=CurrentContextChangeType.PUBLICATION_EVIDENCE_CHANGED,
                    identity=identity,
                    field_path="checked_dependencies.publication_evidence",
                    before=publication_before, after=publication_after,
                    section=section, resource_type=resource_type,
                )
                if delta:
                    deltas.append(delta)
        return [item.canonical_payload() for item in sorted(deltas, key=delta_sort_key)]

    def compare(
        self,
        *,
        base_snapshot_id: str,
        comparison_snapshot_id: str,
        comparison_cutoff: str,
    ) -> dict[str, Any]:
        if not base_snapshot_id.strip() or not comparison_snapshot_id.strip():
            raise ValueError("comparison_request_invalid")
        cutoff = canonical_timestamp(comparison_cutoff)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            base = self.snapshots.get_with_connection(conn, base_snapshot_id)
            comparison = self.snapshots.get_with_connection(conn, comparison_snapshot_id)
            missing = [
                snapshot_id for snapshot_id, value in (
                    (base_snapshot_id, base), (comparison_snapshot_id, comparison)
                ) if value is None
            ]
            if missing:
                raise SnapshotNotFoundError(",".join(missing))
            assert base is not None and comparison is not None
            reason = compatibility_reason(base, comparison, cutoff)
            response: dict[str, Any] = {
                "status": "incomparable_contract" if reason else "available",
                "comparison_policy_version": COMPARISON_POLICY_VERSION,
                "comparison_snapshot_contract": COMPARISON_SNAPSHOT_CONTRACT,
                "comparison_cutoff": cutoff,
                "direction": {
                    "base_snapshot_id": base_snapshot_id,
                    "comparison_snapshot_id": comparison_snapshot_id,
                    "absolute_delta_formula": "comparison_minus_base",
                },
                "base_snapshot": SnapshotReference.from_snapshot(base).canonical_payload(),
                "comparison_snapshot": SnapshotReference.from_snapshot(comparison).canonical_payload(),
                "compatibility": {"compatible": reason is None, "reasons": [reason] if reason else []},
                "stored_deltas": [],
                "base_current_context": None,
                "comparison_current_context": None,
                "current_context_deltas": [],
                "warnings": [],
                "reasons": [reason] if reason else [],
            }
            if reason is None:
                response["stored_deltas"] = self.comparator.compare(base, comparison)
                base_context = self.freshness.snapshot_dependency_freshness_with_connection(
                    conn, base, cutoff, checked_at=cutoff
                )
                comparison_context = self.freshness.snapshot_dependency_freshness_with_connection(
                    conn, comparison, cutoff, checked_at=cutoff
                )
                base_context.pop("snapshot_output_sha256", None)
                comparison_context.pop("snapshot_output_sha256", None)
                response["base_current_context"] = base_context
                response["comparison_current_context"] = comparison_context
                response["current_context_deltas"] = self._compare_contexts(
                    base_context, comparison_context
                )
                response["reasons"] = sorted(set(
                    base_context.get("reasons", []) + comparison_context.get("reasons", [])
                ))
                if base_context["freshness_status"] in {"unknown", "blocked"} or comparison_context["freshness_status"] in {"unknown", "blocked"}:
                    response["warnings"].append("current_dependency_context_requires_review")
            conn.execute("COMMIT")
            return response
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
