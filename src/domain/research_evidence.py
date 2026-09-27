"""Research work products, never observations, approvals or trading signals."""
from __future__ import annotations

from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

GUIDANCE_CONTRACT = "research_guidance_v1"


class EvidencePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    role: Literal["origin", "swing_end", "projection_origin"]
    price: float = Field(gt=0, strict=True)
    market_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["candidate", "lookup", "brief"]
    topic: Literal["eps", "pe", "anchor", "general"]
    fiscal_year: int | None = Field(default=None, ge=1900, le=2200, strict=True)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    interpretation: str = Field(default="", max_length=1500)
    source_url: str = Field(default="", max_length=1500)
    publisher: str = Field(default="", max_length=200)
    published_date: date | None = None
    locator: str = Field(default="", max_length=300)
    reading_scope: str = Field(default="", max_length=500)
    source_access: Literal["read", "blocked", "unread"] = "unread"
    unresolved_conflict: bool = False
    source_type: Literal["original", "attributed_secondary", "lead", "user_assumption"] = "lead"
    review_status: Literal["reviewable", "limited", "lead", "not_applicable"] = "lead"
    limitations: str = Field(default="", max_length=1000)
    recheck_when: str = Field(default="", max_length=500)
    basis: str = Field(default="unknown", min_length=1, max_length=500)
    value: float | None = Field(default=None, strict=True)
    unit: Literal["TWD_per_share", "multiple", "TWD", "not_applicable"] = "not_applicable"
    anchors: list[EvidencePoint] = Field(default_factory=list, max_length=3)
    rule_id: Literal["FB-03", "FB-04"] | None = None
    lookup_scope: str = Field(default="", max_length=500)
    lookup_outcome: Literal["found", "no_qualified_source", "unavailable"] | None = None
    note_draft: str = Field(default="", max_length=4000)
    base_review_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    previous_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_content(self):
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("evidence_text_required")
        if self.source_url:
            parsed = urlsplit(self.source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("evidence_source_url_invalid")
        if self.kind == "lookup" and (not self.lookup_scope.strip() or not self.lookup_outcome):
            raise ValueError("lookup_scope_and_outcome_required")
        if self.kind == "candidate" and self.review_status in {"reviewable", "limited"}:
            if self.source_access != "read" or self.unresolved_conflict:
                raise ValueError("unread_or_conflicting_candidate_not_selectable")
            if self.topic == "general" or not all((self.publisher.strip(), self.source_url,
                    self.published_date, self.locator.strip(), self.reading_scope.strip(),
                    self.limitations.strip(), self.recheck_when.strip())):
                raise ValueError("candidate_evidence_incomplete")
            if self.source_type not in {"original", "attributed_secondary", "user_assumption"}:
                raise ValueError("unread_source_not_selectable")
            if self.topic in {"eps", "pe"} and (self.fiscal_year is None or self.value is None):
                raise ValueError("candidate_year_and_value_required")
            if self.topic == "eps" and self.unit != "TWD_per_share":
                raise ValueError("candidate_eps_unit_invalid")
            if self.topic == "pe" and (self.unit != "multiple" or self.value <= 0):
                raise ValueError("candidate_pe_invalid")
            if self.basis.strip() in {"", "unknown"}:
                raise ValueError("candidate_basis_required")
            if self.topic == "anchor" and (not self.rule_id or self.unit != "TWD" or not self.anchors):
                raise ValueError("candidate_anchors_required")
        return self
