"""Domain contracts for manually entered, revisioned technical anchors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from src.domain.valuation import ApprovalStatus, normalize_utc_timestamp


class AnchorRole(str, Enum):
    ORIGIN = "origin"
    SWING_END = "swing_end"
    PROJECTION_ORIGIN = "projection_origin"


class AnchorType(str, Enum):
    MANUAL_PRICE_ANCHOR = "manual_price_anchor"


class AnchorRevisionStatus(str, Enum):
    AVAILABLE = "available"
    REVOKED = "revoked"


@dataclass(frozen=True)
class AnchorPoint:
    role: AnchorRole
    price: float
    market_date: str
    anchor_type: AnchorType = AnchorType.MANUAL_PRICE_ANCHOR

    def canonical_payload(self) -> dict[str, Any]:
        price = float(self.price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("anchor price must be finite and greater than zero")
        try:
            date.fromisoformat(self.market_date)
        except ValueError as exc:
            raise ValueError("market_date must be an ISO-8601 date") from exc
        return {
            "role": self.role.value,
            "anchor_type": self.anchor_type.value,
            "price": price,
            "market_date": self.market_date,
        }


@dataclass(frozen=True)
class ManualAnchorSetRevision:
    logical_anchor_set_id: str
    revision_number: int
    symbol: str
    evidence_basis_rule_id: str
    anchors: tuple[AnchorPoint, ...]
    available_at: str
    created_by: str
    source: str
    source_note: str | None = None
    revision_of: str | None = None
    status: AnchorRevisionStatus = AnchorRevisionStatus.AVAILABLE
    price_unit: str = "TWD_per_share"

    def canonical_payload(self) -> dict[str, Any]:
        if not self.logical_anchor_set_id.strip():
            raise ValueError("logical_anchor_set_id is required")
        if self.revision_number < 1:
            raise ValueError("revision_number must be at least 1")
        if self.revision_number == 1 and self.revision_of is not None:
            raise ValueError("first revision cannot specify revision_of")
        if self.revision_number > 1 and not self.revision_of:
            raise ValueError("revision_of is required after revision 1")
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        if self.evidence_basis_rule_id not in {"FB-03", "FB-04"}:
            raise ValueError("unsupported anchor evidence_basis_rule_id")
        if self.price_unit != "TWD_per_share":
            raise ValueError("price_unit must be TWD_per_share")
        if not self.created_by.strip() or not self.source.strip():
            raise ValueError("created_by and source are required")
        points = [point.canonical_payload() for point in self.anchors]
        by_role = {point["role"]: point for point in points}
        if len(by_role) != len(points):
            raise ValueError("anchor roles must be unique")
        required = {"origin", "swing_end"}
        if self.evidence_basis_rule_id == "FB-03":
            required.add("projection_origin")
        if set(by_role) != required:
            raise ValueError(
                f"{self.evidence_basis_rule_id} requires exactly {sorted(required)}"
            )
        ordered = [by_role[role]["market_date"] for role in (
            "origin", "swing_end", "projection_origin"
        ) if role in by_role]
        if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
            raise ValueError("anchor market dates must be strictly chronological")
        available_date = datetime.fromisoformat(
            normalize_utc_timestamp(self.available_at, "available_at").replace("Z", "+00:00")
        ).date()
        if date.fromisoformat(ordered[-1]) > available_date:
            raise ValueError("anchor available_at cannot precede its latest market_date")
        if (
            self.evidence_basis_rule_id == "FB-04"
            and by_role["swing_end"]["price"] <= by_role["origin"]["price"]
        ):
            raise ValueError("FB-04 requires an approved upward swing with B greater than A")
        return {
            "logical_anchor_set_id": self.logical_anchor_set_id,
            "revision_number": self.revision_number,
            "revision_of": self.revision_of,
            "symbol": symbol,
            "evidence_basis_rule_id": self.evidence_basis_rule_id,
            "anchors": points,
            "available_at": normalize_utc_timestamp(self.available_at, "available_at"),
            "created_by": self.created_by,
            "source": self.source,
            "source_note": self.source_note,
            "status": self.status.value,
            "price_unit": self.price_unit,
        }


@dataclass(frozen=True)
class TechnicalAnchorApproval:
    approval_id: str
    anchor_revision_id: str
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
        if self.rule_id not in {"FB-03", "FB-04"}:
            raise ValueError("unsupported technical approval rule_id")
        if self.evidence_level != "A" or self.implementation_mode != "verified_core":
            raise ValueError("Phase 4 approvals require A-level verified_core rules")
        if self.project_operationalization:
            raise ValueError("A-level Phase 4 rules are not project operationalizations")
        if not self.rule_version.strip():
            raise ValueError("rule_version is required")
        if not self.approval_id.strip() or not self.anchor_revision_id.strip():
            raise ValueError("approval_id and anchor_revision_id are required")
        if not self.approved_by.strip() or not self.rationale.strip():
            raise ValueError("approved_by and approval rationale are required")
        return {
            "approval_id": self.approval_id,
            "anchor_revision_id": self.anchor_revision_id,
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
