"""Phase 17 Daily Research Review Context orchestration.

The Daily read is deliberately a projection over existing Phase 11-16
evidence.  It owns one query-only SQLite transaction and never runs a
collector, migration, public HTTP request, acknowledgment or refresh as a
side effect of GET.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from src.domain.analysis_snapshot import CaptureMode
from src.domain.daily_research_context_provenance import (
    build_daily_context_reference,
    context_change_reasons,
    verify_daily_context_reference,
)
from src.domain.daily_research_refresh_policy import (
    evaluate_required_analysis_sections,
)
from src.domain.daily_research_review_context import (
    DAILY_BASELINE_SELECTION_POLICY_VERSION,
    DAILY_BASELINE_SELECTION_REASON_REGISTRY_VERSION,
    DAILY_CONTRACT_VERSIONS,
    DAILY_REASON_CODES,
    DAILY_RESEARCH_CURSOR_VERSION,
    DAILY_RESEARCH_D_K_VERSION,
    DAILY_RESEARCH_ORDER_VERSION,
    DAILY_RESEARCH_PREFLIGHT_VERSION,
    DAILY_RESEARCH_REASON_REGISTRY_VERSION,
    DAILY_RESEARCH_REVIEW_CONTEXT_POLICY_VERSION,
    DAILY_RESEARCH_REVIEW_CONTEXT_VERSION,
    DAILY_RESEARCH_REVIEW_STATUS_VERSION,
    DAILY_RESEARCH_SNAPSHOT_INTEGRATION_VERSION,
    DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
    DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
    DailyResearchCursor,
    DailyResearchCursorError,
    active_population_checksum,
    baseline_selection_eligibility,
    canonical_json,
    canonical_utc_timestamp,
    decode_daily_cursor,
    derive_review_flags,
    normalize_daily_reasons,
    reduce_page_status,
    reduce_preflight_status,
    validate_daily_d_k,
)
from src.domain.neutral_batch_market_context import NeutralBatchMarketContextRequest
from src.domain.research_workflow import (
    MembershipState,
    ReviewAcknowledgment,
    ReviewState,
    ResearchComparisonStatus,
    WORKFLOW_CONTRACT_VERSION,
    comparison_has_deltas,
    public_review_event,
)
from src.domain.snapshot_comparison import canonical_timestamp
from src.engine.snapshot_comparator import supports_snapshot_contract
from src.repositories.analysis_snapshot_repository import (
    AnalysisSnapshotRepository,
    SnapshotIntegrityError,
)
from src.repositories.daily_research_read_repository import (
    DailyResearchReadRepository,
)
from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.neutral_batch_market_context_repository import (
    NeutralBatchMarketContextRepository,
)
from src.repositories.research_workflow_repository import (
    ResearchWorkflowRepository,
)
from src.repositories.universe_repository import UniverseRepository
from src.services.data_freshness_service import DataFreshnessService
from src.services.snapshot_comparison_service import SnapshotComparisonService
from src.services.neutral_batch_market_context_service import (
    NeutralBatchMarketContextService,
)


class DailyResearchItemNotFound(LookupError):
    code = "research_watchlist_item_not_found"


class DailyResearchItemInactive(ValueError):
    code = "baseline_selection_item_not_active"


class DailyResearchCursorPopulationChanged(ValueError):
    code = "cursor_population_changed"


class DailyResearchBaselineNotEligible(ValueError):
    code = "baseline_selection_not_eligible"

    def __init__(self, eligibility: dict[str, Any]):
        self.eligibility = eligibility
        super().__init__(self.code)


class DailyResearchBaselineIdempotencyConflict(ValueError):
    code = "baseline_selection_idempotency_conflict"


class DailyResearchRefreshNotEligible(ValueError):
    code = "snapshot_refresh_not_eligible"

    def __init__(self, gate: dict[str, Any]):
        self.gate = gate
        super().__init__(self.code)


class DailyResearchRefreshRace(ValueError):
    code = "snapshot_refresh_expected_snapshot_race"

    def __init__(self, gate: dict[str, Any]):
        self.gate = gate
        super().__init__(self.code)


def _received_timestamp(value: datetime | str) -> str:
    return canonical_utc_timestamp(value, "request_received_at")


def _safe_bool(value: Any) -> bool:
    return bool(value)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class DailyResearchReviewContextService:
    """Single-read-boundary Daily projection and explicit write commands."""

    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        read_repository: DailyResearchReadRepository | None = None,
        workflow: ResearchWorkflowRepository | None = None,
        snapshots: AnalysisSnapshotRepository | None = None,
        freshness: DataFreshnessService | None = None,
        comparison: SnapshotComparisonService | None = None,
        universe: UniverseRepository | None = None,
        phase16_repository: NeutralBatchMarketContextRepository | None = None,
        actor_source: str | None = None,
        analysis_builder: Callable[
            [str, str, dict[str, Any], sqlite3.Connection], dict[str, Any]
        ] | None = None,
    ) -> None:
        self.db_path = db_path
        self.read_repository = read_repository or DailyResearchReadRepository(db_path)
        self.workflow = workflow or ResearchWorkflowRepository(db_path, auto_migrate=False)
        self.snapshots = snapshots or AnalysisSnapshotRepository(db_path, auto_migrate=False)
        self.freshness = freshness or DataFreshnessService(
            db_path, auto_migrate=False, snapshots=self.snapshots
        )
        self.comparison = comparison or SnapshotComparisonService(
            db_path,
            auto_migrate=False,
            snapshots=self.snapshots,
            freshness=self.freshness,
        )
        self.universe = universe or UniverseRepository(db_path, auto_migrate=False)
        self.phase16_repository = phase16_repository or NeutralBatchMarketContextRepository(
            db_path, storage=EodCloseRepository(db_path)
        )
        self.phase16_service = NeutralBatchMarketContextService(
            db_path, repository=self.phase16_repository
        )
        self.actor_source = actor_source or "server_research_boundary"
        self.analysis_builder = analysis_builder

    @staticmethod
    def _status_from_context(
        *,
        identity: Mapping[str, Any],
        phase16_item: Mapping[str, Any] | None,
        eod: Mapping[str, Any] | None,
        latest_result: Mapping[str, Any] | None,
        integrity_error: bool,
        comparison_result: Mapping[str, Any] | None,
        freshness_status: str,
    ) -> str:
        identity_status = str(identity.get("identity_status") or "unresolved")
        if identity_status in {"unresolved", "conflict"}:
            return "blocked"
        if integrity_error:
            return "blocked"
        if comparison_result and comparison_result.get("status") == "incomparable_contract":
            return "blocked"
        if phase16_item is None:
            return "unknown"
        phase16_status = str(phase16_item.get("item_state") or "unknown")
        if phase16_status == "blocked":
            return "blocked"
        if phase16_status in {"unknown", "partial"}:
            return phase16_status
        if eod:
            eod_status = str(eod.get("status") or "unknown")
            if eod_status == "blocked":
                return "blocked"
            if eod_status in {"unknown", "partial"}:
                return eod_status
            if eod_status in {"insufficient_data", "needs_human_input"}:
                return "insufficient_data" if eod_status == "insufficient_data" else "partial"
        if latest_result is None:
            return "insufficient_data"
        if freshness_status == "blocked":
            return "blocked"
        if freshness_status == "unknown":
            return "unknown"
        if freshness_status == "stale":
            return "partial"
        return "available"

    @staticmethod
    def _phase13_identity(context: Mapping[str, Any] | None, symbol: str) -> dict[str, Any]:
        source = context or {}
        reference = source.get("identity_reference") or source.get("identity")
        reference = reference if isinstance(reference, Mapping) else {}
        provenance = source.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        resolved = bool(
            reference
            and reference.get("canonical_symbol") == symbol
            and reference.get("venue") in {"TWSE", "TPEX"}
        )
        venue = "TWSE" if symbol.endswith(".TW") else "TPEX"
        return {
            "canonical_symbol": symbol,
            "venue": reference.get("venue") or venue,
            "identity_status": "resolved" if resolved else "unresolved",
            "identity_ref": reference.get("instrument_revision_id")
            or provenance.get("instrument_revision_id")
            or provenance.get("universe_revision_id"),
            "identity_epoch": reference.get("identity_epoch"),
            "instrument_id": reference.get("instrument_id"),
            "official_code": reference.get("official_code") or symbol.split(".", 1)[0],
            "display_name": reference.get("display_name"),
            "security_type": reference.get("security_type"),
            "listing_status": reference.get("listing_status") or "unknown",
            "trading_state": reference.get("trading_state") or "unknown",
            "reasons": list(source.get("reasons") or []),
        }

    @staticmethod
    def _phase14_from_phase16(item: Mapping[str, Any] | None) -> dict[str, Any]:
        if not item:
            return {
                "status": "unknown",
                "reason_codes": ["no_same_day_snapshot"],
                "canonical_symbol": None,
            }
        value = item.get("eod_close")
        return dict(value) if isinstance(value, Mapping) else {
            "status": "unknown",
            "reason_codes": ["no_same_day_snapshot"],
        }

    @staticmethod
    def _phase15_from_phase16(
        item: Mapping[str, Any] | None,
        aggregate: Mapping[str, Any],
        market_date: str,
        symbol: str,
    ) -> dict[str, Any]:
        value = item or {}
        status = str(value.get("coverage_status") or "unknown")
        reasons = list(value.get("reason_codes") or [])
        return {
            "contract_version": "eod_coverage_visibility_v1",
            "provider": (value.get("provenance") or {}).get("provider") if isinstance(value.get("provenance"), Mapping) else None,
            "dataset": (value.get("provenance") or {}).get("resource_id") if isinstance(value.get("provenance"), Mapping) else None,
            "resource": (value.get("provenance") or {}).get("resource_id") if isinstance(value.get("provenance"), Mapping) else None,
            "record_id": value.get("source_record_reference"),
            "market_date": market_date,
            "venue": value.get("venue"),
            "canonical_symbol": symbol,
            "status": status,
            "partial": status == "partial",
            "unknown": status == "unknown",
            "blocked": status == "blocked",
            "denominator_candidate_count": _safe_int(aggregate.get("denominator_candidate_count")),
            "denominator_expected_count": _safe_int(aggregate.get("denominator_expected_count")),
            "denominator_excluded_count": _safe_int(aggregate.get("denominator_excluded_count")),
            "denominator_unresolved_count": _safe_int(aggregate.get("denominator_unresolved_count")),
            "source_observation_orphan_count": _safe_int(aggregate.get("source_observation_orphan_count")),
            "aggregate_completeness_proven": False,
            "digest": None,
            "reason_codes": reasons,
        }

    @staticmethod
    def _phase16_aggregate_for_item(
        phase16_response: Mapping[str, Any],
        phase16_item: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        venue = str((phase16_item or {}).get("venue") or "")
        per_venue = phase16_response.get("per_venue") or {}
        value = per_venue.get(venue) if isinstance(per_venue, Mapping) else None
        if isinstance(value, Mapping) and isinstance(value.get("aggregate"), Mapping):
            return dict(value["aggregate"])
        return dict(phase16_response.get("aggregate") or {})

    @staticmethod
    def _phase16_reference_item(
        item: Mapping[str, Any] | None,
        market_date: str,
        knowledge_cutoff_at: str,
        symbol: str,
    ) -> dict[str, Any]:
        value = item or {}
        provenance = value.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        return {
            "contract_version": "neutral_batch_market_context_v1",
            "d_k_policy_version": "neutral_batch_market_context_d_k_v1",
            "market_date": market_date,
            "knowledge_cutoff_at": knowledge_cutoff_at,
            "internal_venue_scope": "TWSE_TPEX",
            "canonical_symbol": value.get("canonical_symbol") or symbol,
            "venue": value.get("venue") or ("TWSE" if symbol.endswith(".TW") else "TPEX"),
            "status": value.get("item_state") or "unknown",
            "source_state": value.get("coverage_status") or "unknown",
            "quality_status": value.get("observed_status") or "unknown",
            "quality_reasons": list(value.get("reason_codes") or []),
            "provenance_ref": value.get("source_record_reference") or provenance.get("source_record_reference"),
        }

    @staticmethod
    def _phase16_aggregate_reference(
        phase16_response: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "contract_version": phase16_response.get("contract_version"),
            "scope": "phase16_full_twse_tpex_context",
            "status": phase16_response.get("status") or "unknown",
            "aggregate_completeness_proven": False,
            "per_venue_status": {
                venue: data.get("assembly_status")
                for venue, data in (phase16_response.get("per_venue") or {}).items()
            },
        }

    @staticmethod
    def _phase14_reference(eod: Mapping[str, Any], market_date: str) -> dict[str, Any]:
        return {
            "provider": eod.get("provider"),
            "dataset": eod.get("resource_key"),
            "resource": eod.get("resource_key"),
            "record_id": eod.get("source_record_reference"),
            "venue": eod.get("venue"),
            "market_date": market_date,
            "observation_date": eod.get("selected_trade_date") or eod.get("source_trade_date"),
            "available_at": eod.get("observed_at"),
            "ingested_at": eod.get("observed_at"),
            "classification": eod.get("product_scope"),
            "unit": eod.get("unit"),
            "freshness_status": eod.get("freshness_state"),
            "status": eod.get("status"),
            "eligible": eod.get("status") == "available",
            "correction_id": None,
            "revision": None,
            "revoked": any("revoke" in str(reason) for reason in eod.get("reason_codes", [])),
            "digest": None,
        }

    @staticmethod
    def _phase16_safe_aggregate(phase16_response: Mapping[str, Any]) -> dict[str, Any]:
        aggregate = dict(phase16_response.get("aggregate") or {})
        return {
            "scope": "phase16_full_twse_tpex_context",
            "status": phase16_response.get("status") or "unknown",
            "aggregate_completeness_proven": False,
            "denominator_candidate_count": _safe_int(aggregate.get("denominator_candidate_count")),
            "denominator_expected_count": _safe_int(aggregate.get("denominator_expected_count")),
            "denominator_excluded_count": _safe_int(aggregate.get("denominator_excluded_count")),
            "denominator_unresolved_count": _safe_int(aggregate.get("denominator_unresolved_count")),
            "source_observation_orphan_count": _safe_int(aggregate.get("source_observation_orphan_count")),
            "item_status_counts": dict(aggregate.get("item_status_counts") or {}),
            "per_venue": dict(phase16_response.get("per_venue") or {}),
            "contract_version": phase16_response.get("contract_version"),
            "status_policy_version": "neutral_batch_market_context_status_v1",
            "assembly_version": "neutral_batch_market_context_assembly_v1",
        }

    @staticmethod
    def _snapshot_reference(
        result: Mapping[str, Any] | None,
        *,
        symbol: str,
        requested_cutoff: str,
        eligible_for_requested_d_k: bool,
    ) -> dict[str, Any] | None:
        if not result:
            return None
        snapshot = result.get("snapshot")
        snapshot_id = result.get("snapshot_id") or (snapshot or {}).get("snapshot_id")
        if not snapshot_id:
            return None
        snapshot = snapshot if isinstance(snapshot, Mapping) else {}
        return {
            "snapshot_id": snapshot_id,
            "symbol": snapshot.get("symbol") or symbol,
            "venue": "TWSE" if (snapshot.get("symbol") or symbol).endswith(".TW") else "TPEX",
            "created_at": snapshot.get("created_at"),
            "knowledge_cutoff_at": snapshot.get("knowledge_cutoff_at"),
            "capture_mode": snapshot.get("capture_mode"),
            "model_version": snapshot.get("model_version"),
            "integrity_status": "valid" if not result.get("integrity_error") else "invalid",
            "provenance_status": "available" if snapshot else "unavailable",
            "eligible_for_requested_d_k": bool(eligible_for_requested_d_k),
        }

    @staticmethod
    def _workflow_snapshot_reference(
        result: Mapping[str, Any] | None,
        *,
        symbol: str,
        requested_cutoff: str,
    ) -> dict[str, Any] | None:
        reference = DailyResearchReviewContextService._snapshot_reference(
            result,
            symbol=symbol,
            requested_cutoff=requested_cutoff,
            eligible_for_requested_d_k=False,
        )
        if reference is None:
            return None
        snapshot = result.get("snapshot") if isinstance(result, Mapping) else None
        if isinstance(snapshot, Mapping):
            created_at = snapshot.get("created_at")
            knowledge_cutoff = snapshot.get("knowledge_cutoff_at")
            reference["eligible_for_requested_d_k"] = bool(
                created_at is not None
                and knowledge_cutoff is not None
                and str(created_at) <= requested_cutoff
                and str(knowledge_cutoff) <= requested_cutoff
                and not result.get("integrity_error")
            )
        return reference

    @staticmethod
    def _review_event_reference(
        event: Mapping[str, Any] | None,
        *,
        knowledge_cutoff_at: str,
    ) -> dict[str, Any] | None:
        if event is None:
            return None
        value = public_review_event(dict(event)) or {}
        event_time = max(
            str(event.get("reviewed_at") or ""),
            str(event.get("created_at") or ""),
        )
        value["recorded_after_evidence_cutoff"] = event_time > knowledge_cutoff_at
        return value

    @staticmethod
    def _delta_summary(deltas: list[dict[str, Any]] | None) -> dict[str, Any]:
        values = deltas or []
        return {
            "count": len(values),
            "change_types": dict(sorted(Counter(
                str(item.get("change_type") or "unknown") for item in values
            ).items())),
            "sections": dict(sorted(Counter(
                str(item.get("section") or "unknown") for item in values
            ).items())),
        }

    @staticmethod
    def _review_reason_codes(
        *,
        latest_result: Mapping[str, Any] | None,
        latest_integrity_error: bool,
        baseline_result: Mapping[str, Any] | None,
        baseline_integrity_error: bool,
        baseline_visible: bool,
        comparison_result: Mapping[str, Any] | None,
        phase13: Mapping[str, Any],
        phase16_item: Mapping[str, Any] | None,
        eod: Mapping[str, Any],
        freshness_status: str,
        context_reasons: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        if latest_result is None:
            reasons.append("no_snapshot")
        elif latest_integrity_error:
            reasons.append("snapshot_integrity_error")
        if baseline_result is None:
            if latest_result is not None:
                reasons.append("baseline_not_set")
        elif not baseline_visible:
            reasons.append("baseline_not_visible_at_cutoff")
        elif baseline_integrity_error:
            reasons.append("snapshot_integrity_error")
        if comparison_result:
            status = comparison_result.get("status")
            if status == "incomparable_contract":
                reasons.append("incomparable_contract")
            for reason in comparison_result.get("reasons", []):
                mapping = {
                    "dependency_added": "snapshot_dependency_changed",
                    "dependency_removed": "snapshot_dependency_changed",
                    "resource_revision_changed": "snapshot_dependency_changed",
                    "approval_reference_changed": "profile_revision_changed",
                    "technical_anchor_changed": "anchor_revision_changed",
                    "valuation_range_changed": "valuation_input_changed",
                    "valuation_cell_changed": "valuation_input_changed",
                    "newer_eligible_forward_eps_revision": "forward_eps_revision_changed",
                    "dependency_blocked": "data_blocked",
                    "dependency_unknown": "data_unknown",
                    "dependency_freshness_unknown": "current_context_unavailable",
                }.get(str(reason), str(reason))
                if mapping in DAILY_REASON_CODES:
                    reasons.append(mapping)
        if phase13.get("identity_status") in {"unresolved", "conflict"}:
            reasons.append("identity_unresolved")
        if phase16_item:
            status = str(phase16_item.get("item_state") or "unknown")
            reasons.extend({
                "partial": ["data_partial"],
                "unknown": ["data_unknown"],
                "blocked": ["data_blocked"],
            }.get(status, []))
        if eod.get("status") in {"blocked"}:
            reasons.append("data_blocked")
        elif eod.get("status") in {"unknown"}:
            reasons.append("data_unknown")
        elif eod.get("status") in {"partial", "insufficient_data", "needs_human_input"}:
            reasons.append("data_partial")
        if freshness_status == "stale":
            reasons.append("snapshot_stale")
        reasons.extend(context_reasons)
        return normalize_daily_reasons(reasons)

    @staticmethod
    def _build_context_reference(
        *,
        market_date: str,
        knowledge_cutoff_at: str,
        phase13: Mapping[str, Any],
        eod: Mapping[str, Any],
        phase15: Mapping[str, Any],
        phase16_item: Mapping[str, Any] | None,
        phase16_response: Mapping[str, Any],
        context_reasons: list[str],
    ) -> dict[str, Any]:
        return build_daily_context_reference(
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            phase13={
                "identity": phase13,
                "lifecycle": {"listing_status": phase13.get("listing_status")},
                "trading": {"trading_state": phase13.get("trading_state")},
                "source_state": {
                    "provider": phase13.get("provider"),
                    "dataset": phase13.get("dataset"),
                    "resource": phase13.get("resource"),
                    "record_id": phase13.get("identity_ref"),
                    "source_revision": phase13.get("identity_epoch"),
                    "availability_status": phase13.get("status"),
                    "ingestion_status": phase13.get("status"),
                    "status": phase13.get("status"),
                    "available_at": phase13.get("available_at"),
                    "ingested_at": phase13.get("ingested_at"),
                },
            },
            phase14={"eod": eod},
            phase15={"coverage": phase15},
            phase16_item=DailyResearchReviewContextService._phase16_reference_item(
                phase16_item, market_date, knowledge_cutoff_at,
                str(phase13.get("canonical_symbol") or ""),
            ),
            phase16_aggregate=DailyResearchReviewContextService._phase16_aggregate_reference(
                phase16_response
            ),
            context_reasons=context_reasons,
        )

    @staticmethod
    def _snapshot_contract_supported(
        snapshot: Mapping[str, Any] | None, *, item_symbol: str
    ) -> bool:
        return bool(
            isinstance(snapshot, Mapping)
            and str(snapshot.get("symbol") or "") == item_symbol
            and supports_snapshot_contract(dict(snapshot))
        )

    @staticmethod
    def _refresh_context_eligible(item: Mapping[str, Any]) -> bool:
        """Evaluate the pre-analysis refresh gate shared by GET and POST.

        Snapshot availability is a workflow state, not a refresh-quality
        prerequisite.  In particular, an active item with no snapshot remains
        ``insufficient_data``/``review_limited`` in the read DTO but may still
        attempt an explicit refresh when every evidence prerequisite is
        eligible.  The five required analysis sections are evaluated only
        after this context gate by ``refresh_snapshot``.
        """
        status = str(item.get("status") or "")
        review_state = str(item.get("review_state") or "")
        reasons = {
            str(reason)
            for reason in (item.get("reason_codes") or [])
            if str(reason)
        }
        snapshot_missing = (
            review_state == ReviewState.NO_SNAPSHOT.value
            or "no_snapshot" in reasons
        )
        if snapshot_missing:
            if status != "insufficient_data":
                return False
        elif status != "available":
            return False

        # These are workflow-state overlays that do not, by themselves, make
        # a new explicitly advanced snapshot unsafe.  All evidence/quality
        # reasons remain fail-closed below.
        if reasons.difference({
            "no_snapshot",
            "baseline_not_set",
            "baseline_not_visible_at_cutoff",
        }):
            return False
        if item.get("review_blocked") is not False:
            return False

        identity = item.get("identity")
        if not isinstance(identity, Mapping):
            return False
        if identity.get("identity_status") != "resolved":
            return False
        if identity.get("canonical_symbol") != item.get("canonical_symbol"):
            return False
        if identity.get("venue") not in {"TWSE", "TPEX"}:
            return False

        quality = item.get("quality")
        if not isinstance(quality, Mapping):
            return False
        if quality.get("integrity_status") != "valid":
            return False
        if quality.get("phase14_status") != "available":
            return False
        if quality.get("phase15_status") not in {"available", "observed_eligible"}:
            return False
        if quality.get("phase16_status") != "available":
            return False

        phase16_context = item.get("phase16_context")
        phase16_item = (
            phase16_context.get("item")
            if isinstance(phase16_context, Mapping)
            else None
        )
        if not isinstance(phase16_item, Mapping):
            return False
        if (
            phase16_item.get("item_state") or phase16_item.get("status")
        ) != "available":
            return False

        provenance = item.get("provenance")
        if not isinstance(provenance, Mapping):
            return False
        return bool(
            provenance.get("status") == "available"
            and provenance.get("context_digest_valid") is True
            and isinstance(provenance.get("current_reference"), Mapping)
        )

    def _phase16_projection(
        self,
        conn: sqlite3.Connection,
        *,
        symbols: list[str],
        market_date: str,
        knowledge_cutoff_at: str,
    ) -> dict[str, Any]:
        request = NeutralBatchMarketContextRequest(
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            venue_scope="TWSE_TPEX",
            limit=50,
        )
        projection = self.phase16_repository.read_for_symbols_with_connection(
            conn, request, canonical_symbols=symbols
        )
        return self.phase16_service._compose(request, projection)

    @staticmethod
    def _choose_phase16_item(
        values: list[dict[str, Any]], symbol: str
    ) -> dict[str, Any] | None:
        candidates = [
            value for value in values
            if value.get("canonical_symbol") == symbol
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda value: (
            0 if value.get("item_kind") == "denominator_candidate" else 1,
            str(value.get("item_kind") or ""),
            str(value.get("source_record_reference") or ""),
        ))
        return candidates[0]

    def _project_items(
        self,
        conn: sqlite3.Connection,
        *,
        memberships: list[dict[str, Any]],
        market_date: str,
        knowledge_cutoff_at: str,
        workflow_evaluated_at: str,
        k_events: dict[str, dict[str, Any]],
        workflow_events: dict[str, dict[str, Any]],
        k_snapshots: dict[str, dict[str, Any]],
        workflow_snapshots: dict[str, dict[str, Any]],
        baselines: dict[str, dict[str, Any]],
        phase13_contexts: dict[str, dict[str, Any]],
        phase16_response: dict[str, Any],
    ) -> list[dict[str, Any]]:
        symbols = [str(item["symbol"]) for item in memberships]
        phase16_values = list(phase16_response.get("items") or [])
        snapshot_values: list[dict[str, Any]] = []
        for result in (*k_snapshots.values(), *baselines.values()):
            snapshot = result.get("snapshot") if isinstance(result, Mapping) else None
            if isinstance(snapshot, Mapping):
                snapshot_values.append(dict(snapshot))
        freshness_by_id = self.freshness.snapshot_dependency_freshness_batch_with_connection(
            conn, snapshot_values, knowledge_cutoff_at,
            checked_at=knowledge_cutoff_at,
        )
        results: list[dict[str, Any]] = []
        for membership in memberships:
            item_id = str(membership["watchlist_item_id"])
            symbol = str(membership["symbol"])
            phase13_source = phase13_contexts.get(symbol)
            phase13 = self._phase13_identity(phase13_source, symbol)
            phase16_item = self._choose_phase16_item(phase16_values, symbol)
            eod = self._phase14_from_phase16(phase16_item)
            phase15 = self._phase15_from_phase16(
                phase16_item,
                self._phase16_aggregate_for_item(phase16_response, phase16_item),
                market_date,
                symbol,
            )
            latest_result = k_snapshots.get(symbol)
            latest_integrity_error = bool(latest_result and latest_result.get("integrity_error"))
            latest_snapshot = latest_result.get("snapshot") if latest_result else None
            event = k_events.get(item_id)
            baseline_id = str(event.get("acknowledged_snapshot_id")) if event else ""
            baseline_result = baselines.get(baseline_id) if baseline_id else None
            baseline_integrity_error = bool(
                baseline_result and baseline_result.get("integrity_error")
            )
            baseline_snapshot = baseline_result.get("snapshot") if baseline_result else None
            baseline_visible = bool(
                isinstance(baseline_snapshot, Mapping)
                and str(baseline_snapshot.get("symbol")) == symbol
                and str(baseline_snapshot.get("created_at") or "") <= knowledge_cutoff_at
                and str(baseline_snapshot.get("knowledge_cutoff_at") or "") <= knowledge_cutoff_at
                and not baseline_integrity_error
            )
            comparison_result: dict[str, Any] | None = None
            freshness_status = "unknown"
            if isinstance(latest_snapshot, Mapping):
                freshness = freshness_by_id.get(str(latest_snapshot["snapshot_id"]))
                freshness_status = str((freshness or {}).get("freshness_status") or "unknown")
            if (
                isinstance(latest_snapshot, Mapping)
                and isinstance(baseline_snapshot, Mapping)
                and baseline_visible
                and not latest_integrity_error
            ):
                base_context = freshness_by_id.get(str(baseline_snapshot["snapshot_id"]))
                comparison_context = freshness_by_id.get(str(latest_snapshot["snapshot_id"]))
                comparison_result = self.comparison.compare_preloaded_with_connection(
                    conn,
                    base=baseline_snapshot,
                    comparison=latest_snapshot,
                    comparison_cutoff=knowledge_cutoff_at,
                    base_current_context=base_context,
                    comparison_current_context=comparison_context,
                )
                freshness_status = str(
                    (comparison_context or {}).get("freshness_status") or freshness_status
                )
            stored_reference = None
            if isinstance(baseline_snapshot, Mapping):
                candidate = baseline_snapshot.get("output", {}).get(
                    "daily_research_context_reference"
                )
                if isinstance(candidate, Mapping):
                    stored_reference = dict(candidate)
            current_reference = self._build_context_reference(
                market_date=market_date,
                knowledge_cutoff_at=knowledge_cutoff_at,
                phase13=phase13,
                eod=eod,
                phase15=phase15,
                phase16_item=phase16_item,
                phase16_response=phase16_response,
                context_reasons=[],
            )
            if baseline_snapshot is None:
                context_reasons = []
            else:
                context_reasons = context_change_reasons(
                    stored_reference, current_reference
                )
                if not isinstance(stored_reference, Mapping):
                    context_reasons = ["context_provenance_missing"]
            reason_codes = self._review_reason_codes(
                latest_result=latest_result,
                latest_integrity_error=latest_integrity_error,
                baseline_result=baseline_result,
                baseline_integrity_error=baseline_integrity_error,
                baseline_visible=baseline_visible,
                comparison_result=comparison_result,
                phase13=phase13,
                phase16_item=phase16_item,
                eod=eod,
                freshness_status=freshness_status,
                context_reasons=context_reasons,
            )
            current_reference = self._build_context_reference(
                market_date=market_date,
                knowledge_cutoff_at=knowledge_cutoff_at,
                phase13=phase13,
                eod=eod,
                phase15=phase15,
                phase16_item=phase16_item,
                phase16_response=phase16_response,
                context_reasons=context_reasons,
            )
            comparison_status = ResearchComparisonStatus.NOT_RUN.value
            comparison_delta = None
            review_state = ReviewState.NO_SNAPSHOT.value if latest_snapshot is None else ReviewState.BASELINE_NOT_SET.value
            if comparison_result:
                comparison_delta = comparison_result
                if comparison_result.get("status") == "incomparable_contract":
                    review_state = ReviewState.INCOMPARABLE_CONTRACT.value
                    comparison_status = ResearchComparisonStatus.INCOMPARABLE_CONTRACT.value
                else:
                    comparison_status = ResearchComparisonStatus.COMPARABLE.value
                    has_delta = comparison_has_deltas(
                        comparison_status=ResearchComparisonStatus.COMPARABLE,
                        stored_delta_count=len(comparison_result.get("stored_deltas", [])),
                        current_context_delta_count=len(comparison_result.get("current_context_deltas", [])),
                    )
                    review_state = (
                        ReviewState.COMPARABLE_WITH_DELTAS.value
                        if has_delta else ReviewState.COMPARABLE_WITHOUT_DELTAS.value
                    )
            elif latest_integrity_error or baseline_integrity_error:
                review_state = ReviewState.SNAPSHOT_INTEGRITY_ERROR.value
            elif any(code == "data_blocked" for code in reason_codes):
                review_state = ReviewState.BLOCKED.value
            elif any(code == "data_unknown" for code in reason_codes):
                review_state = ReviewState.UNKNOWN.value
            item_status = self._status_from_context(
                identity=phase13,
                phase16_item=phase16_item,
                eod=eod,
                latest_result=latest_result,
                integrity_error=latest_integrity_error or baseline_integrity_error,
                comparison_result=comparison_result,
                freshness_status=freshness_status,
            )
            flags = derive_review_flags(
                review_state=review_state,
                comparison_status=comparison_status,
                comparison_has_deltas=(
                    comparison_has_deltas(
                        comparison_status=ResearchComparisonStatus.COMPARABLE,
                        stored_delta_count=len((comparison_result or {}).get("stored_deltas", [])),
                        current_context_delta_count=len((comparison_result or {}).get("current_context_deltas", [])),
                    ) if comparison_result and comparison_result.get("status") == "available" else None
                ),
                freshness_status=freshness_status,
                item_status=item_status,
                reason_codes=reason_codes,
            )
            workflow_event = workflow_events.get(item_id)
            workflow_latest = workflow_snapshots.get(symbol)
            phase16_safe = phase16_item or {
                "canonical_symbol": symbol,
                "venue": phase13.get("venue"),
                "item_state": "unknown",
                "coverage_status": "unknown",
                "reason_codes": ["lineage_unresolved"],
            }
            latest_ref = self._snapshot_reference(
                latest_result,
                symbol=symbol,
                requested_cutoff=knowledge_cutoff_at,
                eligible_for_requested_d_k=bool(latest_snapshot),
            )
            workflow_ref = self._workflow_snapshot_reference(
                workflow_latest,
                symbol=symbol,
                requested_cutoff=knowledge_cutoff_at,
            )
            baseline_ref = self._snapshot_reference(
                baseline_result,
                symbol=symbol,
                requested_cutoff=knowledge_cutoff_at,
                eligible_for_requested_d_k=baseline_visible,
            )
            workflow_event_ref = self._review_event_reference(
                workflow_event, knowledge_cutoff_at=knowledge_cutoff_at
            )
            baseline_eligibility = baseline_selection_eligibility(
                item_symbol=symbol,
                snapshot=latest_snapshot,
                knowledge_cutoff_at=knowledge_cutoff_at,
                integrity_error=latest_integrity_error,
                contract_supported=self._snapshot_contract_supported(
                    latest_snapshot, item_symbol=symbol
                ),
            )
            # A no-snapshot item has no proposed baseline.  It remains a
            # limited workflow item rather than exposing a second error state.
            if latest_snapshot is None:
                baseline_eligibility = baseline_selection_eligibility(
                    item_symbol=symbol,
                    snapshot=None,
                    knowledge_cutoff_at=knowledge_cutoff_at,
                )
            item = {
                "watchlist_reference": {
                    "watchlist_item_id": membership.get("watchlist_item_id"),
                    "symbol": symbol,
                    "membership_state": membership.get("membership_state"),
                    "created_at": membership.get("created_at"),
                    "updated_at": membership.get("updated_at"),
                    "archived_at": membership.get("archived_at"),
                    "workflow_contract_version": membership.get("workflow_contract_version"),
                },
                "canonical_symbol": symbol,
                "venue": phase13.get("venue"),
                "identity": phase13,
                "status": item_status,
                "review_state": review_state,
                "workflow_review_state": "acknowledged" if workflow_event else "not_acknowledged",
                "workflow_evaluated_at": workflow_evaluated_at,
                "review_needed": flags["review_needed"],
                "review_blocked": flags["review_blocked"],
                "review_limited": flags["review_limited"],
                "reason_codes": reason_codes,
                "latest_snapshot_reference": latest_ref,
                "workflow_latest_snapshot_reference": workflow_ref,
                "acknowledged_baseline_reference": baseline_ref if baseline_visible else None,
                "k_visible_acknowledgment_reference": self._review_event_reference(
                    event, knowledge_cutoff_at=knowledge_cutoff_at
                ) if event else None,
                "workflow_acknowledgment_reference": workflow_event_ref,
                "comparison_status": comparison_status,
                "comparison_has_deltas": (
                    comparison_has_deltas(
                        comparison_status=ResearchComparisonStatus.COMPARABLE,
                        stored_delta_count=len((comparison_result or {}).get("stored_deltas", [])),
                        current_context_delta_count=len((comparison_result or {}).get("current_context_deltas", [])),
                    ) if comparison_result and comparison_result.get("status") == "available" else None
                ),
                "stored_delta_summary": self._delta_summary(
                    (comparison_result or {}).get("stored_deltas")
                ),
                "current_context_delta_summary": self._delta_summary(
                    (comparison_result or {}).get("current_context_deltas")
                ),
                "phase16_context": {
                    "item": phase16_safe,
                    "aggregate_status": phase16_response.get("status"),
                    "aggregate_scope": "phase16_full_twse_tpex_context",
                    "aggregate_completeness_proven": False,
                },
                "freshness_status": freshness_status,
                "quality": {
                    "status": item_status,
                    "phase13_status": phase13_source.get("status") if phase13_source else "unknown",
                    "phase14_status": eod.get("status"),
                    "phase15_status": phase15.get("status"),
                    "phase16_status": phase16_safe.get("item_state"),
                    "integrity_status": "invalid" if latest_integrity_error or baseline_integrity_error else "valid",
                    "aggregate_completeness_proven": False,
                    "reasons": reason_codes,
                },
                "provenance": {
                    "status": "available" if verify_daily_context_reference(current_reference) else "invalid",
                    "current_reference": current_reference,
                    "baseline_reference": stored_reference,
                    "context_digest_valid": verify_daily_context_reference(current_reference),
                    "context_change_reasons": context_reasons,
                    "integration_contract_version": DAILY_RESEARCH_SNAPSHOT_INTEGRATION_VERSION,
                },
                "permitted_actions": {},
                "baseline_selection_policy_version": DAILY_BASELINE_SELECTION_POLICY_VERSION,
                "baseline_selection_reason_registry_version": DAILY_BASELINE_SELECTION_REASON_REGISTRY_VERSION,
                "baseline_selection_eligible": baseline_eligibility["baseline_selection_eligible"],
                "baseline_selection_blocked": baseline_eligibility["baseline_selection_blocked"],
                "baseline_selection_reason_codes": baseline_eligibility["baseline_selection_reason_codes"],
                "_comparison": comparison_delta,
            }
            item["permitted_actions"] = {
                "open_review": True,
                "acknowledge": bool(baseline_eligibility["baseline_selection_eligible"]),
                "refresh_snapshot": self._refresh_context_eligible(item),
                "archive": membership.get("membership_state") == MembershipState.ACTIVE.value,
                "restore": membership.get("membership_state") == MembershipState.ARCHIVED.value,
            }
            results.append(item)
        return results

    def _response(
        self,
        conn: sqlite3.Connection,
        *,
        memberships: list[dict[str, Any]],
        has_more: bool,
        next_cursor: str | None,
        market_date: str,
        knowledge_cutoff_at: str,
        request_received_at: str,
        workflow_evaluated_at: str,
        population: list[dict[str, Any]],
        include_comparison: bool = False,
    ) -> dict[str, Any]:
        item_ids = [str(item["watchlist_item_id"]) for item in memberships]
        symbols = [str(item["symbol"]) for item in memberships]
        k_events = self.workflow.latest_review_events_as_of_with_connection(
            conn, item_ids, knowledge_cutoff_at
        )
        workflow_events = self.workflow.latest_review_events_as_of_with_connection(
            conn, item_ids, workflow_evaluated_at
        )
        k_snapshots = self.snapshots.daily_latest_for_symbols_as_of_with_connection(
            conn, symbols, knowledge_cutoff_at
        )
        workflow_snapshots = self.snapshots.latest_for_symbols_as_of_with_connection(
            conn, symbols, workflow_evaluated_at
        )
        baseline_ids = [
            str(event["acknowledged_snapshot_id"])
            for event in k_events.values()
            if event.get("acknowledged_snapshot_id")
        ]
        baselines = self.snapshots.get_many_for_daily_with_connection(conn, baseline_ids)
        phase13_contexts = self.universe.contexts_for_symbols_with_connection(
            conn, canonical_symbols=symbols, knowledge_cutoff_at=knowledge_cutoff_at
        )
        phase16_response = self._phase16_projection(
            conn,
            symbols=symbols,
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
        )
        items = self._project_items(
            conn,
            memberships=memberships,
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            workflow_evaluated_at=workflow_evaluated_at,
            k_events=k_events,
            workflow_events=workflow_events,
            k_snapshots=k_snapshots,
            workflow_snapshots=workflow_snapshots,
            baselines=baselines,
            phase13_contexts=phase13_contexts,
            phase16_response=phase16_response,
        )
        for item in items:
            comparison = item.pop("_comparison", None)
            if include_comparison:
                item["comparison"] = comparison
        statuses = [str(item["status"]) for item in items]
        page_status = reduce_page_status(statuses)
        page_counts = {
            state: statuses.count(state)
            for state in ("available", "partial", "insufficient_data", "unknown", "blocked")
        }
        review_needed_count = sum(bool(item["review_needed"]) for item in items)
        review_blocked_count = sum(bool(item["review_blocked"]) for item in items)
        review_limited_count = sum(bool(item["review_limited"]) for item in items)
        aggregate = self._phase16_safe_aggregate(phase16_response)
        market_status = str(phase16_response.get("status") or "unknown")
        preflight_reasons: list[str] = []
        if market_status == "blocked":
            preflight_reasons.append("data_blocked")
        elif market_status in {"unknown", "insufficient_data"}:
            preflight_reasons.append("data_unknown")
        elif market_status == "partial":
            preflight_reasons.append("data_partial")
        preflight = {
            "status": reduce_preflight_status(page_status, market_status),
            "status_scope": "full_daily_preflight",
            "market_context_status": market_status,
            "market_context_status_scope": "phase16_full_twse_tpex_context",
            "request_valid": True,
            "active_queue_total_count": len(population),
            "active_population_checksum": active_population_checksum(population),
            "page_item_count": len(items),
            "page_has_more": has_more,
            "page_review_needed_count": review_needed_count,
            "page_review_blocked_count": review_blocked_count,
            "page_review_limited_count": review_limited_count,
            "page_status_counts": page_counts,
            "venue_statuses": {
                venue: data.get("assembly_status")
                for venue, data in (phase16_response.get("per_venue") or {}).items()
            },
            "reasons": normalize_daily_reasons(preflight_reasons),
            "count_scope": {
                "active_queue_total_count": "active_workflow_population",
                "page_status_counts": "page_items",
                "phase16_aggregate": "phase16_full_twse_tpex_context",
            },
            "aggregate_completeness_proven": False,
            "preflight_policy_version": DAILY_RESEARCH_PREFLIGHT_VERSION,
        }
        request = {
            "market_date": market_date,
            "knowledge_cutoff_at": knowledge_cutoff_at,
            "request_received_at": request_received_at,
            "workflow_evaluated_at": workflow_evaluated_at,
            "population": "active_research_queue",
            "population_evaluated_at": workflow_evaluated_at,
            "internal_venue_scope": "TWSE_TPEX",
            "d_k_policy_version": DAILY_RESEARCH_D_K_VERSION,
            "workflow_time_policy_version": DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
            "snapshot_selection_policy_version": DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
            "order_version": DAILY_RESEARCH_ORDER_VERSION,
        }
        return {
            "contract_version": DAILY_RESEARCH_REVIEW_CONTEXT_VERSION,
            "policy_version": DAILY_RESEARCH_REVIEW_CONTEXT_POLICY_VERSION,
            "workflow_time_policy_version": DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
            "snapshot_selection_policy_version": DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
            "reason_registry_version": DAILY_RESEARCH_REASON_REGISTRY_VERSION,
            "d_k_policy_version": DAILY_RESEARCH_D_K_VERSION,
            "order_version": DAILY_RESEARCH_ORDER_VERSION,
            "cursor_version": DAILY_RESEARCH_CURSOR_VERSION,
            "snapshot_integration_version": DAILY_RESEARCH_SNAPSHOT_INTEGRATION_VERSION,
            "status_scope": "page_items",
            "request": request,
            "status": page_status,
            "preflight": preflight,
            "aggregate": aggregate,
            "items": items,
            "limit": len(items) if not has_more else min(50, len(items)),
            "next_cursor": next_cursor,
        }

    def list(
        self,
        *,
        market_date: str,
        knowledge_cutoff_at: str,
        request_received_at: datetime | str,
        limit: int = 25,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("daily_limit_invalid")
        received = _received_timestamp(request_received_at)
        target, cutoff, _ = validate_daily_d_k(market_date, knowledge_cutoff_at, received)
        workflow_evaluated_at = received
        with self.read_repository.transaction() as conn:
            population = self.workflow.active_population_keys_with_connection(conn)
            checksum = active_population_checksum(population)
            decoded = None
            if cursor:
                decoded = decode_daily_cursor(
                    cursor,
                    market_date=target,
                    knowledge_cutoff_at=cutoff,
                    limit=limit,
                )
                if decoded.active_population_checksum != checksum:
                    raise DailyResearchCursorPopulationChanged(
                        "cursor_population_changed"
                    )
                key_set = {
                    (str(item["symbol"]), str(item["watchlist_item_id"]))
                    for item in population
                }
                if (decoded.last_symbol, decoded.last_watchlist_item_id) not in key_set:
                    raise DailyResearchCursorError("daily_cursor_impossible_tuple")
            rows = self.workflow.active_memberships_page_with_connection(
                conn,
                limit=limit,
                last_symbol=decoded.last_symbol if decoded else None,
                last_watchlist_item_id=decoded.last_watchlist_item_id if decoded else None,
            )
            has_more = len(rows) > limit
            memberships = rows[:limit]
            next_cursor = None
            if has_more and memberships:
                last = memberships[-1]
                next_cursor = DailyResearchCursor(
                    market_date=target,
                    knowledge_cutoff_at=cutoff,
                    limit=limit,
                    active_population_checksum=checksum,
                    last_symbol=str(last["symbol"]),
                    last_watchlist_item_id=str(last["watchlist_item_id"]),
                ).encode()
            response = self._response(
                conn,
                memberships=memberships,
                has_more=has_more,
                next_cursor=next_cursor,
                market_date=target,
                knowledge_cutoff_at=cutoff,
                request_received_at=received,
                workflow_evaluated_at=workflow_evaluated_at,
                population=population,
            )
            response["limit"] = limit
            return response

    def detail(
        self,
        item_id: str,
        *,
        market_date: str,
        knowledge_cutoff_at: str,
        request_received_at: datetime | str,
    ) -> dict[str, Any]:
        received = _received_timestamp(request_received_at)
        target, cutoff, _ = validate_daily_d_k(market_date, knowledge_cutoff_at, received)
        with self.read_repository.transaction() as conn:
            membership = self.workflow.membership_with_connection(conn, item_id)
            if membership is None or membership.get("membership_state") != MembershipState.ACTIVE.value:
                raise DailyResearchItemNotFound(item_id)
            population = self.workflow.active_population_keys_with_connection(conn)
            response = self._response(
                conn,
                memberships=[membership],
                has_more=False,
                next_cursor=None,
                market_date=target,
                knowledge_cutoff_at=cutoff,
                request_received_at=received,
                workflow_evaluated_at=received,
                population=population,
                include_comparison=True,
            )
            response["item"] = response.pop("items")[0]
            response["limit"] = 1
            return response

    @staticmethod
    def _daily_baseline_idempotency_storage_key(raw_key: str) -> str:
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        return "daily-baseline-selection-v1:" + digest

    @staticmethod
    def _baseline_payload_fingerprint(
        *,
        item_id: str,
        snapshot_id: str,
        knowledge_cutoff_at: str,
    ) -> str:
        return hashlib.sha256(canonical_json({
            "watchlist_item_id": item_id,
            "baseline_snapshot_id": snapshot_id,
            "knowledge_cutoff_at": knowledge_cutoff_at,
            "baseline_selection_policy_version": DAILY_BASELINE_SELECTION_POLICY_VERSION,
        }).encode("utf-8")).hexdigest()

    def select_baseline(
        self,
        item_id: str,
        *,
        baseline_snapshot_id: str,
        knowledge_cutoff_at: str,
        request_received_at: datetime | str,
        idempotency_key: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        raw_key = str(idempotency_key or "").strip()
        if not raw_key:
            raise ValueError("idempotency_key_required")
        received = _received_timestamp(request_received_at)
        cutoff = canonical_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff")
        if cutoff > received:
            raise ValueError("knowledge_cutoff_after_request")
        item_id = str(item_id).strip()
        snapshot_id = str(baseline_snapshot_id or "").strip()
        if not item_id or not snapshot_id:
            raise ValueError("baseline_selection_identifiers_required")
        storage_key = self._daily_baseline_idempotency_storage_key(raw_key)
        fingerprint = self._baseline_payload_fingerprint(
            item_id=item_id,
            snapshot_id=snapshot_id,
            knowledge_cutoff_at=cutoff,
        )
        conn = self.workflow._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = self.workflow.event_by_idempotency_key_with_connection(
                conn, storage_key
            )
            if existing:
                same = (
                    existing.get("watchlist_item_id") == item_id
                    and existing.get("acknowledged_snapshot_id") == snapshot_id
                    and canonical_timestamp(existing.get("comparison_cutoff_at")) == cutoff
                )
                if not same:
                    raise DailyResearchBaselineIdempotencyConflict(
                        "baseline_selection_idempotency_conflict"
                    )
                event = {**existing, "created": False}
                conn.commit()
                return self._baseline_response(
                    event, cutoff=cutoff, received=received, correlation_id=correlation_id
                )
            # The raw key belongs to the legacy Phase 12 namespace.  A Daily
            # request must fail closed rather than silently claim that event.
            if self.workflow.event_by_idempotency_key_with_connection(conn, raw_key):
                raise DailyResearchBaselineIdempotencyConflict(
                    "baseline_selection_idempotency_conflict"
                )
            item = self.workflow.membership_with_connection(conn, item_id)
            if item is None:
                raise DailyResearchItemNotFound(item_id)
            if item.get("membership_state") != MembershipState.ACTIVE.value:
                raise DailyResearchItemInactive(item_id)
            row = conn.execute(
                "SELECT * FROM analysis_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            snapshot: dict[str, Any] | None = None
            integrity_error = False
            if row is not None:
                try:
                    snapshot = self.snapshots.get_with_connection(conn, snapshot_id)
                except SnapshotIntegrityError:
                    integrity_error = True
            comparison_supported = self._snapshot_contract_supported(
                snapshot, item_symbol=str(item["symbol"])
            )
            eligibility = baseline_selection_eligibility(
                item_symbol=str(item["symbol"]),
                snapshot=snapshot,
                knowledge_cutoff_at=cutoff,
                integrity_error=integrity_error,
                contract_supported=comparison_supported,
            )
            if eligibility["baseline_selection_blocked"]:
                raise DailyResearchBaselineNotEligible(eligibility)
            event = self.workflow.append_review_event_with_connection(
                conn,
                ReviewAcknowledgment(
                    watchlist_item_id=item_id,
                    acknowledged_snapshot_id=snapshot_id,
                    comparison_cutoff_at=cutoff,
                    idempotency_key=storage_key,
                ),
                reviewed_at=received,
            )
            conn.commit()
            return self._baseline_response(
                event, cutoff=cutoff, received=received, correlation_id=correlation_id
            )
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _baseline_response(
        event: Mapping[str, Any], *, cutoff: str, received: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        payload = {
            "status": "available",
            "baseline_selection_policy_version": DAILY_BASELINE_SELECTION_POLICY_VERSION,
            "baseline_selection_reason_registry_version": DAILY_BASELINE_SELECTION_REASON_REGISTRY_VERSION,
            "baseline_selection_event": {
                "review_event_id": event.get("review_event_id"),
                "watchlist_item_id": event.get("watchlist_item_id"),
                "baseline_snapshot_id": event.get("acknowledged_snapshot_id"),
                "knowledge_cutoff_at": cutoff,
                "selected_at": event.get("reviewed_at") or received,
                "created": bool(event.get("created")),
                "workflow_contract_version": event.get(
                    "workflow_contract_version", WORKFLOW_CONTRACT_VERSION
                ),
            },
            "workflow_evaluated_at": received,
            "correlation_id": correlation_id,
        }
        return payload

    def refresh_snapshot(
        self,
        item_id: str,
        *,
        market_date: str,
        loaded_knowledge_cutoff_at: str,
        expected_snapshot_id: str | None,
        advance_knowledge_cutoff: bool,
        request_received_at: datetime | str,
        idempotency_key: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Explicit refresh command; no GET path calls this method."""
        if not advance_knowledge_cutoff:
            raise ValueError("refresh_requires_explicit_cutoff_advance")
        if not str(idempotency_key or "").strip():
            raise ValueError("idempotency_key_required")
        received = _received_timestamp(request_received_at)
        loaded_d, loaded_k, _ = validate_daily_d_k(
            market_date, loaded_knowledge_cutoff_at, received
        )
        new_k = received
        new_d = loaded_d
        if self.analysis_builder is None:
            raise DailyResearchRefreshNotEligible({
                "eligible": False,
                "error": "refresh_analysis_builder_unavailable",
                "market_date": new_d,
                "knowledge_cutoff_at": new_k,
            })
        from src.services.evidence_analysis_service import EvidenceAnalysisService

        conn = self.snapshots._connect()
        try:
            # Refresh is one caller-owned transaction.  The context gate,
            # analysis reads, expected-latest check, immutable snapshot and
            # idempotency ledger must observe one database state.
            self.read_repository._verify_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            item = self.workflow.membership_with_connection(conn, item_id)
            if item is None:
                raise DailyResearchItemNotFound(item_id)
            if item.get("membership_state") != MembershipState.ACTIVE.value:
                raise DailyResearchItemInactive(item_id)
            population = self.workflow.active_population_keys_with_connection(conn)
            context = self._response(
                conn,
                memberships=[item],
                has_more=False,
                next_cursor=None,
                market_date=new_d,
                knowledge_cutoff_at=new_k,
                request_received_at=received,
                workflow_evaluated_at=received,
                population=population,
                include_comparison=True,
            )
            current_item = next(
                (
                    value for value in context["items"]
                    if value["watchlist_reference"]["watchlist_item_id"] == item_id
                ),
                None,
            )
            if current_item is None:
                raise DailyResearchItemNotFound(item_id)
            gate = {
                "eligible": self._refresh_context_eligible(current_item),
                "analysis_sections": None,
                "item_status": current_item["status"],
                "reason_codes": current_item["reason_codes"],
            }
            if not gate["eligible"]:
                raise DailyResearchRefreshNotEligible(gate)
            analysis = self.analysis_builder(
                str(item["symbol"]),
                new_k,
                {
                    "market_date": new_d,
                    "loaded_knowledge_cutoff_at": loaded_k,
                    "expected_snapshot_id": expected_snapshot_id,
                    "options": options or {},
                },
                conn,
            )
            if not isinstance(analysis, Mapping):
                raise DailyResearchRefreshNotEligible({
                    "eligible": False,
                    "error": "required_analysis_section_unavailable",
                    "section": None,
                })
            sections = evaluate_required_analysis_sections(analysis)
            gate["analysis_sections"] = sections
            if not sections["eligible"]:
                raise DailyResearchRefreshNotEligible(gate)
            reference = current_item["provenance"]["current_reference"]
            analysis_copy = copy.deepcopy(dict(analysis))
            analysis_copy["symbol"] = str(item["symbol"])
            analysis_copy["knowledge_cutoff_at"] = new_k
            analysis_copy["daily_research_context_reference"] = reference
            analysis_copy["snapshot_id"] = None
            evidence = EvidenceAnalysisService(
                self.db_path,
                auto_migrate=False,
                snapshot_repository=self.snapshots,
            )
            binding = conn.execute(
                "SELECT 1 FROM analysis_snapshot_idempotency_keys "
                "WHERE idempotency_key = ?",
                (str(idempotency_key).strip(),),
            ).fetchone()
            if binding is None:
                latest = conn.execute(
                    "SELECT snapshot_id FROM analysis_snapshots "
                    "WHERE symbol = ? ORDER BY created_at DESC, snapshot_id DESC LIMIT 1",
                    (str(item["symbol"]),),
                ).fetchone()
                actual_snapshot_id = str(latest["snapshot_id"]) if latest else None
                expected = str(expected_snapshot_id) if expected_snapshot_id else None
                if actual_snapshot_id != expected:
                    raise DailyResearchRefreshRace({
                        "eligible": False,
                        "reason": "expected_snapshot_changed",
                        "expected_snapshot_id": expected,
                        "actual_snapshot_id": actual_snapshot_id,
                    })
            result = evidence.create_snapshot(
                analysis=analysis_copy,
                capture_mode=CaptureMode.LIVE_REFRESH,
                idempotency_key=str(idempotency_key).strip(),
                supersedes_snapshot_id=expected_snapshot_id,
                connection=conn,
                created_at=received,
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "status": "available",
            "created": bool(result.get("created")),
            "refresh_received_at": received,
            "new_knowledge_cutoff_at": new_k,
            "snapshot": self._snapshot_reference(
                {"snapshot": result, "snapshot_id": result.get("snapshot_id")},
                symbol=str(item["symbol"]),
                requested_cutoff=new_k,
                eligible_for_requested_d_k=True,
            ),
            "refresh_gate": gate,
        }


__all__ = [
    "DailyResearchBaselineIdempotencyConflict",
    "DailyResearchBaselineNotEligible",
    "DailyResearchCursorPopulationChanged",
    "DailyResearchItemInactive",
    "DailyResearchItemNotFound",
    "DailyResearchRefreshNotEligible",
    "DailyResearchRefreshRace",
    "DailyResearchReviewContextService",
]
