"""Phase 12 local Research Review Queue API."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from src.api.workflow_security import CSRF_COOKIE_NAME
from src.api.research_audit import write_research_audit
from src.domain.research_workflow import ResearchQueueOrder
from src.repositories.analysis_snapshot_repository import SnapshotIntegrityError
from src.repositories.research_workflow_repository import (
    ResearchWorkflowNotFoundError,
    ResearchWorkflowRepository,
)
from src.services.research_review_service import ResearchReviewService


router = APIRouter(prefix="/api/v2/research", tags=["research-review-queue"])


class StrictResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddMembershipRequest(StrictResearchRequest):
    symbol: str


class AcknowledgmentRequest(StrictResearchRequest):
    acknowledged_snapshot_id: str
    comparison_cutoff: str


class EmptyResearchRequest(StrictResearchRequest):
    pass


def _service() -> ResearchReviewService:
    return ResearchReviewService(os.getenv("DATABASE_PATH", "data/cache.db"))


def _repository() -> ResearchWorkflowRepository:
    return ResearchWorkflowRepository(os.getenv("DATABASE_PATH", "data/cache.db"))


def _cutoff(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(status_code=422, detail="comparison_cutoff_required")
    return value


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResearchWorkflowNotFoundError):
        return HTTPException(status_code=404, detail="research_watchlist_item_not_found")
    if isinstance(exc, SnapshotIntegrityError):
        return HTTPException(status_code=422, detail="snapshot_integrity_error")
    if isinstance(exc, ValueError):
        detail = str(exc)
        status = 409 if detail == "review_idempotency_conflict" else 422
        return HTTPException(status_code=status, detail=detail)
    if isinstance(exc, sqlite3.Error):
        return HTTPException(status_code=500, detail="research_workflow_unavailable")
    return HTTPException(status_code=500, detail="research_workflow_unavailable")


def _audit(
    request: Request, *, command_type: str, item_id: str | None = None,
    event_id: str | None = None, symbol: str | None = None,
    outcome: str, reason: str,
) -> None:
    write_research_audit(
        correlation_id=request.state.research_correlation_id,
        command_type=command_type,
        item_id=item_id,
        event_id=event_id,
        symbol=symbol,
        outcome=outcome,
        reason=reason,
        server_timestamp=request.state.research_request_received_at,
    )


def _audit_failure(
    request: Request, exc: Exception, *, command_type: str,
    item_id: str | None = None, symbol: str | None = None,
) -> HTTPException:
    mapped = _map_error(exc)
    _audit(
        request, command_type=command_type, item_id=item_id, symbol=symbol,
        outcome="failed" if mapped.status_code >= 500 else "rejected",
        reason=str(mapped.detail),
    )
    return mapped


@router.get("/csrf-token")
def csrf_token(request: Request, response: Response):
    if os.getenv("RESEARCH_WORKFLOW_WRITES_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(status_code=503, detail="research_workflow_writes_disabled")
    try:
        session_id, token, expires = request.state.research_csrf_sessions.issue(
            request.state.research_request_received_at
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.set_cookie(
        CSRF_COOKIE_NAME, session_id, httponly=True, samesite="strict",
        secure=False, path="/api/v2/research",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": token, "expires_at": expires.isoformat().replace("+00:00", "Z")}


@router.get("/queue")
def queue(
    request: Request,
    comparison_cutoff: str | None = Query(None),
    include_archived: bool = Query(False),
    limit: int = Query(25, ge=1, le=50),
    order: ResearchQueueOrder = Query(ResearchQueueOrder.SYMBOL),
):
    try:
        return _service().queue(
            comparison_cutoff=_cutoff(comparison_cutoff),
            request_received_at=request.state.research_request_received_at,
            include_archived=include_archived,
            limit=limit,
            order=order,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/queue/{item_id}")
def queue_detail(
    item_id: str, request: Request,
    comparison_cutoff: str | None = Query(None),
):
    try:
        return _service().detail(
            item_id, comparison_cutoff=_cutoff(comparison_cutoff),
            request_received_at=request.state.research_request_received_at,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/queue")
def add_membership(payload: AddMembershipRequest, request: Request, response: Response):
    try:
        item = _repository().add_membership(payload.symbol)
        response.status_code = 201 if item["created"] else 200
        reason = "created" if item["created"] else "restored" if item["restored"] else "existing"
        _audit(
            request, command_type="add_membership",
            item_id=item["watchlist_item_id"], symbol=item["symbol"],
            outcome="success", reason=reason,
        )
        return item
    except Exception as exc:
        raise _audit_failure(
            request, exc, command_type="add_membership", symbol=None,
        ) from exc


@router.post("/queue/{item_id}/archive")
def archive_membership(item_id: str, payload: EmptyResearchRequest, request: Request):
    try:
        item = _repository().archive(item_id)
        _audit(
            request, command_type="archive_membership", item_id=item_id,
            symbol=item["symbol"], outcome="success", reason="archived",
        )
        return item
    except Exception as exc:
        raise _audit_failure(
            request, exc, command_type="archive_membership", item_id=item_id,
        ) from exc


@router.post("/queue/{item_id}/unarchive")
def unarchive_membership(item_id: str, payload: EmptyResearchRequest, request: Request):
    try:
        item = _repository().unarchive(item_id)
        _audit(
            request, command_type="unarchive_membership", item_id=item_id,
            symbol=item["symbol"], outcome="success", reason="unarchived",
        )
        return item
    except Exception as exc:
        raise _audit_failure(
            request, exc, command_type="unarchive_membership", item_id=item_id,
        ) from exc


@router.post("/queue/{item_id}/acknowledgments")
def acknowledge(
    item_id: str, payload: AcknowledgmentRequest, request: Request,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if idempotency_key is None or not idempotency_key.strip():
        _audit(
            request, command_type="acknowledge_snapshot", item_id=item_id,
            outcome="rejected", reason="idempotency_key_required",
        )
        raise HTTPException(status_code=422, detail="idempotency_key_required")
    try:
        service = _service()
        audit_snapshot = service.snapshots.get(payload.acknowledged_snapshot_id)
        event = service.acknowledge(
            item_id, snapshot_id=payload.acknowledged_snapshot_id,
            comparison_cutoff=payload.comparison_cutoff,
            idempotency_key=idempotency_key,
            request_received_at=request.state.research_request_received_at,
        )
        response.status_code = 201 if event["created"] else 200
        _audit(
            request, command_type="acknowledge_snapshot", item_id=item_id,
            event_id=event["review_event_id"],
            symbol=audit_snapshot["symbol"] if audit_snapshot else None,
            outcome="success", reason="created" if event["created"] else "existing",
        )
        return event
    except Exception as exc:
        raise _audit_failure(
            request, exc, command_type="acknowledge_snapshot", item_id=item_id,
        ) from exc
