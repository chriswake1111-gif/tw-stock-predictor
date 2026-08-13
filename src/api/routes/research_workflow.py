"""Phase 12 local Research Review Queue API."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from src.api.workflow_security import CSRF_COOKIE_NAME
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
):
    try:
        return _service().queue(
            comparison_cutoff=_cutoff(comparison_cutoff),
            request_received_at=request.state.research_request_received_at,
            include_archived=include_archived,
            limit=limit,
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
def add_membership(payload: AddMembershipRequest, response: Response):
    try:
        item = _repository().add_membership(payload.symbol)
        response.status_code = 201 if item["created"] else 200
        return item
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/queue/{item_id}/archive")
def archive_membership(item_id: str):
    try:
        return _repository().archive(item_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/queue/{item_id}/unarchive")
def unarchive_membership(item_id: str):
    try:
        return _repository().unarchive(item_id)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/queue/{item_id}/acknowledgments")
def acknowledge(
    item_id: str, payload: AcknowledgmentRequest, request: Request,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if idempotency_key is None or not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="idempotency_key_required")
    try:
        event = _service().acknowledge(
            item_id, snapshot_id=payload.acknowledged_snapshot_id,
            comparison_cutoff=payload.comparison_cutoff,
            idempotency_key=idempotency_key,
            request_received_at=request.state.research_request_received_at,
        )
        response.status_code = 201 if event["created"] else 200
        return event
    except Exception as exc:
        raise _map_error(exc) from exc
