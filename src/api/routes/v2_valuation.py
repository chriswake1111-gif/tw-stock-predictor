"""Parallel v2 API for manually ingested Forward EPS and PE scenarios."""

from __future__ import annotations

import os
import hmac
import sqlite3
import yaml
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from src.analysis_service import normalize_symbol
from src.domain.valuation import (
    ApprovalStatus,
    ApprovalResourceType,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    PEScenario,
    PEScope,
    RecordStatus,
    normalize_utc_timestamp,
    utc_now_timestamp,
)
from src.domain.technical_anchor import (
    AnchorPoint,
    AnchorRevisionStatus,
    AnchorRole,
    AnchorType,
    ManualAnchorSetRevision,
)
from src.services.forward_eps_service import ForwardEPSService
from src.services.market_liquidity_service import MarketLiquidityService
from src.services.rule_registry import RuleRegistry
from src.services.technical_scenario_service import TechnicalScenarioService


router = APIRouter(prefix="/api/v2", tags=["evidence-model-v2"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForwardEPSRequest(StrictRequest):
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


class PEScenarioRequest(StrictRequest):
    logical_series_id: str
    revision_number: int
    revision_of: str | None = None
    label: str
    pe_value: float
    rationale: str
    scope: PEScope
    symbol: str | None = None
    industry: str | None = None
    market: str | None = None
    available_at: str
    effective_from: str | None = None
    effective_to: str | None = None
    evidence_basis_rule_id: str | None = None
    version: str = "2.0.0"


class ApprovalRequest(StrictRequest):
    decision: ApprovalStatus
    rule_id: str
    rationale: str
    available_at: str


class AnchorPointRequest(StrictRequest):
    role: AnchorRole
    price: float
    market_date: str
    anchor_type: AnchorType = AnchorType.MANUAL_PRICE_ANCHOR


class ManualAnchorRequest(StrictRequest):
    logical_anchor_set_id: str
    revision_number: int
    revision_of: str | None = None
    symbol: str
    evidence_basis_rule_id: str
    anchors: list[AnchorPointRequest]
    available_at: str
    source: str
    source_note: str | None = None
    status: AnchorRevisionStatus = AnchorRevisionStatus.AVAILABLE


class TechnicalApprovalRequest(StrictRequest):
    decision: ApprovalStatus
    rule_id: str
    rationale: str
    approved_at: str


def _service() -> ForwardEPSService:
    return ForwardEPSService(os.getenv("DATABASE_PATH", "data/cache.db"))


def _liquidity_service() -> MarketLiquidityService:
    config_path = os.getenv("CONFIG_PATH", "config/config.yaml")
    try:
        with open(config_path, encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
    except OSError:
        config = {}
    return MarketLiquidityService(
        os.getenv("DATABASE_PATH", "data/cache.db"), config=config
    )


def _technical_service() -> TechnicalScenarioService:
    return TechnicalScenarioService(os.getenv("DATABASE_PATH", "data/cache.db"))


def _require_write_access(api_key: str | None) -> str:
    if os.getenv("EVIDENCE_V2_WRITES_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(status_code=503, detail="evidence_v2_writes_disabled")
    expected = os.getenv("EVIDENCE_V2_ADMIN_API_KEY", "")
    actor = os.getenv("EVIDENCE_V2_ADMIN_ACTOR", "")
    if not expected or not actor:
        raise HTTPException(
            status_code=503, detail="evidence_v2_admin_configuration_incomplete"
        )
    if api_key is None or not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=401, detail="invalid_admin_api_key")
    return actor


def _public_dto(record: dict) -> dict:
    def scrub(value):
        if isinstance(value, dict):
            return {
                key: scrub(item) for key, item in value.items()
                if key not in {"idempotency_key", "payload_fingerprint"}
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(record)


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
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        _require_write_access(admin_api_key)
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
            status=RecordStatus.ACTIVE,
        )
        result = _service().ingest_forward_eps(observation, idempotency_key)
        return {
            "status": "available",
            "approval_status": "draft",
            "observation": _public_dto(result),
        }
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pe-scenarios")
def create_pe_scenario(
    payload: PEScenarioRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        _require_write_access(admin_api_key)
        scenario = PEScenario(
            logical_series_id=payload.logical_series_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            label=payload.label,
            pe_value=payload.pe_value,
            rationale=payload.rationale,
            evidence_level="U",
            scope=payload.scope,
            symbol=normalize_symbol(payload.symbol) if payload.symbol else None,
            industry=payload.industry,
            market=payload.market,
            available_at=payload.available_at,
            approval_status=ApprovalStatus.DRAFT,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            evidence_basis_rule_id=payload.evidence_basis_rule_id,
            version=payload.version,
        )
        result = _service().ingest_pe_scenario(scenario, idempotency_key)
        return {
            "status": "available",
            "approval_status": "draft",
            "pe_scenario": _public_dto(result),
        }
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _record_approval(
    *,
    resource_type: ApprovalResourceType,
    resource_id: str,
    payload: ApprovalRequest,
    idempotency_key: str,
    admin_api_key: str | None,
):
    try:
        actor = _require_write_access(admin_api_key)
        result = _service().record_approval(
            resource_type=resource_type,
            resource_id=resource_id,
            decision=payload.decision,
            rule_id=payload.rule_id,
            rationale=payload.rationale,
            available_at=payload.available_at,
            approved_by=actor,
            idempotency_key=idempotency_key,
        )
        return {"status": "available", "approval": _public_dto(result)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/forward-eps/{observation_id}/approval")
def approve_forward_eps(
    observation_id: str,
    payload: ApprovalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    return _record_approval(
        resource_type=ApprovalResourceType.FORWARD_EPS,
        resource_id=observation_id,
        payload=payload,
        idempotency_key=idempotency_key,
        admin_api_key=admin_api_key,
    )


@router.post("/pe-scenarios/{scenario_id}/approval")
def approve_pe_scenario(
    scenario_id: str,
    payload: ApprovalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    return _record_approval(
        resource_type=ApprovalResourceType.PE_SCENARIO,
        resource_id=scenario_id,
        payload=payload,
        idempotency_key=idempotency_key,
        admin_api_key=admin_api_key,
    )


@router.post("/anchors")
def create_manual_anchor_set(
    payload: ManualAnchorRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        revision = ManualAnchorSetRevision(
            logical_anchor_set_id=payload.logical_anchor_set_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            symbol=normalize_symbol(payload.symbol),
            evidence_basis_rule_id=payload.evidence_basis_rule_id,
            anchors=tuple(AnchorPoint(
                role=point.role, price=point.price, market_date=point.market_date,
                anchor_type=point.anchor_type,
            ) for point in payload.anchors),
            available_at=payload.available_at,
            created_by=actor,
            source=payload.source,
            source_note=payload.source_note,
            status=payload.status,
        )
        result = _technical_service().ingest(revision, idempotency_key)
        return {"status": "available", "approval_status": "draft", "anchor_set": _public_dto(result)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/anchors/{anchor_revision_id}/approval")
def approve_manual_anchor_set(
    anchor_revision_id: str,
    payload: TechnicalApprovalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        result = _technical_service().record_approval(
            anchor_revision_id=anchor_revision_id,
            decision=payload.decision,
            rule_id=payload.rule_id,
            rationale=payload.rationale,
            approved_at=payload.approved_at,
            approved_by=actor,
            idempotency_key=idempotency_key,
        )
        return {"status": "available", "approval": _public_dto(result)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/anchors/{symbol}")
def list_manual_anchor_sets(
    symbol: str,
    knowledge_cutoff_at: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
):
    try:
        cutoff, cutoff_policy = resolve_knowledge_cutoff(knowledge_cutoff_at, as_of_date)
        normalized_symbol = normalize_symbol(symbol)
        states = _technical_service().repository.states_as_of(normalized_symbol, cutoff)
        return {
            "status": "available" if states else "needs_human_input",
            "symbol": normalized_symbol,
            "knowledge_cutoff_at": cutoff,
            "cutoff_policy": cutoff_policy,
            "anchor_sets": [_public_dto(state) for state in states],
        }
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
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

    try:
        liquidity = _liquidity_service().analyze(cutoff)
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        liquidity = {
            "status": "insufficient_data",
            "reason": f"liquidity_data_unavailable: {exc}",
            "rules_used": [],
        }

    try:
        technical_support = _technical_service().analyze(normalized_symbol, cutoff)
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        technical_support = {
            "status": "insufficient_data",
            "reason": f"technical_anchor_data_unavailable: {exc}",
            "scenarios": [],
            "rules_used": [],
        }

    valuation_available = valuation["status"] in {"available", "not_applicable"}
    liquidity_available = liquidity["status"] == "available"
    technical_available = technical_support["status"] == "available"
    available_sections = []
    missing = []
    if valuation_available:
        available_sections.append("valuation")
    else:
        missing.append("forward_valuation")
    if liquidity_available:
        available_sections.append("liquidity")
    else:
        missing.append("liquidity")
    if technical_available:
        available_sections.append("technical_support")
    else:
        missing.append("technical_support")
    needs_human = []
    if valuation["status"] == "needs_human_input":
        if valuation["reason"] == "approved_forward_eps_required":
            needs_human.append("approved_forward_eps")
        elif valuation["reason"] == "approved_symbol_pe_missing_at_knowledge_cutoff":
            needs_human.append("approved_symbol_pe")
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
            "available_sections": available_sections,
            "missing_sections": missing,
            "stale_sections": [],
            "needs_human_input": needs_human,
        },
        "valuation": valuation,
        "liquidity": liquidity,
        "technical_support": technical_support,
        "wave_scenarios": {"status": "needs_human_input", "reason": "phase_4_not_implemented"},
        "fibonacci_scenarios": {"status": "unsupported", "reason": "use_technical_support_phase_4"},
        "target_confluence": {"status": "unsupported", "reason": "phase_7_not_implemented"},
        "deployment_plan": {"status": "unsupported", "reason": "phase_5_not_implemented"},
        "invalidation": valuation["invalidation_conditions"],
        "rules_used": valuation["rules_used"] + liquidity.get("rules_used", []) + technical_support.get("rules_used", []),
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
