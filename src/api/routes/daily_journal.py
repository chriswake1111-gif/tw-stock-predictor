"""Installed daily observations and notes, under the research CSRF boundary."""
from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.api.routes.installed_data_operations import _get_db_path, _get_instance_id
from src.api.routes.local_assumptions import invoke
from src.services.daily_research_journal_service import DailyResearchJournalService

router = APIRouter(prefix="/api/v2/research/journal", tags=["daily-journal"])


class SaveNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_cutoff_at: str
    note: str = Field(default="", max_length=4000)


def service(request):
    _get_instance_id(request)
    return DailyResearchJournalService(_get_db_path(request))


@router.get("")
def overview(request: Request, limit: int = Query(25, ge=1, le=50), after_symbol: str = ""):
    return invoke(lambda: service(request).overview(limit, after_symbol))


@router.get("/{symbol}")
def history(symbol: str, request: Request, limit: int = Query(20, ge=1, le=50)):
    return invoke(lambda: service(request).history(symbol, limit))


@router.post("/{symbol}")
def save(symbol: str, payload: SaveNote, request: Request,
         idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return invoke(lambda: service(request).save(symbol, payload.knowledge_cutoff_at, payload.note, idempotency_key))
