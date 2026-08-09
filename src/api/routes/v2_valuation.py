"""Parallel v2 API for manually ingested Forward EPS and PE scenarios."""

from __future__ import annotations

import os
import hmac
import sqlite3
import yaml
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

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
from src.domain.deployment import (
    DeploymentPlanRevision,
    DeploymentPlanStatus,
    DeploymentTrigger,
    TriggerType,
)
from src.domain.screening import (
    ScreeningProfileRevision,
    ScreeningProfileScope,
    ScreeningRecordStatus,
    SecurityValuationObservation,
    ValuationBasis,
)
from src.domain.analysis_snapshot import (
    CaptureMode,
    SynthesisProfileRevision,
    SynthesisProfileScope,
    SynthesisRecordStatus,
)
from src.services.deployment_plan_service import DeploymentPlanService
from src.services.forward_eps_service import ForwardEPSService
from src.services.market_liquidity_service import MarketLiquidityService
from src.services.rule_registry import RuleRegistry
from src.services.security_screening_service import SecurityScreeningService
from src.services.technical_scenario_service import TechnicalScenarioService
from src.services.evidence_analysis_service import EvidenceAnalysisService
from src.services.performance_validation_service import PerformanceValidationService


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


class DeploymentTriggerRequest(StrictRequest):
    stage: int
    trigger_type: TriggerType
    value: str | float | None = None
    reference_id: str | None = None
    anchor_revision_id: str | None = None
    approval_id: str | None = None
    rule_id: str | None = None


class DeploymentPlanRequest(StrictRequest):
    logical_campaign_id: str
    revision_number: int
    revision_of: str | None = None
    symbol: str
    planned_total_capital: str | float
    triggers: list[DeploymentTriggerRequest] = Field(default_factory=list)
    available_at: str
    source_note: str | None = None
    status: DeploymentPlanStatus = DeploymentPlanStatus.AVAILABLE


class DeploymentApprovalRequest(StrictRequest):
    decision: ApprovalStatus
    rationale: str
    approved_at: str


class SecurityValuationRequest(StrictRequest):
    logical_observation_id: str
    revision_number: int
    revision_of: str | None = None
    symbol: str
    metric_date: str
    pe: float | None = None
    pb: float | None = None
    dividend_yield_ratio: float | None = None
    source_name: str
    source_dataset: str
    available_at: str
    status: ScreeningRecordStatus = ScreeningRecordStatus.AVAILABLE


class ScreeningProfileRequest(StrictRequest):
    logical_profile_id: str
    revision_number: int
    revision_of: str | None = None
    scope: ScreeningProfileScope
    scope_value: str | None = None
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
    rationale: str
    status: ScreeningRecordStatus = ScreeningRecordStatus.AVAILABLE


class ScreeningProfileApprovalRequest(StrictRequest):
    decision: ApprovalStatus
    rationale: str
    approved_at: str


class EvidenceStrengthThresholdRequest(StrictRequest):
    minimum_independent_target_components: int
    label: str


class SynthesisProfileRequest(StrictRequest):
    logical_profile_id: str
    revision_number: int
    revision_of: str | None = None
    scope: SynthesisProfileScope
    scope_value: str | None = None
    allowed_method_families: list[str]
    overlap_tolerance: str
    evidence_strength_policy: list[EvidenceStrengthThresholdRequest]
    calculation_quantum: str
    display_quantum: str
    available_at: str
    rationale: str
    status: SynthesisRecordStatus = SynthesisRecordStatus.AVAILABLE


class SynthesisProfileApprovalRequest(StrictRequest):
    decision: ApprovalStatus
    rationale: str
    approved_at: str


class AnalysisRefreshRequest(StrictRequest):
    logical_synthesis_profile_id: str | None = None
    synthesis_profile_revision_id: str | None = None
    supersedes_snapshot_id: str | None = None


class EvaluationRunRequest(StrictRequest):
    snapshot_ids: list[str] = Field(min_length=1)
    evaluation_profile_acknowledgement: str


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


