"""GET-only public API for the Phase 14 official EOD close context."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from src.domain.universe import validate_knowledge_cutoff_at
from src.repositories.eod_close_repository import EodStorageUnavailable
from src.services.eod_close_service import EodCloseService


router = APIRouter(prefix="/api/v2/market-context/eod-close", tags=["eod-close-context"])


def _service() -> EodCloseService:
    return EodCloseService(os.getenv("EOD_DB_PATH", os.getenv("UNIVERSE_DB_PATH", "data/cache.db")))


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (EodStorageUnavailable, sqlite3.Error)):
        return HTTPException(status_code=503, detail="eod_storage_unavailable")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="eod_storage_unavailable")


@router.get("/current/{canonical_symbol}")
def current(canonical_symbol: str):
    try:
        return _service().current(canonical_symbol)
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/as-of/{canonical_symbol}")
def as_of(
    canonical_symbol: str,
    knowledge_cutoff_at: str | None = Query(None),
):
    if knowledge_cutoff_at is None:
        raise HTTPException(status_code=422, detail="knowledge_cutoff_at_required")
    try:
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        return _service().as_of(canonical_symbol, knowledge_cutoff_at=cutoff)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


__all__ = ["router"]
