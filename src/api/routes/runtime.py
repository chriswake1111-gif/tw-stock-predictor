"""Local runtime health/readiness contract for packaged startup."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from src.services.research_evidence_service import guidance_enabled
from src.domain.research_evidence import GUIDANCE_CONTRACT
from src.services.earnings_public_data_service import earnings_enabled


READY_CONTRACT_VERSION = "tw_stock_ready_v1"
router = APIRouter(tags=["runtime"])


def readiness_payload(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "runtime_readiness", None)
    if state is None:
        return {
            "contract_version": READY_CONTRACT_VERSION,
            "status": "not_ready",
            "ready": False,
            "reason": "app_startup_not_completed",
        }
    return {
        "contract_version": READY_CONTRACT_VERSION,
        "status": "ready" if state.get("ready") else "not_ready",
        "ready": bool(state.get("ready")),
        "app_version": state.get("app_version", "unknown"),
        "build_sha": state.get("build_sha", "unknown"),
        "origin": state.get("origin"),
        "database_state": state.get("database_state"),
        "scheduler_enabled": bool(state.get("scheduler_enabled", False)),
        "reason": state.get("reason"),
        "research_assistant_contract": "tw_stock_research_assistant_v1",
        "valuation_pairing_policy": "same_fiscal_year_v1",
        "research_guidance_contract": GUIDANCE_CONTRACT if guidance_enabled() else None,
        "earnings_research_contract": "earnings-four-quarter-v1" if earnings_enabled() else None,
    }


@router.get("/api/ready")
def get_ready(request: Request) -> JSONResponse:
    payload = readiness_payload(request)
    return JSONResponse(status_code=200 if payload["ready"] else 503, content=payload)


__all__ = ["READY_CONTRACT_VERSION", "get_ready", "readiness_payload", "router"]
