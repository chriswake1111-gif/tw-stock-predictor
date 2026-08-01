"""Domain contracts for evidence-based Forward EPS and PE valuation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


class ForwardEPSSourceType(str, Enum):
    BROKER_REPORT = "broker_report"
    COMPANY_GUIDANCE = "company_guidance"
    CONSENSUS_API = "consensus_api"
    MANUAL = "manual"


class RecordStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class PEScope(str, Enum):
    SYMBOL = "symbol"
    INDUSTRY = "industry"
    MARKET = "market"


class ApprovalStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REVOKED = "revoked"


class ApprovalResourceType(str, Enum):
    FORWARD_EPS = "forward_eps"
    PE_SCENARIO = "pe_scenario"


def normalize_utc_timestamp(value: str, field_name: str) -> str:
    """Validate an aware ISO-8601 timestamp and normalize it to UTC."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_aware_timestamp(value: str, field_name: str) -> datetime:
    normalized = normalize_utc_timestamp(value, field_name)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def utc_now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _finite_optional(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


@dataclass(frozen=True)
class ForwardEPSObservation:
    logical_series_id: str
    revision_number: int
    symbol: str
    fiscal_year: int
    eps_base: float
    source_name: str
    source_type: ForwardEPSSourceType
    published_at: str
    available_at: str
    eps_low: float | None = None
    eps_high: float | None = None
    analyst_count: int | None = None
    quality_note: str | None = None
    revision_of: str | None = None
    status: RecordStatus = RecordStatus.ACTIVE
    unit: str = "TWD_per_share"

    def validated(self) -> "ForwardEPSObservation":
        if not self.logical_series_id.strip():
            raise ValueError("logical_series_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.revision_of is not None:
            raise ValueError("first revision cannot specify revision_of")
        if self.revision_number > 1 and not self.revision_of:
            raise ValueError("revision_of is required after revision 1")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if not self.source_name.strip():
            raise ValueError("source_name is required")
        if self.fiscal_year < 1900:
            raise ValueError("fiscal_year is invalid")
        try:
            published_date = date.fromisoformat(self.published_at)
        except ValueError as exc:
            raise ValueError("published_at must be an ISO-8601 date") from exc
        normalize_utc_timestamp(self.available_at, "available_at")
        available_candidate = self.available_at.replace("Z", "+00:00")
        available_date = datetime.fromisoformat(available_candidate).date()
        if published_date > available_date:
            raise ValueError("published_at cannot be later than available_at date")
        low = _finite_optional(self.eps_low, "eps_low")
        base = _finite_optional(self.eps_base, "eps_base")
        high = _finite_optional(self.eps_high, "eps_high")
        if low is not None and low > base:
            raise ValueError("eps_low must be less than or equal to eps_base")
        if high is not None and base > high:
            raise ValueError("eps_base must be less than or equal to eps_high")
        if self.analyst_count is not None and self.analyst_count < 1:
            raise ValueError("analyst_count must be positive when supplied")
        normalize_utc_timestamp(self.available_at, "available_at")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        self.validated()
        return {
            "logical_series_id": self.logical_series_id,
            "revision_number": self.revision_number,
            "revision_of": self.revision_of,
            "symbol": self.symbol.strip().upper(),
            "fiscal_year": self.fiscal_year,
            "eps_low": self.eps_low,
            "eps_base": self.eps_base,
            "eps_high": self.eps_high,
            "source_name": self.source_name,
            "source_type": self.source_type.value,
            "published_at": self.published_at,
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "analyst_count": self.analyst_count,
            "quality_note": self.quality_note,
            "status": self.status.value,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class PEScenario:
    logical_series_id: str
    revision_number: int
    label: str
    pe_value: float
    rationale: str
    evidence_level: str
    scope: PEScope
    available_at: str
    approval_status: ApprovalStatus
    symbol: str | None = None
    industry: str | None = None
    market: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    revision_of: str | None = None
    version: str = "2.0.0"

    def validated(self) -> "PEScenario":
        if not self.logical_series_id.strip():
            raise ValueError("logical_series_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.revision_of is not None:
            raise ValueError("first revision cannot specify revision_of")
        if self.revision_number > 1 and not self.revision_of:
            raise ValueError("revision_of is required after revision 1")
        pe_value = float(self.pe_value)
        if not math.isfinite(pe_value) or pe_value <= 0:
            raise ValueError("pe_value must be finite and greater than zero")
        if self.evidence_level not in {"A", "B", "C", "U"}:
            raise ValueError("evidence_level must be A, B, C, or U")
        scope_values = {
            PEScope.SYMBOL: self.symbol,
            PEScope.INDUSTRY: self.industry,
            PEScope.MARKET: self.market,
        }
        if not scope_values[self.scope] or sum(bool(value) for value in scope_values.values()) != 1:
            raise ValueError("exactly one value matching the PE scope is required")
        normalize_utc_timestamp(self.available_at, "available_at")
        if self.approval_status is ApprovalStatus.APPROVED:
            if not self.label.strip():
                raise ValueError("approved PE scenarios require a non-blank label")
            if not self.rationale.strip():
                raise ValueError("approved PE scenarios require a non-blank rationale")
            if not self.approved_by or not self.approved_at:
                raise ValueError("approved PE scenarios require approved_by and approved_at")
            normalize_utc_timestamp(self.approved_at, "approved_at")
        elif self.approved_at is not None:
            normalize_utc_timestamp(self.approved_at, "approved_at")
        if self.effective_from and self.effective_to:
            effective_from = parse_aware_timestamp(
                self.effective_from, "effective_from"
            )
            effective_to = parse_aware_timestamp(self.effective_to, "effective_to")
            if effective_from >= effective_to:
                raise ValueError("effective_from must be earlier than effective_to")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        self.validated()
        return {
            "logical_series_id": self.logical_series_id,
            "revision_number": self.revision_number,
            "revision_of": self.revision_of,
            "label": self.label,
            "pe_value": float(self.pe_value),
            "rationale": self.rationale,
            "evidence_level": self.evidence_level,
            "scope": self.scope.value,
            "symbol": self.symbol.strip().upper() if self.symbol else None,
            "industry": self.industry,
            "market": self.market,
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "approval_status": self.approval_status.value,
            "approved_by": self.approved_by,
            "approved_at": normalize_utc_timestamp(self.approved_at, "approved_at") if self.approved_at else None,
            "effective_from": normalize_utc_timestamp(self.effective_from, "effective_from") if self.effective_from else None,
            "effective_to": normalize_utc_timestamp(self.effective_to, "effective_to") if self.effective_to else None,
            "version": self.version,
        }


@dataclass(frozen=True)
class ValuationApproval:
    approval_id: str
    resource_type: ApprovalResourceType
    resource_id: str
    decision: ApprovalStatus
    rule_id: str
    evidence_level: str
    project_operationalization: bool
    approved_by: str
    rationale: str
    available_at: str

    def canonical_payload(self) -> dict[str, Any]:
        if self.decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REVOKED}:
            raise ValueError("approval decision must be approved or revoked")
        if not self.approval_id.strip():
            raise ValueError("approval_id is required")
        if not self.resource_id.strip():
            raise ValueError("resource_id is required")
        if not self.approved_by.strip():
            raise ValueError("approved_by is required")
        if not self.rationale.strip():
            raise ValueError("approval rationale cannot be blank")
        if self.evidence_level not in {"A", "B", "C", "U"}:
            raise ValueError("evidence_level must be A, B, C, or U")
        if self.evidence_level == "C" and not self.project_operationalization:
            raise ValueError("C-level approvals require project_operationalization")
        return {
            "approval_id": self.approval_id,
            "resource_type": self.resource_type.value,
            "resource_id": self.resource_id,
            "decision": self.decision.value,
            "rule_id": self.rule_id,
            "evidence_level": self.evidence_level,
            "project_operationalization": self.project_operationalization,
            "approved_by": self.approved_by,
            "rationale": self.rationale,
            "available_at": normalize_utc_timestamp(
                self.available_at, "available_at"
            ),
        }
