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
    expected_content_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    research_year: int | None = Field(default=None, ge=1900, le=2200, strict=True)
    include_research_context: bool = False


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
    return invoke(lambda: service(request).save(symbol, payload.knowledge_cutoff_at, payload.note,
                                              idempotency_key, payload.expected_content_fingerprint,
                                              payload.research_year, payload.include_research_context))


@router.get("/{symbol}/preview")
def preview(symbol: str, request: Request, research_year: int | None = Query(None, ge=1900, le=2200)):
    return invoke(lambda: service(request).preview(symbol, research_year))