def _deployment_service() -> DeploymentPlanService:
    return DeploymentPlanService(os.getenv("DATABASE_PATH", "data/cache.db"))


def _screening_service() -> SecurityScreeningService:
    return SecurityScreeningService(os.getenv("DATABASE_PATH", "data/cache.db"))


def _evidence_analysis_service() -> EvidenceAnalysisService:
    return EvidenceAnalysisService(os.getenv("DATABASE_PATH", "data/cache.db"))


def _performance_validation_service() -> PerformanceValidationService:
    return PerformanceValidationService(os.getenv("DATABASE_PATH", "data/cache.db"))


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


@router.post("/deployment-plan")
def create_deployment_plan(
    payload: DeploymentPlanRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        revision = DeploymentPlanRevision(
            logical_campaign_id=payload.logical_campaign_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            symbol=normalize_symbol(payload.symbol),
            planned_total_capital=payload.planned_total_capital,
            triggers=tuple(DeploymentTrigger(
                stage=item.stage,
                trigger_type=item.trigger_type,
                value=item.value,
                reference_id=item.reference_id,
                anchor_revision_id=item.anchor_revision_id,
                approval_id=item.approval_id,
                rule_id=item.rule_id,
            ) for item in payload.triggers),
            available_at=payload.available_at,
            created_by=actor,
            source_note=payload.source_note,
            status=payload.status,
        )
        record = _deployment_service().ingest(revision, idempotency_key)
        plan = _deployment_service()._plan_for_state({**record, "approval": None})
        return {"status": plan["status"], "reason": plan["reason"], "deployment_plan": _public_dto(plan)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/deployment-plan/{plan_revision_id}/approval")
def approve_deployment_plan(
    plan_revision_id: str,
    payload: DeploymentApprovalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        result = _deployment_service().record_approval(
            plan_revision_id=plan_revision_id,
            decision=payload.decision,
            rationale=payload.rationale,
            approved_at=payload.approved_at,
            approved_by=actor,
            idempotency_key=idempotency_key,
        )
        return {"status": "available", "approval": _public_dto(result)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/security-valuations")
def create_security_valuation(
    payload: SecurityValuationRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        _require_write_access(admin_api_key)
        observation = SecurityValuationObservation(
            logical_observation_id=payload.logical_observation_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            symbol=normalize_symbol(payload.symbol),
            metric_date=payload.metric_date,
            pe=payload.pe,
            pb=payload.pb,
            dividend_yield_ratio=payload.dividend_yield_ratio,
            source_name=payload.source_name,
            source_dataset=payload.source_dataset,
            available_at=payload.available_at,
            status=payload.status,
        )
        result = _screening_service().ingest_valuation(
            observation, idempotency_key
        )
        return {"status": "available", "valuation_observation": _public_dto(result)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/screening-profiles")
def create_screening_profile(
    payload: ScreeningProfileRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        revision = ScreeningProfileRevision(
            logical_profile_id=payload.logical_profile_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            scope=payload.scope,
            scope_value=payload.scope_value,
            valuation_basis=payload.valuation_basis,
            valuation_source_name=payload.valuation_source_name,
            valuation_source_dataset=payload.valuation_source_dataset,
            pe_percentile_max=payload.pe_percentile_max,
            pb_percentile_max=payload.pb_percentile_max,
            dividend_yield_percentile_min=payload.dividend_yield_percentile_min,
            history_years=payload.history_years,
            minimum_observations=payload.minimum_observations,
            forward_eps_source_name=payload.forward_eps_source_name,
            forward_eps_source_type=payload.forward_eps_source_type,
            technical_component=payload.technical_component,
            available_at=payload.available_at,
            created_by=actor,
            rationale=payload.rationale,
            status=payload.status,
        )
        result = _screening_service().ingest_profile(revision, idempotency_key)
        return {
            "status": "available",
            "approval_status": "draft",
            "screening_profile": _public_dto(result),
        }
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/screening-profiles/{profile_revision_id}/approval")
def approve_screening_profile(
    profile_revision_id: str,
    payload: ScreeningProfileApprovalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        result = _screening_service().record_profile_approval(
            profile_revision_id=profile_revision_id,
            decision=payload.decision,
            rationale=payload.rationale,
            approved_at=payload.approved_at,
            approved_by=actor,
            idempotency_key=idempotency_key,
        )
        return {"status": "available", "approval": _public_dto(result)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/synthesis-profiles")
def create_synthesis_profile(
    payload: SynthesisProfileRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        revision = SynthesisProfileRevision(
            logical_profile_id=payload.logical_profile_id,
            revision_number=payload.revision_number,
            revision_of=payload.revision_of,
            scope=payload.scope,
            scope_value=payload.scope_value,
            allowed_method_families=tuple(payload.allowed_method_families),
            overlap_tolerance=payload.overlap_tolerance,
            evidence_strength_policy=tuple(
                threshold.model_dump() for threshold in payload.evidence_strength_policy
            ),
            calculation_quantum=payload.calculation_quantum,
            display_quantum=payload.display_quantum,
            available_at=payload.available_at,
            created_by=actor,
            rationale=payload.rationale,
            status=payload.status,
        )
        result = _evidence_analysis_service().ingest_profile(
            revision, idempotency_key
        )
        return {
            "status": "available",
            "approval_status": "draft",
            "synthesis_profile": _public_dto(result),
        }
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/synthesis-profiles/{profile_revision_id}/approval")
def approve_synthesis_profile(
    profile_revision_id: str,
    payload: SynthesisProfileApprovalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        result = _evidence_analysis_service().record_profile_approval(
            profile_revision_id=profile_revision_id,
            decision=payload.decision,
            rationale=payload.rationale,
            approved_at=payload.approved_at,
            approved_by=actor,
            idempotency_key=idempotency_key,
        )
        return {"status": "available", "approval": _public_dto(result)}
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/synthesis-profiles/{logical_profile_id}")
def get_synthesis_profiles(
    logical_profile_id: str,
    knowledge_cutoff_at: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
):
    try:
        cutoff, cutoff_policy = resolve_knowledge_cutoff(
            knowledge_cutoff_at, as_of_date
        )
        profiles = [
            profile
            for profile in _evidence_analysis_service().profile_repository.effective_states_as_of(cutoff)
            if profile["logical_profile_id"] == logical_profile_id
        ]
        return {
            "status": "available" if profiles else "insufficient_data",
            "knowledge_cutoff_at": cutoff,
            "cutoff_policy": cutoff_policy,
            "profiles": _public_dto(profiles),
        }
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/screening/{symbol}")
def get_security_screening(
    symbol: str,
    knowledge_cutoff_at: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
    logical_profile_id: str | None = Query(default=None),
    profile_revision_id: str | None = Query(default=None),
):
    try:
        cutoff, cutoff_policy = resolve_knowledge_cutoff(
            knowledge_cutoff_at, as_of_date
        )
        result = _screening_service().analyze(
            normalize_symbol(symbol),
            cutoff,
            logical_profile_id=logical_profile_id,
            profile_revision_id=profile_revision_id,
        )
        return {**_public_dto(result), "cutoff_policy": cutoff_policy}
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/deployment-plan/{symbol}")
def get_deployment_plans(
    symbol: str,
    knowledge_cutoff_at: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
):
    try:
        cutoff, cutoff_policy = resolve_knowledge_cutoff(knowledge_cutoff_at, as_of_date)
        result = _deployment_service().analyze(normalize_symbol(symbol), cutoff)
        return {**_public_dto(result), "cutoff_policy": cutoff_policy}
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analysis/{symbol}")
def get_v2_analysis(
    symbol: str,
    knowledge_cutoff_at: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    market: str | None = Query(default=None),
    logical_synthesis_profile_id: str | None = Query(default=None),
    synthesis_profile_revision_id: str | None = Query(default=None),
):
    try:
        cutoff, cutoff_policy = resolve_knowledge_cutoff(
            knowledge_cutoff_at, as_of_date
        )
        if logical_synthesis_profile_id and synthesis_profile_revision_id:
            raise ValueError("synthesis_profile_selectors_are_mutually_exclusive")
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

    try:
        deployment_plan = _deployment_service().analyze(normalized_symbol, cutoff)
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        deployment_plan = {
            "status": "insufficient_data",
            "reason": f"deployment_plan_data_unavailable: {exc}",
            "plans": [],
            "rules_used": [],
        }

    try:
        screening = _screening_service().analyze(normalized_symbol, cutoff)
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        screening = {
            "status": "insufficient_data",
            "reason": f"screening_data_unavailable: {exc}",
            "research_result": None,
            "components": {},
            "rules_used": [],
            "automatic_order": False,
        }

    try:
        target_confluence = _evidence_analysis_service().synthesize(
            symbol=normalized_symbol,
            knowledge_cutoff_at=cutoff,
            valuation=valuation,
            technical_support=technical_support,
            logical_profile_id=logical_synthesis_profile_id,
            profile_revision_id=synthesis_profile_revision_id,
        )
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        target_confluence = {
            "status": "insufficient_data",
            "reason": f"target_confluence_unavailable: {exc}",
            "overlap_ranges": [],
            "candidate_count": 0,
            "support_count": 0,
            "independent_method_count": 0,
            "evidence_strength": None,
            "rules_used": [],
            "automatic_order": False,
        }

    valuation_available = valuation["status"] in {"available", "not_applicable"}
    liquidity_available = liquidity["status"] == "available"
    technical_available = technical_support["status"] == "available"
    screening_available = screening["status"] == "available"
    confluence_available = target_confluence["status"] == "available"
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
    if screening_available:
        available_sections.append("screening")
    else:
        missing.append("screening")
    if confluence_available:
        available_sections.append("target_confluence")
    else:
        missing.append("target_confluence")
    needs_human = []
    if valuation["status"] == "needs_human_input":
        if valuation["reason"] == "approved_forward_eps_required":
            needs_human.append("approved_forward_eps")
        elif valuation["reason"] == "approved_symbol_pe_missing_at_knowledge_cutoff":
            needs_human.append("approved_symbol_pe")
    if technical_support["status"] == "needs_human_input":
        technical_requirement = {
            "manual_anchor_required": "manual_anchor",
            "approved_manual_anchor_required": "approved_manual_anchor",
            "anchor_approval_revoked": "approved_manual_anchor",
        }.get(technical_support.get("reason"))
        if technical_requirement:
            needs_human.append(technical_requirement)
    if screening["status"] == "needs_human_input":
        screening_requirement = {
            "approved_screening_profile_required": "approved_screening_profile",
            "screening_profile_selection_required": "screening_profile_selection",
            "screening_profile_approval_revoked": "approved_screening_profile",
            "screening_profile_revoked": "approved_screening_profile",
        }.get(screening.get("reason"))
        if screening_requirement:
            needs_human.append(screening_requirement)
    if target_confluence["status"] == "needs_human_input":
        synthesis_requirement = {
            "approved_synthesis_profile_required": "approved_synthesis_profile",
            "synthesis_profile_selection_required": "synthesis_profile_selection",
            "synthesis_profile_revision_superseded": "approved_synthesis_profile",
            "synthesis_profile_revision_not_visible_at_cutoff": "approved_synthesis_profile",
            "synthesis_profile_approval_revoked": "approved_synthesis_profile",
            "synthesis_profile_revoked": "approved_synthesis_profile",
        }.get(target_confluence.get("reason"))
        if synthesis_requirement:
            needs_human.append(synthesis_requirement)
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
        "target_confluence": target_confluence,
        "deployment_plan": deployment_plan,
        "screening": screening,
        "invalidation": valuation["invalidation_conditions"],
        "rules_used": valuation["rules_used"] + liquidity.get("rules_used", []) + technical_support.get("rules_used", []) + deployment_plan.get("rules_used", []) + screening.get("rules_used", []) + target_confluence.get("rules_used", []),
        "unsupported": ["eva_formula", "margin_return_8_percent_formula"],
        "snapshot_id": None,
    }


@router.post("/analysis/{symbol}/refresh")
def refresh_v2_analysis(
    symbol: str,
    payload: AnalysisRefreshRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
    knowledge_cutoff_at: str | None = Query(default=None),
    as_of_date: str | None = Query(default=None),
    industry: str | None = Query(default=None),
    market: str | None = Query(default=None),
):
    try:
        _require_write_access(admin_api_key)
        caller_supplied_cutoff = bool(knowledge_cutoff_at or as_of_date)
        cutoff, cutoff_policy = resolve_knowledge_cutoff(
            knowledge_cutoff_at, as_of_date
        )
        analysis = get_v2_analysis(
            symbol=symbol,
            knowledge_cutoff_at=cutoff,
            as_of_date=None,
            industry=industry,
            market=market,
            logical_synthesis_profile_id=payload.logical_synthesis_profile_id,
            synthesis_profile_revision_id=payload.synthesis_profile_revision_id,
        )
        analysis["cutoff_policy"] = cutoff_policy
        capture_mode = (
            CaptureMode.HISTORICAL_RECONSTRUCTION
            if caller_supplied_cutoff
            else CaptureMode.LIVE_REFRESH
        )
        result = _evidence_analysis_service().create_snapshot(
            analysis=analysis,
            capture_mode=capture_mode,
            idempotency_key=idempotency_key,
            supersedes_snapshot_id=payload.supersedes_snapshot_id,
        )
        return {"status": "available", "snapshot": _public_dto(result)}
    except HTTPException:
        raise
    except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
        detail = str(exc)
        status_code = 409 if "idempotency key" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/analysis/snapshots/{snapshot_id}")
def get_v2_analysis_snapshot(snapshot_id: str):
    try:
        result = _evidence_analysis_service().snapshot_repository.get(snapshot_id)
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="analysis_snapshot_not_found")
    return {"status": "available", "snapshot": _public_dto(result)}


@router.post("/evaluations/runs")
def create_evaluation_run(
    payload: EvaluationRunRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
):
    try:
        actor = _require_write_access(admin_api_key)
        result = _performance_validation_service().create_run(
            snapshot_ids=payload.snapshot_ids,
            profile_acknowledgement=payload.evaluation_profile_acknowledgement,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        return {"status": "available", "evaluation_run": _public_dto(result)}
    except HTTPException:
        raise
    except (ValueError, RuntimeError, sqlite3.Error) as exc:
        detail = str(exc)
        status_code = 409 if "idempotency key" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/evaluations/runs/{run_id}/results")
def get_evaluation_run_results(run_id: str):
    results = _performance_validation_service().results_for_run(run_id)
    if results is None:
        raise HTTPException(status_code=404, detail="evaluation_run_not_found")
    return {"status": "available", "results": _public_dto(results)}


@router.get("/evaluations/runs/{run_id}")
def get_evaluation_run(run_id: str):
    result = _performance_validation_service().get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation_run_not_found")
    return {"status": "available", "evaluation_run": _public_dto(result)}


@router.get("/analysis/snapshots/{snapshot_id}/evaluations")
def get_snapshot_evaluations(snapshot_id: str):
    return {
        "status": "available",
        "results": _public_dto(
            _performance_validation_service().results_for_snapshot(snapshot_id)
        ),
    }


@router.get("/performance/summary")
def get_performance_summary(evaluation_run_id: str = Query(...)):
    result = _performance_validation_service().summary(evaluation_run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="evaluation_run_not_found")
    return {"status": "available", "performance_summary": _public_dto(result)}


@router.get("/model-rules")
def list_v2_model_rules():
    return {
        "model_version": "2.0.0",
        "official_affiliation": False,
        "rules": [rule.to_dict() for rule in RuleRegistry().list_rules()],
    }
