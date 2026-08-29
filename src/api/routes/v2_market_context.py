"""GET-only Phase 16 neutral batch market context API."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from src.domain.neutral_batch_market_context import (
    NeutralBatchMarketContextCursorError,
)
from src.repositories.eod_close_repository import EodStorageUnavailable
from src.services.neutral_batch_market_context_service import (
    NeutralBatchMarketContextService,
)


router = APIRouter(
    prefix="/api/v2/market-context",
    tags=["neutral-batch-market-context"],
)


def _service() -> NeutralBatchMarketContextService:
    return NeutralBatchMarketContextService(
        os.getenv("EOD_DB_PATH", os.getenv("UNIVERSE_DB_PATH", "data/cache.db"))
    )


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (EodStorageUnavailable, sqlite3.Error)):
        return HTTPException(
            status_code=503,
            detail="neutral_batch_market_context_storage_unavailable",
        )
    if isinstance(exc, NeutralBatchMarketContextCursorError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ValueError) and any(
        str(exc).startswith(prefix)
        for prefix in (
            "market_date",
            "knowledge_cutoff_at",
            "venue_scope",
            "limit",
            "cursor",
        )
    ):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(
        status_code=503,
        detail="neutral_batch_market_context_storage_unavailable",
    )


@router.get("/batch/as-of")
def batch_as_of(
    market_date: str = Query(...),
    knowledge_cutoff_at: str = Query(...),
    venue_scope: str = Query("TWSE_TPEX"),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
):
    try:
        return _service().as_of(
            market_date=market_date,
            knowledge_cutoff_at=knowledge_cutoff_at,
            venue_scope=venue_scope,
            limit=limit,
            cursor=cursor,
        )
    except Exception as exc:
        raise _map_error(exc) from exc


__all__ = ["batch_as_of", "router"]
