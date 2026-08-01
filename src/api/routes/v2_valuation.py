"""Parallel v2 API for manually ingested Forward EPS and PE scenarios."""

from __future__ import annotations

import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.analysis_service import normalize_symbol
from src.domain.valuation import (
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    PEScenario,
    PEScope,
    RecordStatus,
    normalize_utc_timestamp,
    utc_now_timestamp,
)
from src.services.forward_eps_service import ForwardEPSService
from src.services.rule_registry import RuleRegistry


router = APIRouter(prefix="/api/v2", tags=["evidence-model-v2"])


class ForwardEPSRequest(BaseModel):
    logical_series_id: str
    revision_number: int
    revision_of: str | None = None
    symbol: str
    fiscal_year: int
    eps_low: float | None = None
    eps_base: float
    eps_high: float | None = None
    source_name: str
    source_type: ForwardEPSSourceType
    published_at: str
    available_at: str
    analyst_count: int | None = None
    quality_note: str | None = None
    status: RecordStatus = RecordStatus.ACTIVE


class PEScenarioRequest(BaseModel):
    logical_series_id: str
    revision_number: int
    revision_of: str | None = None
    label: str
    pe_value: float
    rationale: str
    evidence_level: str
    scope: PEScope
    symbol: str | None = None
    industry: str | None = None
    market: str | None = None
    available_at: str
    approval_status: ApprovalStatus
    approved_by: str | None = None
    approved_at: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    version: str = "2.0.0"


def _service() -> ForwardEPSService:
    return ForwardEPSService(os.getenv("DATABASE_PATH", "data/cache.db"))


def resolve_knowledge_cutoff(
    knowledge_cutoff_at: str | None,
    as_of_date: str | None,
) -> tuple[str, dict]:
    if knowledge_cutoff_at and as_of_date:
        raise ValueError("knowledge_cutoff_at and as_of_date are mutually exclusive")
    if knowledge_cutoff_at:
        return normalize_utc_timestamp(
            knowledge_cutoff_at, "knowledge_cutoff_at"
        ), {
            "mode": "explicit_timestamp",
            "input": knowledge_cutoff_at,
            "timezone": "input_offset_normalized_to_UTC",
        }
    if as_of_date:
        try:
            local_date = date.fromisoformat(as_of_date)
        except ValueError as exc:
            raise ValueError("as_of_date must be an ISO-8601 date") from exc
        local_start = datetime.combine(
            local_date, time.min, tzinfo=ZoneInfo("Asia/Taipei")
        )
        cutoff = normalize_utc_timestamp(local_start.isoformat(), "knowledge_cutoff_at")
        return cutoff, {
            "mode": "date_start_of_day",
            "input": as_of_date,
            "timezone": "Asia/Taipei",
            "policy": "00:00:00 Asia/Taipei; same-day publications are excluded",
        }
    cutoff = utc_now_timestamp()
    return cutoff, {
        "mode": "request_received_at",
        "input": None,
        "timezone": "UTC",
        "policy": "server request time",
    }


@router.post("/forward-eps")
def create_forward_eps(
    payload: ForwardEPSRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        observation = ForwardEPSObservation(
            logical_series_id=payload.logical_series_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            symbol=normalize_symbol(payload.symbol),
            fiscal_year=payload.fiscal_year,
            eps_low=payload.eps_low,
            eps_base=payload.eps_base,
            eps_high=payload.eps_high,
            source_name=payload.source_name,
            source_type=payload.source_type,
            published_at=payload.published_at,
            available_at=payload.available_at,
            analyst_count=payload.analyst_count,
            quality_note=payload.quality_note,
            status=payload.status,
        )
        result = _service().ingest_forward_eps(observation, idempotency_key)
        return {"status": "available", "observation": result}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pe-scenarios")
def create_pe_scenario(
    payload: PEScenarioRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        scenario = PEScenario(
            logical_series_id=payload.logical_series_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            label=payload.label,
            pe_value=payload.pe_value,
            rationale=payload.rationale,
            evidence_level=payload.evidence_level,
            scope=payload.scope,
            symbol=normalize_symbol(payload.symbol) if payload.symbol else None,
            industry=payload.industry,
            market=payload.market,
            available_at=payload.available_at,
            approval_status=payload.approval_status,
            approved_by=payload.approved_by,
            approved_at=payload.approved_at,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            version=payload.version,
        )
        result = _service().ingest_pe_scenario(scenario, idempotency_key)
        return {"status": "available", "pe_scenario": result}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analysis/{symbol}")
def get_v2_analysis(
    symbol: str,
    knowledge_cutoff_at: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    market: str | None = Query(default=None),
):
    try:
        cutoff, cutoff_policy = resolve_knowledge_cutoff(
            knowledge_cutoff_at, as_of_date
        )
        normalized_symbol = normalize_symbol(symbol)
        valuation = _service().analyze(
            normalized_symbol, cutoff, industry=industry, market=market
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    valuation_available = valuation["status"] in {"available", "not_applicable"}
    missing = [] if valuation_available else ["forward_valuation"]
    needs_human = ["approved_symbol_pe"] if valuation["status"] == "needs_human_input" else []
    return {
        "status": "partial",
        "symbol": normalized_symbol,
        "knowledge_cutoff_at": cutoff,
        "cutoff_policy": cutoff_policy,
        "model": {
            "name": "Du-public-methods evidence-based assistant",
            "version": "2.0.0",
            "official_affiliation": False,
        },
        "data_quality": {
            "status": "partial",
            "available_sections": ["valuation"] if valuation_available else [],
            "missing_sections": missing,
            "stale_sections": [],
            "needs_human_input": needs_human,
        },
        "valuation": valuation,
        "liquidity": {"status": "unsupported", "reason": "phase_3_not_implemented"},
        "technical_support": {"status": "unsupported", "reason": "phase_4_not_implemented"},
        "wave_scenarios": {"status": "needs_human_input", "reason": "phase_4_not_implemented"},
        "fibonacci_scenarios": {"status": "unsupported", "reason": "phase_4_not_implemented"},
        "target_confluence": {"status": "unsupported", "reason": "phase_7_not_implemented"},
        "deployment_plan": {"status": "unsupported", "reason": "phase_5_not_implemented"},
        "invalidation": valuation["invalidation_conditions"],
        "rules_used": valuation["rules_used"],
        "unsupported": ["eva_formula", "margin_return_8_percent_formula"],
        "snapshot_id": None,
    }


@router.get("/model-rules")
def list_v2_model_rules():
    return {
        "model_version": "2.0.0",
        "official_affiliation": False,
        "rules": [rule.to_dict() for rule in RuleRegistry().list_rules()],
    }
