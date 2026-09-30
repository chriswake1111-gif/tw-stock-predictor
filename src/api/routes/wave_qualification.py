"""Independent read-only diagnostics; never part of a save confirmation."""
from fastapi import APIRouter, Request, Response
from src.api.routes.installed_data_operations import _get_db_path, _get_instance_id
from src.api.routes.local_assumptions import invoke
from src.services.wave_qualification_service import WaveQualificationService

router = APIRouter(prefix="/api/v2/research/wave-qualification", tags=["wave-qualification"])


@router.get("/{symbol}")
def get_qualification(symbol: str, request: Request, response: Response):
    _get_instance_id(request)
    response.headers["Cache-Control"] = "no-store"
    return invoke(lambda: WaveQualificationService(_get_db_path(request)).get(symbol))
