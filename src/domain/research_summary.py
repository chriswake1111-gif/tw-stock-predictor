"""Domain contracts for Phase 20 research summary and human decision queue."""

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ScreeningMetricSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "unavailable"
    value: Optional[float] = None
    label: str
    ui_copy: str = "尚無可用資料"


class ScreeningContextSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pe: ScreeningMetricSummary = Field(
        default_factory=lambda: ScreeningMetricSummary(label="本益比 (PE)")
    )
    pb: ScreeningMetricSummary = Field(
        default_factory=lambda: ScreeningMetricSummary(label="股價淨值比 (PB)")
    )
    dividend_yield: ScreeningMetricSummary = Field(
        default_factory=lambda: ScreeningMetricSummary(label="殖利率")
    )


class HumanDecisionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: str
    title: str
    rule_id: str
    evidence_level: str
    description: str
    suggested_action: str
    status: str = "pending"


class MarketContextSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    settled_trade_date: Optional[str] = None
    official_close: Optional[float] = None
    close_status: str = "insufficient_data"
    close_reason: Optional[str] = None
    currency: str = "TWD"
    unit: str = "TWD_per_share"
    is_market_closed: bool = False
    market_status_label: str = "尚未有結算行情"
    market_turnover_total: Optional[float] = None
    market_turnover_status: str = "insufficient_data"
    cbc_m1b_ratio: Optional[float] = None
    cbc_status: str = "insufficient_data"


class ValuationContextSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "needs_human_judgment"
    reason_code: Optional[str] = "forward_eps_missing_at_knowledge_cutoff"
    target_matrix: List[Any] = Field(default_factory=list)


class TechnicalContextSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "needs_human_judgment"
    reason_code: Optional[str] = "manual_anchor_required"
    targets: Optional[dict[str, Any]] = None


class AuditReferenceSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_snapshot_id: Optional[str] = None
    available_at: Optional[str] = None
    ingested_at: Optional[str] = None
    model_version: str = "2.0.0"
    rule_traces: List[str] = Field(
        default_factory=lambda: ["VAL-01", "VAL-02", "FB-03", "FB-04", "ENT-02", "SEL-01"]
    )


class ResearchSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canonical_symbol: str
    official_code: str
    venue: str
    company_name: Optional[str] = None
    short_name: Optional[str] = None
    market_context: MarketContextSummary
    valuation_context: ValuationContextSummary
    technical_context: TechnicalContextSummary
    screening_context: ScreeningContextSummary
    human_decision_queue: List[HumanDecisionItem]
    audit_reference: AuditReferenceSummary
    knowledge_cutoff_at: str


__all__ = [
    "ScreeningMetricSummary",
    "ScreeningContextSummary",
    "HumanDecisionItem",
    "MarketContextSummary",
    "ValuationContextSummary",
    "TechnicalContextSummary",
    "AuditReferenceSummary",
    "ResearchSummaryResponse",
]
