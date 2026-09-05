"""Research V2 API endpoints for current settled context and research bootstrap."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from src.services.current_research_service import CurrentResearchService
from src.services.research_bootstrap_service import ResearchBootstrapService


router = APIRouter(prefix="/api/v2/research", tags=["research-v2"])


class BootstrapRequestBody(BaseModel):
    canonical_symbol: str


def _service(request: Request | None = None) -> CurrentResearchService:
    db_path = os.getenv("DATABASE_PATH", "data/cache.db")
    if request:
        settings = getattr(request.app.state, "runtime_settings", None)
        if settings and getattr(settings, "paths", None):
            db_path = str(settings.paths.database_path)
    return CurrentResearchService(db_path)


def _bootstrap_service(request: Request) -> ResearchBootstrapService:
    db_path = os.getenv("DATABASE_PATH", "data/cache.db")
    settings = getattr(request.app.state, "runtime_settings", None)
    if settings and getattr(settings, "paths", None):
        db_path = str(settings.paths.database_path)
    handshake = getattr(request.app.state, "launch_handshake", None)
    instance_id = "installed-runtime"
    if handshake and isinstance(handshake, dict) and handshake.get("launch_id"):
        instance_id = str(handshake["launch_id"])
    return ResearchBootstrapService(db_path=db_path, runtime_instance_id=instance_id)


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, sqlite3.Error):
        return HTTPException(status_code=503, detail="research_storage_unavailable")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="research_storage_unavailable")


@router.get("/context/current/{canonical_symbol}")
def current_context(
    canonical_symbol: str,
    request: Request,
    knowledge_cutoff_at: str | None = Query(None),
):
    try:
        return _service(request).get_context(
            canonical_symbol=canonical_symbol,
            knowledge_cutoff_at=knowledge_cutoff_at,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/summary/{canonical_symbol}")
def research_summary(
    canonical_symbol: str,
    request: Request,
    as_of: str | None = Query(None),
    knowledge_cutoff_at: str | None = Query(None),
):
    cutoff = as_of or knowledge_cutoff_at
    try:
        summary = _service(request).get_summary(
            canonical_symbol=canonical_symbol,
            knowledge_cutoff_at=cutoff,
        )
        if summary is None:
            raise HTTPException(status_code=404, detail="instrument_not_found")
        return summary
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


@router.post("/bootstrap")
def bootstrap(
    body: BootstrapRequestBody,
    request: Request,
):
    try:
        return _bootstrap_service(request).bootstrap_symbol(body.canonical_symbol)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


__all__ = ["router"]

