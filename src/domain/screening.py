"""Evidence Model V2 Phase 6 screening domain contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from src.domain.valuation import (
    ApprovalStatus,
    ForwardEPSSourceType,
    normalize_utc_timestamp,
)


class ScreeningRecordStatus(str, Enum):
    AVAILABLE = "available"
    REVOKED = "revoked"


class ScreeningProfileScope(str, Enum):
    GLOBAL = "global"
    SYMBOL = "symbol"
    INDUSTRY = "industry"


class ValuationBasis(str, Enum):
    SELF_HISTORY = "self_history"
    INDUSTRY = "industry"


class ForwardEPSGrowthConvention(str, Enum):
    CONSECUTIVE_FISCAL_YEAR_BASE = "consecutive_fiscal_year_base"


def _optional_finite(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite when supplied")
    return number


@dataclass(frozen=True)
class SecurityValuationObservation:
    logical_observation_id: str
    revision_number: int
    symbol: str
    metric_date: str
    source_name: str
    source_dataset: str
    available_at: str
    pe: float | None = None
    pb: float | None = None
    dividend_yield_ratio: float | None = None
    revision_of: str | None = None
    status: ScreeningRecordStatus = ScreeningRecordStatus.AVAILABLE
    dividend_yield_unit: str = "ratio"

    def canonical_payload(self) -> dict[str, Any]:
        if not self.logical_observation_id.strip():
            raise ValueError("logical_observation_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.revision_of is not None:
            raise ValueError("first revision cannot specify revision_of")
        if self.revision_number > 1 and not self.revision_of:
            raise ValueError("revision_of is required after revision 1")
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        try:
            date.fromisoformat(self.metric_date)
        except ValueError as exc:
            raise ValueError("metric_date must be an ISO-8601 date") from exc
        if not self.source_name.strip() or not self.source_dataset.strip():
            raise ValueError("source_name and source_dataset are required")
        if self.dividend_yield_unit != "ratio":
            raise ValueError("dividend_yield_unit must be ratio")
        if self.pe is None and self.pb is None and self.dividend_yield_ratio is None:
            raise ValueError("at least one valuation metric is required")
        return {
            "logical_observation_id": self.logical_observation_id.strip(),
            "revision_number": self.revision_number,
            "revision_of": self.revision_of,
            "symbol": symbol,
            "metric_date": self.metric_date,
            "pe": _optional_finite(self.pe, "pe"),
            "pb": _optional_finite(self.pb, "pb"),
            "dividend_yield_ratio": _optional_finite(
                self.dividend_yield_ratio, "dividend_yield_ratio"
            ),
            "source_name": self.source_name.strip(),
            "source_dataset": self.source_dataset.strip(),
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "status": self.status.value,
            "dividend_yield_unit": self.dividend_yield_unit,
        }


@dataclass(frozen=True)
class ScreeningProfileRevision:
    logical_profile_id: str
    revision_number: int
    scope: ScreeningProfileScope
    valuation_basis: ValuationBasis
    valuation_source_name: str
    valuation_source_dataset: str
    pe_percentile_max: float
    pb_percentile_max: float
    dividend_yield_percentile_min: float
    history_years: int
    minimum_observations: int
    forward_eps_source_name: str
    forward_eps_source_type: ForwardEPSSourceType
    technical_component: str
    available_at: str
    created_by: str
    rationale: str
    scope_value: str | None = None
    forward_eps_growth_required: bool = True
    forward_eps_growth_convention: ForwardEPSGrowthConvention = (
        ForwardEPSGrowthConvention.CONSECUTIVE_FISCAL_YEAR_BASE
    )
    revision_of: str | None = None
    status: ScreeningRecordStatus = ScreeningRecordStatus.AVAILABLE

    def canonical_payload(self) -> dict[str, Any]:
        if not self.logical_profile_id.strip():
            raise ValueError("logical_profile_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.revision_of is not None:
            raise ValueError("first revision cannot specify revision_of")
        if self.revision_number > 1 and not self.revision_of:
            raise ValueError("revision_of is required after revision 1")
        if self.scope is ScreeningProfileScope.GLOBAL:
            if self.scope_value is not None:
                raise ValueError("global profile cannot specify scope_value")
            scope_value = None
        else:
            if self.scope_value is None or not self.scope_value.strip():
                raise ValueError("symbol and industry profiles require scope_value")
            scope_value = self.scope_value.strip()
            if self.scope is ScreeningProfileScope.SYMBOL:
                scope_value = scope_value.upper()
        for field_name, value in (
            ("pe_percentile_max", self.pe_percentile_max),
            ("pb_percentile_max", self.pb_percentile_max),
            ("dividend_yield_percentile_min", self.dividend_yield_percentile_min),
        ):
            number = float(value)
            if not math.isfinite(number) or not 0 <= number <= 100:
                raise ValueError(f"{field_name} must be between 0 and 100")
        if self.history_years < 1:
            raise ValueError("history_years must be at least 1")
        if self.minimum_observations < 20:
            raise ValueError(
                "minimum_observations must be at least 20 for the Phase 6 research safety floor"
            )
        if not self.forward_eps_growth_required:
            raise ValueError("Phase 6 requires the Forward EPS growth gate")
        required_strings = {
            "valuation_source_name": self.valuation_source_name,
            "valuation_source_dataset": self.valuation_source_dataset,
            "forward_eps_source_name": self.forward_eps_source_name,
            "technical_component": self.technical_component,
            "created_by": self.created_by,
            "rationale": self.rationale,
        }
        for field_name, value in required_strings.items():
            if not value.strip():
                raise ValueError(f"{field_name} is required")
        return {
            "logical_profile_id": self.logical_profile_id.strip(),
            "revision_number": self.revision_number,
            "revision_of": self.revision_of,
            "scope": self.scope.value,
            "scope_value": scope_value,
            "valuation_basis": self.valuation_basis.value,
            "valuation_source_name": self.valuation_source_name.strip(),
            "valuation_source_dataset": self.valuation_source_dataset.strip(),
            "pe_percentile_max": float(self.pe_percentile_max),
            "pb_percentile_max": float(self.pb_percentile_max),
            "dividend_yield_percentile_min": float(
                self.dividend_yield_percentile_min
            ),
            "history_years": self.history_years,
            "minimum_observations": self.minimum_observations,
            "forward_eps_growth_required": self.forward_eps_growth_required,
            "forward_eps_source_name": self.forward_eps_source_name.strip(),
            "forward_eps_source_type": self.forward_eps_source_type.value,
            "forward_eps_growth_convention": self.forward_eps_growth_convention.value,
            "technical_component": self.technical_component.strip(),
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "created_by": self.created_by.strip(),
            "rationale": self.rationale.strip(),
            "status": self.status.value,
        }


@dataclass(frozen=True)
class ScreeningProfileApproval:
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
        if self.decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REVOKED}:
            raise ValueError("approval decision must be approved or revoked")
        if self.rule_id != "SEL-01":
            raise ValueError("screening profile approval must use SEL-01")
        if self.evidence_level != "C":
            raise ValueError("SEL-01 implementation requires evidence level C")
        if self.implementation_mode != "project_operationalization":
            raise ValueError("SEL-01 must remain a project operationalization")
        if not self.project_operationalization:
            raise ValueError("SEL-01 approval requires project_operationalization")
        required = {
            "approval_id": self.approval_id,
            "profile_revision_id": self.profile_revision_id,
            "rule_version": self.rule_version,
            "approved_by": self.approved_by,
            "rationale": self.rationale,
        }
        for field_name, value in required.items():
            if not value.strip():
                raise ValueError(f"{field_name} is required")
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
