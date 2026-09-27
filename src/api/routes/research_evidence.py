"""Installed local evidence work area; candidate retention is not approval."""
from fastapi import APIRouter, Header, Query, Request

from src.api.routes.installed_data_operations import _get_db_path, _get_instance_id
from src.api.routes.local_assumptions import invoke
from src.domain.research_evidence import EvidenceInput
from src.services.research_evidence_service import ResearchEvidenceService

router = APIRouter(prefix="/api/v2/research/evidence", tags=["research-evidence"])


def service(request):
    _get_instance_id(request)
    return ResearchEvidenceService(_get_db_path(request))


@router.get("/{symbol}")
def read(symbol: str, request: Request, history: bool = False,
         limit: int = Query(100, ge=1, le=100), before: str | None = None):
    return invoke(lambda: service(request).list(symbol, include_history=history, limit=limit, before=before))


@router.post("/{symbol}")
def append(symbol: str, payload: EvidenceInput, request: Request,
           key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)):
    return invoke(lambda: service(request).append(symbol, payload.model_dump(mode="json"), key))


@router.get("/{symbol}/reuse")
def reuse(symbol: str, request: Request, scope: str = Query(min_length=1, max_length=500),
          fiscal_year: int | None = Query(None, ge=1900, le=2200), force: bool = False, new_information: bool = False):
    return invoke(lambda: service(request).lookup_reuse(symbol, fiscal_year, scope, force=force, new_information=new_information))


@router.get("/{symbol}/{record_id}/candidate")
def candidate(symbol: str, record_id: str, request: Request):
    def result():
        kind, values = service(request).candidate_values(symbol, record_id)
        return {"kind": kind, "values": values, "candidate_id": record_id, "approval_required": True}
    return invoke(result)
