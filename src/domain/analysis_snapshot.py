"""Phase 7 synthesis-profile and immutable analysis-snapshot contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from src.domain.valuation import ApprovalStatus, normalize_utc_timestamp


class SynthesisProfileScope(str, Enum):
    GLOBAL = "global"
    SYMBOL = "symbol"


class SynthesisRecordStatus(str, Enum):
    AVAILABLE = "available"
    REVOKED = "revoked"


class CaptureMode(str, Enum):
    LIVE_REFRESH = "live_refresh"
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decimal_text(value: str, field_name: str, *, positive: bool = False) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise ValueError(f"{field_name} must be a finite positive decimal")
    return format(number.normalize(), "f")


@dataclass(frozen=True)
class SynthesisProfileRevision:
    logical_profile_id: str
    revision_number: int
    scope: SynthesisProfileScope
    allowed_method_families: tuple[str, ...]
    overlap_tolerance: str
    evidence_strength_policy: tuple[dict[str, Any], ...]
    available_at: str
    created_by: str
    rationale: str
    scope_value: str | None = None
    revision_of: str | None = None
    status: SynthesisRecordStatus = SynthesisRecordStatus.AVAILABLE
    role_compatibility_policy: str = "target_only_for_strength_support_alignment"
    point_to_range_policy: str = "relative_ratio"
    boundary_policy: str = "closed"
    cluster_policy: str = "maximal_active_target_sets_v1"
    dependency_policy_version: str = "connected_components_v1"
    calculation_quantum: str = "0.0001"
    display_quantum: str = "0.01"
    rounding_mode: str = "ROUND_HALF_UP"

    def canonical_payload(self) -> dict[str, Any]:
        logical_id = self.logical_profile_id.strip()
        if not logical_id:
            raise ValueError("logical_profile_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.revision_of is not None:
            raise ValueError("first revision cannot specify revision_of")
        if self.revision_number > 1 and not self.revision_of:
            raise ValueError("revision_of is required after revision 1")
        if self.scope is SynthesisProfileScope.GLOBAL:
            if self.scope_value is not None:
                raise ValueError("global profile cannot specify scope_value")
            scope_value = None
        else:
            if self.scope_value is None or not self.scope_value.strip():
                raise ValueError("symbol profile requires scope_value")
            scope_value = self.scope_value.strip().upper()
        allowed = tuple(sorted(set(item.strip() for item in self.allowed_method_families)))
        if not allowed or any(not item for item in allowed):
            raise ValueError("allowed_method_families cannot be empty")
        if not set(allowed).issubset({"VAL-01", "FB-03", "FB-04"}):
            raise ValueError("Phase 7 profile contains an unsupported method family")
        tolerance = _decimal_text(self.overlap_tolerance, "overlap_tolerance")
        if Decimal(tolerance) < 0 or Decimal(tolerance) >= 1:
            raise ValueError("overlap_tolerance must be at least zero and less than one")
        thresholds: list[dict[str, Any]] = []
        previous = 1
        labels: set[str] = set()
        for item in self.evidence_strength_policy:
            count = int(item.get("minimum_independent_target_components", 0))
            label = str(item.get("label", "")).strip()
            if count < 2:
                raise ValueError("TGT-01 strength thresholds must start at two methods")
            if count <= previous:
                raise ValueError("evidence strength thresholds must be strictly increasing")
            if label not in {"low", "moderate", "high"} or label in labels:
                raise ValueError("evidence strength labels must be unique low/moderate/high values")
            thresholds.append({
                "minimum_independent_target_components": count,
                "label": label,
            })
            previous = count
            labels.add(label)
        if not thresholds:
            raise ValueError("evidence_strength_policy is required")
        fixed = {
            "role_compatibility_policy": (
                self.role_compatibility_policy,
                "target_only_for_strength_support_alignment",
            ),
            "point_to_range_policy": (self.point_to_range_policy, "relative_ratio"),
            "boundary_policy": (self.boundary_policy, "closed"),
            "cluster_policy": (self.cluster_policy, "maximal_active_target_sets_v1"),
            "dependency_policy_version": (
                self.dependency_policy_version,
                "connected_components_v1",
            ),
            "rounding_mode": (self.rounding_mode, "ROUND_HALF_UP"),
        }
        for field_name, (actual, expected) in fixed.items():
            if actual != expected:
                raise ValueError(f"{field_name} must be {expected}")
        if not self.created_by.strip() or not self.rationale.strip():
            raise ValueError("created_by and rationale are required")
        return {
            "logical_profile_id": logical_id,
            "revision_number": self.revision_number,
            "revision_of": self.revision_of,
            "scope": self.scope.value,
            "scope_value": scope_value,
            "allowed_method_families": list(allowed),
            "role_compatibility_policy": self.role_compatibility_policy,
            "point_to_range_policy": self.point_to_range_policy,
            "overlap_tolerance": tolerance,
            "boundary_policy": self.boundary_policy,
            "cluster_policy": self.cluster_policy,
            "dependency_policy_version": self.dependency_policy_version,
            "evidence_strength_policy": thresholds,
            "calculation_quantum": _decimal_text(
                self.calculation_quantum, "calculation_quantum", positive=True
            ),
            "display_quantum": _decimal_text(
                self.display_quantum, "display_quantum", positive=True
            ),
            "rounding_mode": self.rounding_mode,
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "created_by": self.created_by.strip(),
            "rationale": self.rationale.strip(),
            "status": self.status.value,
        }


@dataclass(frozen=True)
class SynthesisProfileApproval:
    approval_id: str
    profile_revision_id: str
    decision: ApprovalStatus
    rule_id: str
    rule_version: str
    evidence_level: str
    implementation_mode: str
    project_operationalization: bool
    approved_by: str
    rationale: str
    approved_at: str

    def canonical_payload(self) -> dict[str, Any]:
        if self.rule_id != "TGT-01":
            raise ValueError("synthesis profile approval must use TGT-01")
        if self.evidence_level != "C":
            raise ValueError("TGT-01 must remain evidence level C")
        if self.implementation_mode != "project_operationalization":
            raise ValueError("TGT-01 must remain a project operationalization")
        if not self.project_operationalization:
            raise ValueError("TGT-01 approval requires project_operationalization")
        required = {
            "approval_id": self.approval_id,
            "profile_revision_id": self.profile_revision_id,
            "rule_version": self.rule_version,
            "approved_by": self.approved_by,
            "rationale": self.rationale,
        }
        if any(not value.strip() for value in required.values()):
            raise ValueError("approval identifiers, actor, rationale, and version are required")
        return {
            "approval_id": self.approval_id,
            "profile_revision_id": self.profile_revision_id,
            "decision": self.decision.value,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "evidence_level": self.evidence_level,
            "implementation_mode": self.implementation_mode,
            "project_operationalization": self.project_operationalization,
            "approved_by": self.approved_by.strip(),
            "rationale": self.rationale.strip(),
            "approved_at": normalize_utc_timestamp(self.approved_at, "approved_at"),
        }


@dataclass(frozen=True)
class AnalysisSnapshot:
    symbol: str
    knowledge_cutoff_at: str
    capture_mode: CaptureMode
    model_version: str
    used_rule_versions: dict[str, str]
    source_resource_versions: list[dict[str, Any]]
    manual_approval_ids: list[str]
    output: dict[str, Any]
    created_at: str
    synthesis_profile_revision_id: str | None = None
    synthesis_profile_approval_id: str | None = None
    supersedes_snapshot_id: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        symbol = self.symbol.strip().upper()
        if not symbol or not self.model_version.strip():
            raise ValueError("snapshot symbol and model_version are required")
        if bool(self.synthesis_profile_revision_id) != bool(
            self.synthesis_profile_approval_id
        ):
            raise ValueError("synthesis profile revision and approval IDs must appear together")
        cutoff = normalize_utc_timestamp(
            self.knowledge_cutoff_at, "knowledge_cutoff_at"
        )
        created = normalize_utc_timestamp(self.created_at, "created_at")
        return {
            "symbol": symbol,
            "knowledge_cutoff_at": cutoff,
            "capture_mode": self.capture_mode.value,
            "model_version": self.model_version.strip(),
            "synthesis_profile_revision_id": self.synthesis_profile_revision_id,
            "synthesis_profile_approval_id": self.synthesis_profile_approval_id,
            "used_rule_versions": dict(sorted(self.used_rule_versions.items())),
            "source_resource_versions": sorted(
                self.source_resource_versions,
                key=lambda item: canonical_json(item),
            ),
            "manual_approval_ids": sorted(set(self.manual_approval_ids)),
            "output": self.output,
            "created_at": created,
            "supersedes_snapshot_id": self.supersedes_snapshot_id,
        }
