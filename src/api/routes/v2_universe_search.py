"""Local-only Universe search and short-name coverage API."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from src.repositories.universe_repository import UniverseStorageUnavailable
from src.services.universe_service import UniverseService


router = APIRouter(prefix="/api/v2/universe", tags=["universe-search"])


def _service() -> UniverseService:
    return UniverseService(os.getenv("UNIVERSE_DB_PATH", "data/cache.db"))


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (UniverseStorageUnavailable, sqlite3.Error)):
        return HTTPException(status_code=503, detail="universe_storage_unavailable")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="universe_storage_unavailable")


@router.get("/search")
def search(
    q: str = Query("", description="Query code, symbol, short name, or display name"),
    limit: int = Query(10, ge=1, le=50),
    knowledge_cutoff_at: str | None = Query(None),
):
    try:
        return _service().search_local(
            query=q,
            limit=limit,
            knowledge_cutoff_at=knowledge_cutoff_at,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/coverage")
def coverage(
    knowledge_cutoff_at: str | None = Query(None),
):
    try:
        return _service().short_name_coverage(
            knowledge_cutoff_at=knowledge_cutoff_at,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


__all__ = ["router"]
