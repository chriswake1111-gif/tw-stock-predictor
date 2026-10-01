"""Read-only present-research candidates. Drafts retain the existing write gate."""
from fastapi import APIRouter, Request, Response
from src.api.routes.installed_data_operations import _get_db_path, _get_instance_id
from src.api.routes.local_assumptions import invoke
from src.services.wave_candidate_service import WaveCandidateService

router = APIRouter(prefix="/api/v2/research/wave-candidates", tags=["wave-candidates"])


@router.get("/{symbol}")
def candidates(symbol: str, request: Request, response: Response):
    _get_instance_id(request)
    response.headers["Cache-Control"] = "no-store"
    return invoke(lambda: WaveCandidateService(_get_db_path(request)).get(symbol))


@router.get("/{symbol}/{candidate_id}")
def candidate(symbol: str, candidate_id: str, request: Request, response: Response):
    _get_instance_id(request)
    response.headers["Cache-Control"] = "no-store"
    def read():
        kind, values = WaveCandidateService(_get_db_path(request)).candidate_values(symbol, candidate_id)
        return dict(kind=kind, values=values, candidate_id=candidate_id, approval_required=True)
    return invoke(read)
