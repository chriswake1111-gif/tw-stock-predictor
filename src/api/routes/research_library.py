"""Explicit label commands, protected by the installed local boundary."""
from typing import Literal
from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import Field
from src.api.routes.installed_data_operations import _get_db_path, _get_instance_id
from src.api.routes.local_assumptions import Strict, invoke
from src.services.research_library_service import ResearchLibraryService
from src.services.research_home_summary_service import ResearchHomeSummaryService, home_summary_enabled

router = APIRouter(prefix="/api/v2/research/library", tags=["research-library"])


def service(request):
    _get_instance_id(request)
    return ResearchLibraryService(_get_db_path(request))


class LabelCommand(Strict):
    label: Literal["held", "favorite"]
    value: bool = Field(strict=True)
    version: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("")
def list_stocks(request: Request, category: Literal["all", "held", "favorites", "researched"] = "all",
                after: str = Query("", max_length=32), limit: int = Query(30, ge=1, le=100)):
    result = invoke(lambda: service(request).list(category, after, limit))
    if result.get("enabled"):
        result["summary_enabled"] = home_summary_enabled()
    return result


@router.get("/{symbol}/summary")
def get_summary(symbol: str, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    _get_instance_id(request)
    return invoke(lambda: ResearchHomeSummaryService(_get_db_path(request)).get(symbol))


@router.get("/{symbol}")
def get_stock(symbol: str, request: Request):
    return invoke(lambda: service(request).state(symbol))


@router.post("/{symbol}")
def set_label(symbol: str, payload: LabelCommand, request: Request,
              key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return invoke(lambda: service(request).set_label(symbol, payload.label, payload.value, payload.version, key))
