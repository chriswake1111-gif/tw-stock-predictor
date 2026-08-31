"""Phase 17 Daily Research Review Context API."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from src.api.research_audit import write_research_audit
from src.repositories.daily_research_read_repository import (
    DailyResearchContractUnavailable,
)
from src.services.daily_research_review_context_service import (
    DailyResearchBaselineIdempotencyConflict,
    DailyResearchBaselineNotEligible,
    DailyResearchCursorPopulationChanged,
    DailyResearchItemInactive,
    DailyResearchItemNotFound,
    DailyResearchRefreshNotEligible,
    DailyResearchRefreshRace,
    DailyResearchReviewContextService,
)


router = APIRouter(prefix="/api/v2/research", tags=["daily-research-review-context"])


class StrictDailyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaselineSelectionRequest(StrictDailyRequest):
    baseline_snapshot_id: str
    knowledge_cutoff_at: str


class SnapshotRefreshRequest(StrictDailyRequest):
    market_date: str
    loaded_knowledge_cutoff_at: str
    expected_snapshot_id: str | None = None
    advance_knowledge_cutoff: bool


def _build_refresh_analysis(
    symbol: str,
    knowledge_cutoff_at: str,
    context: dict[str, Any],
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Reuse the V2 evidence engine without exposing a second Daily API path."""
    from src.api.routes.v2_valuation import _build_v2_analysis

    return _build_v2_analysis(
        symbol=symbol,
        knowledge_cutoff_at=knowledge_cutoff_at,
        auto_migrate=False,
        connection=connection,
    )


def _service() -> DailyResearchReviewContextService:
    return DailyResearchReviewContextService(
        os.getenv("DATABASE_PATH", "data/cache.db"),
        analysis_builder=_build_refresh_analysis,
    )


def _received_at(request: Request):
    return request.state.research_request_received_at


def _correlation_id(request: Request) -> str:
    return str(request.state.research_correlation_id)


def _audit(
    request: Request,
    *,
    command_type: str,
    item_id: str | None,
    event_id: str | None = None,
    outcome: str,
    reason: str,
) -> None:
    write_research_audit(
        correlation_id=_correlation_id(request),
        command_type=command_type,
        item_id=item_id,
        event_id=event_id,
        symbol=None,
        outcome=outcome,
        reason=reason,
        server_timestamp=_received_at(request),
    )


def _map_error(exc: Exception, *, command_type: str | None = None) -> HTTPException:
    if isinstance(exc, DailyResearchItemNotFound):
        return HTTPException(status_code=404, detail="research_watchlist_item_not_found")
    if isinstance(exc, DailyResearchItemInactive):
        if command_type == "daily_baseline_selection":
            return HTTPException(status_code=409, detail="baseline_selection_item_not_active")
        return HTTPException(status_code=404, detail="research_watchlist_item_not_found")
    if isinstance(exc, DailyResearchContractUnavailable):
        return HTTPException(status_code=503, detail=exc.code)
    if isinstance(exc, DailyResearchCursorPopulationChanged):
        return HTTPException(status_code=409, detail=exc.code)
    if isinstance(exc, DailyResearchRefreshRace):
        return HTTPException(status_code=409, detail={"code": exc.code, "gate": exc.gate})
    if isinstance(exc, DailyResearchBaselineIdempotencyConflict):
        return HTTPException(status_code=409, detail=exc.code)
    if isinstance(exc, DailyResearchBaselineNotEligible):
        return HTTPException(
            status_code=422,
            detail={"code": exc.code, "eligibility": exc.eligibility},
        )
    if isinstance(exc, DailyResearchRefreshNotEligible):
        return HTTPException(
            status_code=422,
            detail={"code": exc.code, "gate": exc.gate},
        )
    if isinstance(exc, ValueError):
        detail = str(exc)
        status = 409 if "conflict" in detail or detail.endswith("_race") else 422
        return HTTPException(status_code=status, detail=detail)
    if isinstance(exc, sqlite3.Error):
        return HTTPException(status_code=503, detail="daily_research_unavailable")
    return HTTPException(status_code=500, detail="daily_research_unavailable")


def _audit_failure(
    request: Request,
    exc: Exception,
    *,
    command_type: str,
    item_id: str | None,
) -> HTTPException:
    mapped = _map_error(exc, command_type=command_type)
    _audit(
        request,
        command_type=command_type,
        item_id=item_id,
        outcome="failed" if mapped.status_code >= 500 else "rejected",
        reason=str(mapped.detail),
    )
    return mapped


@router.get("/daily-context")
def daily_context(
    request: Request,
    market_date: str = Query(...),
    knowledge_cutoff_at: str = Query(...),
    limit: int = Query(25, ge=1, le=50),
    cursor: str | None = Query(None),
):
    try:
        return _service().list(
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            request_received_at=_received_at(request),
            limit=limit,
            cursor=cursor,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/daily-context/{item_id}")
def daily_context_detail(
    item_id: str,
    request: Request,
    market_date: str = Query(...),
    knowledge_cutoff_at: str = Query(...),
):
    try:
        return _service().detail(
            item_id,
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            request_received_at=_received_at(request),
        )
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/daily-context/{item_id}/baseline-selections")
def select_daily_baseline(
    item_id: str,
    payload: BaselineSelectionRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if idempotency_key is None or not idempotency_key.strip():
        _audit(
            request,
            command_type="daily_baseline_selection",
            item_id=item_id,
            outcome="rejected",
            reason="idempotency_key_required",
        )
        raise HTTPException(status_code=422, detail="idempotency_key_required")
    try:
        result = _service().select_baseline(
            item_id,
            baseline_snapshot_id=payload.baseline_snapshot_id,
            knowledge_cutoff_at=payload.knowledge_cutoff_at,
            request_received_at=_received_at(request),
            idempotency_key=idempotency_key,
            correlation_id=_correlation_id(request),
        )
        response.status_code = 201 if result["baseline_selection_event"]["created"] else 200
        _audit(
            request,
            command_type="daily_baseline_selection",
            item_id=item_id,
            event_id=result["baseline_selection_event"]["review_event_id"],
            outcome="success",
            reason=("created" if result["baseline_selection_event"]["created"] else "existing"),
        )
        return result
    except Exception as exc:
        raise _audit_failure(
            request,
            exc,
            command_type="daily_baseline_selection",
            item_id=item_id,
        ) from exc


@router.post("/queue/{item_id}/snapshot-refresh")
def refresh_daily_snapshot(
    item_id: str,
    payload: SnapshotRefreshRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if idempotency_key is None or not idempotency_key.strip():
        _audit(
            request,
            command_type="daily_snapshot_refresh",
            item_id=item_id,
            outcome="rejected",
            reason="idempotency_key_required",
        )
        raise HTTPException(status_code=422, detail="idempotency_key_required")
    try:
        result = _service().refresh_snapshot(
            item_id,
            market_date=payload.market_date,
            loaded_knowledge_cutoff_at=payload.loaded_knowledge_cutoff_at,
            expected_snapshot_id=payload.expected_snapshot_id,
            advance_knowledge_cutoff=payload.advance_knowledge_cutoff,
            request_received_at=_received_at(request),
            idempotency_key=idempotency_key,
        )
        response.status_code = 201 if result.get("created", True) else 200
        _audit(
            request,
            command_type="daily_snapshot_refresh",
            item_id=item_id,
            outcome="success",
            reason="created" if result.get("created", True) else "existing",
        )
        return result
    except Exception as exc:
        raise _audit_failure(
            request,
            exc,
            command_type="daily_snapshot_refresh",
            item_id=item_id,
        ) from exc


__all__ = ["router"]
