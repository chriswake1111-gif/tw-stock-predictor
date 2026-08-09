"""Evidence-governed capital-deployment domain contracts for Phase 5."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from src.domain.valuation import ApprovalStatus, normalize_utc_timestamp


class DeploymentPlanStatus(str, Enum):
    AVAILABLE = "available"
    REVOKED = "revoked"


class TriggerType(str, Enum):
    MANUAL_PRICE = "manual_price"
    USER_PERCENTAGE = "user_percentage"
    ATR_MULTIPLE = "atr_multiple"
    APPROVED_FB04_SCENARIO = "approved_fb04_scenario"


def _positive_decimal(value: Any, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return result


@dataclass(frozen=True)
class DeploymentTrigger:
    stage: int
    trigger_type: TriggerType
    value: str | float | None = None
    reference_id: str | None = None
    anchor_revision_id: str | None = None
    approval_id: str | None = None
    rule_id: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        if self.stage not in {1, 2, 3}:
            raise ValueError("deployment trigger stage must be 1, 2, or 3")
        result: dict[str, Any] = {
            "stage": self.stage,
            "trigger_type": self.trigger_type.value,
            "value": None,
            "reference_id": self.reference_id,
            "anchor_revision_id": self.anchor_revision_id,
            "approval_id": self.approval_id,
            "rule_id": self.rule_id,
        }
        if self.trigger_type == TriggerType.MANUAL_PRICE:
            if any((self.anchor_revision_id, self.approval_id, self.rule_id)):
                raise ValueError("manual trigger cannot claim evidence approval metadata")
            result["value"] = str(_positive_decimal(self.value, "manual trigger price"))
            result["source_classification"] = "manual_input"
        elif self.trigger_type == TriggerType.USER_PERCENTAGE:
            if any((self.anchor_revision_id, self.approval_id, self.rule_id)):
                raise ValueError("user percentage cannot claim an evidence rule or approval")
            percentage = _positive_decimal(self.value, "user percentage")
            if percentage >= 1:
                raise ValueError("user percentage must be expressed as a decimal below one")
            result["value"] = str(percentage)
            result["source_classification"] = "user_parameter"
        elif self.trigger_type == TriggerType.ATR_MULTIPLE:
            if any((self.anchor_revision_id, self.approval_id, self.rule_id)):
                raise ValueError("ATR trigger cannot claim an evidence rule or approval")
            result["value"] = str(_positive_decimal(self.value, "ATR multiple"))
            if not self.reference_id:
                raise ValueError("ATR trigger requires a reference_id")
            result["source_classification"] = "project_configuration"
        elif self.trigger_type == TriggerType.APPROVED_FB04_SCENARIO:
            if self.value is not None:
                raise ValueError("approved FB-04 trigger cannot supply an independent value")
            if not all((self.reference_id, self.anchor_revision_id, self.approval_id)):
                raise ValueError("approved FB-04 trigger requires scenario, anchor revision, and approval IDs")
            if self.rule_id != "FB-04":
                raise ValueError("approved Fibonacci deployment trigger must use FB-04")
            result["source_classification"] = "approved_rule_scenario"
        return result


@dataclass(frozen=True)
class DeploymentPlanRevision:
    logical_campaign_id: str
    revision_number: int
    symbol: str
    planned_total_capital: str | float
    triggers: tuple[DeploymentTrigger, ...]
    available_at: str
    created_by: str
    revision_of: str | None = None
    source_note: str | None = None
    status: DeploymentPlanStatus = DeploymentPlanStatus.AVAILABLE
    currency: str = "TWD"

    def canonical_payload(self) -> dict[str, Any]:
        if not self.logical_campaign_id.strip():
            raise ValueError("logical_campaign_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.revision_of is not None:
            raise ValueError("first revision cannot specify revision_of")
        if self.revision_number > 1 and not self.revision_of:
            raise ValueError("revision_of is required after revision 1")
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        if self.currency != "TWD":
            raise ValueError("deployment currency must be TWD")
        if not self.created_by.strip():
            raise ValueError("created_by is required")
        capital = _positive_decimal(self.planned_total_capital, "planned_total_capital")
        trigger_payloads = [trigger.canonical_payload() for trigger in self.triggers]
        stages = [trigger["stage"] for trigger in trigger_payloads]
        if stages != list(range(1, len(stages) + 1)) or len(stages) > 3:
            raise ValueError("deployment triggers must be the ordered prefix of stages 1, 2, and 3")
        return {
            "logical_campaign_id": self.logical_campaign_id,
            "revision_number": self.revision_number,
            "revision_of": self.revision_of,
            "symbol": symbol,
            "planned_total_capital": str(capital),
            "currency": self.currency,
            "triggers": trigger_payloads,
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "created_by": self.created_by,
            "source_note": self.source_note,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class DeploymentPlanApproval:
    approval_id: str
    plan_revision_id: str
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
        if self.rule_id != "ENT-02":
            raise ValueError("deployment plan approval must use ENT-02")
        if self.evidence_level != "A" or self.implementation_mode != "verified_core":
            raise ValueError("ENT-02 approval requires A-level verified_core metadata")
        if self.project_operationalization:
            raise ValueError("ENT-02 is not a project operationalization")
        if not all((self.approval_id.strip(), self.plan_revision_id.strip(), self.rule_version.strip())):
            raise ValueError("approval identity, resource, and rule version are required")
        if not self.approved_by.strip() or not self.rationale.strip():
            raise ValueError("approved_by and rationale are required")
        return {
            "approval_id": self.approval_id,
            "plan_revision_id": self.plan_revision_id,
            "decision": self.decision.value,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "evidence_level": self.evidence_level,
            "implementation_mode": self.implementation_mode,
            "project_operationalization": self.project_operationalization,
            "approved_by": self.approved_by,
            "rationale": self.rationale,
            "approved_at": normalize_utc_timestamp(self.approved_at, "approved_at"),
        }
