"""GET-only Phase 15 historical EOD coverage visibility API."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from src.repositories.eod_close_repository import EodStorageUnavailable
from src.services.eod_coverage_service import EodCoverageService


router = APIRouter(prefix="/api/v2/market-context/eod-close", tags=["eod-coverage-visibility"])


def _service() -> EodCoverageService:
    return EodCoverageService(os.getenv("EOD_DB_PATH", os.getenv("UNIVERSE_DB_PATH", "data/cache.db")))


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (EodStorageUnavailable, sqlite3.Error)):
        return HTTPException(status_code=503, detail="eod_coverage_storage_unavailable")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="eod_coverage_storage_unavailable")


@router.get("/coverage/as-of")
def coverage_as_of(
    venue: str = Query(...),
    source_trade_date: str = Query(...),
    knowledge_cutoff_at: str = Query(...),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
):
    try:
        return _service().as_of(
            venue=venue,
            source_trade_date=source_trade_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            limit=limit,
            cursor=cursor,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


__all__ = ["router"]
