"""GET-only public Universe Foundation API with safe descriptive DTOs."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from src.domain.universe import UniverseVenue, coerce_venue, validate_knowledge_cutoff_at
from src.repositories.universe_repository import UniverseStorageUnavailable
from src.services.universe_service import UniverseService


router = APIRouter(prefix="/api/v2/universe", tags=["universe-foundation"])


def _service() -> UniverseService:
    return UniverseService(os.getenv("UNIVERSE_DB_PATH", "data/cache.db"))


def _cutoff(value: str | None) -> str:
    if value is None:
        raise HTTPException(status_code=422, detail="knowledge_cutoff_at_required")
    try:
        return validate_knowledge_cutoff_at(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UniverseStorageUnavailable) or isinstance(exc, sqlite3.Error):
        return HTTPException(status_code=503, detail="universe_storage_unavailable")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="universe_storage_unavailable")


@router.get("/instruments/{canonical_symbol}")
def instrument(canonical_symbol: str, knowledge_cutoff_at: str | None = Query(None)):
    try:
        return _service().get_instrument(canonical_symbol, knowledge_cutoff_at=_cutoff(knowledge_cutoff_at))
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/resolve")
def resolve(official_code: str | None = Query(None), venue: str | None = Query(None),
           knowledge_cutoff_at: str | None = Query(None)):
    if not official_code or not venue:
        raise HTTPException(status_code=422, detail="official_code_and_venue_required")
    try:
        venue_value = coerce_venue(venue)
        return _service().resolve(official_code=official_code, venue=venue_value,
                                  knowledge_cutoff_at=_cutoff(knowledge_cutoff_at))
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


@router.get("/instruments")
def instruments(knowledge_cutoff_at: str | None = Query(None), query: str | None = Query(None),
               venue: str | None = Query(None), security_type: str | None = Query(None),
               listing_status: str | None = Query(None), cursor: str | None = Query(None),
               limit: int = Query(25, ge=1, le=100)):
    try:
        venue_value = coerce_venue(venue) if venue else None
        return _service().list(knowledge_cutoff_at=_cutoff(knowledge_cutoff_at), query=query,
                               venue=venue_value, security_type=security_type, listing_status=listing_status,
                               cursor=cursor, limit=limit)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_error(exc) from exc


__all__ = ["router"]
