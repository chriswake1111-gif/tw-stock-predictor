"""Phase 8 immutable historical scenario-performance contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from src.domain.analysis_snapshot import canonical_json, sha256_json
from src.domain.valuation import ApprovalStatus, normalize_utc_timestamp


EVALUATOR_VERSION = "phase8_historical_scenario_v1"


class EvaluationRecordStatus(str, Enum):
    AVAILABLE = "available"
    REVOKED = "revoked"


class EvaluationOrigin(str, Enum):
    PROSPECTIVE_SNAPSHOT = "prospective_snapshot"
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"


class EvaluationSubjectType(str, Enum):
    TARGET_CANDIDATE = "target_candidate"
    SUPPORT_CANDIDATE = "support_candidate"
    TARGET_CLUSTER = "target_cluster"


def decimal_text(value: Any, field_name: str, *, positive: bool = False) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise ValueError(f"{field_name} must be a finite{' positive' if positive else ''} decimal")
    return format(number.normalize(), "f")


def _date(value: str, field_name: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO date") from exc


def _sha256(value: str, field_name: str) -> str:
    candidate = str(value).strip().lower()
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return candidate


@dataclass(frozen=True)
class EvaluationProfileRevision:
    logical_profile_id: str
    revision_number: int
    horizons_sessions: tuple[int, ...]
    available_at: str
    created_by: str
    rationale: str
    previous_revision_id: str | None = None
    status: EvaluationRecordStatus = EvaluationRecordStatus.AVAILABLE
    start_policy: str = "next_symbol_tradable_session_after_cutoff_local_date"
    start_price_policy: str = "adjusted_open"
    end_price_policy: str = "adjusted_close"
    target_touch_policy: str = "closed_boundary_intraday"
    already_in_range_policy: str = "separate_not_future_hit"
    benchmark_policy: str = "taiwan_taiex_price_return"
    outcome_completeness_policy: str = "complete_horizon_required"
    calculation_quantum: str = "0.00000001"
    display_quantum: str = "0.0001"

    def canonical_payload(self) -> dict[str, Any]:
        logical_id = self.logical_profile_id.strip()
        if not logical_id:
            raise ValueError("logical_profile_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if (self.revision_number == 1) != (self.previous_revision_id is None):
            raise ValueError("previous_revision_id is required only after revision 1")
        horizons = tuple(sorted(set(int(item) for item in self.horizons_sessions)))
        if horizons != (20, 60):
            raise ValueError("Phase 8 MVP horizons_sessions must be exactly 20 and 60")
        fixed = {
            "start_policy": "next_symbol_tradable_session_after_cutoff_local_date",
            "start_price_policy": "adjusted_open",
            "end_price_policy": "adjusted_close",
            "target_touch_policy": "closed_boundary_intraday",
            "already_in_range_policy": "separate_not_future_hit",
            "benchmark_policy": "taiwan_taiex_price_return",
            "outcome_completeness_policy": "complete_horizon_required",
        }
        for field_name, expected in fixed.items():
            if getattr(self, field_name) != expected:
                raise ValueError(f"{field_name} must be {expected}")
        if not self.created_by.strip() or not self.rationale.strip():
            raise ValueError("created_by and rationale are required")
        return {
            "logical_profile_id": logical_id,
            "revision_number": self.revision_number,
            "previous_revision_id": self.previous_revision_id,
            "horizons_sessions": list(horizons),
            **fixed,
            "calculation_quantum": decimal_text(self.calculation_quantum, "calculation_quantum", positive=True),
            "display_quantum": decimal_text(self.display_quantum, "display_quantum", positive=True),
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "status": self.status.value,
            "created_by": self.created_by.strip(),
            "rationale": self.rationale.strip(),
        }


@dataclass(frozen=True)
class EvaluationProfileApproval:
    approval_id: str
    profile_revision_id: str
    decision: ApprovalStatus
    approved_by: str
    rationale: str
    approved_at: str

    def canonical_payload(self) -> dict[str, Any]:
        if self.decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REVOKED}:
            raise ValueError("evaluation profile decision must be approved or revoked")
        required = (self.approval_id, self.profile_revision_id, self.approved_by, self.rationale)
        if any(not str(value).strip() for value in required):
            raise ValueError("approval identifiers, actor, and rationale are required")
        return {
            "approval_id": self.approval_id.strip(),
            "profile_revision_id": self.profile_revision_id.strip(),
            "decision": self.decision.value,
            "approved_by": self.approved_by.strip(),
            "rationale": self.rationale.strip(),
            "approved_at": normalize_utc_timestamp(self.approved_at, "approved_at"),
        }


@dataclass(frozen=True)
class OutcomeResourceManifest:
    manifest_id: str
    manifest_version: int
    dataset_name: str
    provider: str
    dataset_hash: str
    date_start: str
    date_end: str
    universe_definition: str
    calendar_resource: dict[str, Any]
    calendar_hash: str
    benchmark_resource: dict[str, Any]
    benchmark_hash: str
    ohlc_adjustment_contract: str
    corporate_action_contract: str
    symbol_resources: tuple[dict[str, Any], ...]
    ingested_at: str
    created_at: str

    def canonical_payload(self) -> dict[str, Any]:
        required = (
            self.manifest_id, self.dataset_name, self.provider,
            self.universe_definition, self.ohlc_adjustment_contract,
            self.corporate_action_contract,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("manifest identifiers and contracts are required")
        if self.manifest_version < 1:
            raise ValueError("manifest_version must be at least 1")
        start, end = _date(self.date_start, "date_start"), _date(self.date_end, "date_end")
        if start > end:
            raise ValueError("date_start cannot be later than date_end")
        resources = sorted((dict(item) for item in self.symbol_resources), key=canonical_json)
        if not resources:
            raise ValueError("symbol_resources cannot be empty")
        return {
            "manifest_id": self.manifest_id.strip(),
            "manifest_version": self.manifest_version,
            "dataset_name": self.dataset_name.strip(),
            "provider": self.provider.strip(),
            "dataset_hash": _sha256(self.dataset_hash, "dataset_hash"),
            "date_start": start,
            "date_end": end,
            "universe_definition": self.universe_definition.strip(),
            "calendar_resource": dict(self.calendar_resource),
            "calendar_hash": _sha256(self.calendar_hash, "calendar_hash"),
            "benchmark_resource": dict(self.benchmark_resource),
            "benchmark_hash": _sha256(self.benchmark_hash, "benchmark_hash"),
            "ohlc_adjustment_contract": self.ohlc_adjustment_contract.strip(),
            "corporate_action_contract": self.corporate_action_contract.strip(),
            "symbol_resources": resources,
            "ingested_at": normalize_utc_timestamp(self.ingested_at, "ingested_at"),
            "created_at": normalize_utc_timestamp(self.created_at, "created_at"),
        }

    def fingerprint(self) -> str:
        return sha256_json(self.canonical_payload())


@dataclass(frozen=True)
class EvaluationRun:
    evaluation_profile_revision_id: str
    evaluator_version: str
    evaluation_origin_policy: str
    snapshot_set_hash: str
    outcome_resource_manifest_id: str
    outcome_manifest_hash: str
    universe_definition: str
    created_at: str
    status: str = "completed"

    def semantic_payload(self) -> dict[str, Any]:
        if self.evaluation_origin_policy != "separate_by_evaluation_origin":
            raise ValueError("evaluation_origin_policy must separate evaluation origins")
        if self.status != "completed":
            raise ValueError("evaluation runs are persisted only when completed")
        required = (
            self.evaluation_profile_revision_id, self.evaluator_version,
            self.outcome_resource_manifest_id, self.universe_definition,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("evaluation run identifiers are required")
        return {
            "evaluation_profile_revision_id": self.evaluation_profile_revision_id.strip(),
            "evaluator_version": self.evaluator_version.strip(),
            "evaluation_origin_policy": self.evaluation_origin_policy,
            "snapshot_set_hash": _sha256(self.snapshot_set_hash, "snapshot_set_hash"),
            "outcome_resource_manifest_id": self.outcome_resource_manifest_id.strip(),
            "outcome_manifest_hash": _sha256(self.outcome_manifest_hash, "outcome_manifest_hash"),
            "universe_definition": self.universe_definition.strip(),
            "status": self.status,
        }


@dataclass(frozen=True)
class ScenarioEvaluation:
    snapshot_id: str
    symbol: str
    evaluation_origin: EvaluationOrigin
    subject_type: EvaluationSubjectType
    subject_id: str
    method_family: str
    semantic_role: str
    subject_metadata: dict[str, Any]
    knowledge_cutoff_at: str
    horizon_sessions: int
    terminal_outcome: str
    quality_status: str
    benchmark_status: str
    outcome_resource_manifest_id: str
    created_at: str
    evidence_strength: str | None = None
    evaluation_start_session: str | None = None
    evaluation_end_session: str | None = None
    market_sessions_skipped: int | None = None
    start_price: str | None = None
    target_low: str | None = None
    target_high: str | None = None
    target_position_at_start: str | None = None
    target_reached: bool | None = None
    first_target_reached_at: str | None = None
    trading_sessions_to_target: int | None = None
    maximum_upside_excursion: str | None = None
    maximum_downside_excursion: str | None = None
    directional_mfe: str | None = None
    directional_mae: str | None = None
    forward_return: str | None = None
    benchmark_return: str | None = None
    excess_return: str | None = None
    invalidation_status: str = "not_applicable"
    invalidation_reason: str = "INV-01_not_implemented"

    def canonical_payload(self) -> dict[str, Any]:
        if self.horizon_sessions not in {20, 60}:
            raise ValueError("horizon_sessions must be 20 or 60")
        if self.semantic_role not in {"target", "support"}:
            raise ValueError("semantic_role must be target or support")
        if self.invalidation_status != "not_applicable" or self.invalidation_reason != "INV-01_not_implemented":
            raise ValueError("Phase 8 MVP must keep INV-01 not applicable")
        required = (self.snapshot_id, self.symbol, self.subject_id, self.method_family, self.outcome_resource_manifest_id)
        if any(not str(value).strip() for value in required):
            raise ValueError("scenario evaluation identifiers are required")
        optional_decimals = (
            "start_price", "target_low", "target_high", "maximum_upside_excursion",
            "maximum_downside_excursion", "directional_mfe", "directional_mae",
            "forward_return", "benchmark_return", "excess_return",
        )
        payload = {
            "snapshot_id": self.snapshot_id.strip(),
            "symbol": self.symbol.strip().upper(),
            "evaluation_origin": self.evaluation_origin.value,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id.strip(),
            "method_family": self.method_family.strip(),
            "semantic_role": self.semantic_role,
            "evidence_strength": self.evidence_strength,
            "subject_metadata": dict(self.subject_metadata),
            "knowledge_cutoff_at": normalize_utc_timestamp(self.knowledge_cutoff_at, "knowledge_cutoff_at"),
            "horizon_sessions": self.horizon_sessions,
            "evaluation_start_session": _date(self.evaluation_start_session, "evaluation_start_session") if self.evaluation_start_session else None,
            "evaluation_end_session": _date(self.evaluation_end_session, "evaluation_end_session") if self.evaluation_end_session else None,
            "market_sessions_skipped": self.market_sessions_skipped,
            "target_position_at_start": self.target_position_at_start,
            "target_reached": self.target_reached,
            "first_target_reached_at": _date(self.first_target_reached_at, "first_target_reached_at") if self.first_target_reached_at else None,
            "trading_sessions_to_target": self.trading_sessions_to_target,
            "terminal_outcome": self.terminal_outcome,
            "quality_status": self.quality_status,
            "benchmark_status": self.benchmark_status,
            "invalidation_status": self.invalidation_status,
            "invalidation_reason": self.invalidation_reason,
            "outcome_resource_manifest_id": self.outcome_resource_manifest_id.strip(),
            "created_at": normalize_utc_timestamp(self.created_at, "created_at"),
        }
        for field_name in optional_decimals:
            value = getattr(self, field_name)
            payload[field_name] = decimal_text(value, field_name) if value is not None else None
        if payload["target_low"] is not None and payload["target_high"] is not None:
            if Decimal(payload["target_low"]) > Decimal(payload["target_high"]):
                raise ValueError("target_low cannot exceed target_high")
        return payload

    def calculation_fingerprint(self) -> str:
        semantic = self.canonical_payload()
        semantic.pop("created_at")
        return sha256_json(semantic)
