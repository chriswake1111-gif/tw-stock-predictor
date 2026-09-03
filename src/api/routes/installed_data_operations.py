"""Phase 19 Installed Data Synchronization and Local Data Operations API endpoints."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.api.workflow_security import CSRF_COOKIE_NAME, CsrfSessionStore
from src.domain.installed_data_operations import (
    InstalledOperationStatus,
    InstalledOperationType,
    InstalledReadiness,
    OperationActiveConflict,
)
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.services.installed_data_sync_service import (
    GLOBAL_OPERATION_DEADLINE_SECONDS,
    InstalledDataSyncService,
)
from src.services.installed_readiness_evaluator import evaluate_installed_readiness

router = APIRouter(prefix="/api/v2/data-operations", tags=["Data Operations"])


class SyncRequestBody(BaseModel):
    target_symbols: list[str] | None = None
    deadline_seconds: float = Field(default=180.0, ge=1.0, le=180.0)


class BootstrapRequestBody(BaseModel):
    target_symbols: list[str] | None = None


def _get_db_path(request: Request) -> str:
    settings = getattr(request.app.state, "runtime_settings", None)
    if settings and getattr(settings, "paths", None):
        return str(settings.paths.database_path)
    return os.getenv("DATABASE_PATH", os.getenv("EOD_DB_PATH", "data/cache.db"))


def _get_instance_id(request: Request) -> str:
    handshake = getattr(request.app.state, "launch_handshake", None)
    if handshake and isinstance(handshake, dict) and handshake.get("launch_id"):
        return str(handshake["launch_id"])
    raise HTTPException(
        status_code=503,
        detail="launch_handshake_missing_or_unvalidated",
    )


@router.get("/csrf-token")
def get_csrf_token(request: Request, response: Response) -> dict[str, str]:
    sessions = getattr(request.state, "research_csrf_sessions", None)
    if sessions is None:
        sessions = getattr(request.app.state, "csrf_sessions", None)
    if sessions is None:
        sessions = CsrfSessionStore()
        request.app.state.csrf_sessions = sessions

    received_at = getattr(
        request.state, "research_request_received_at", datetime.now(timezone.utc)
    )
    session_id, token, expires = sessions.issue(received_at)
    response.set_cookie(
        CSRF_COOKIE_NAME,
        session_id,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/api/v2",
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "csrf_token": token,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


@router.get("/status")
def get_data_operations_status(request: Request) -> dict[str, Any]:
    db_path = _get_db_path(request)
    repo = InstalledDataOperationsRepository(db_path)
    active = repo.get_active_operation()

    with repo._get_connection() as conn:
        readiness_enum, details = evaluate_installed_readiness(conn)

    readiness = readiness_enum.value
    if active is not None and readiness == InstalledReadiness.NOT_INITIALIZED.value:
        readiness = InstalledReadiness.PARTIAL.value

    return {
        "readiness": readiness,
        "is_syncing": active is not None,
        "active_operation": (
            {
                "operation_id": active.operation_id,
                "operation_type": active.operation_type,
                "status": active.status,
                "current_stage": active.current_stage,
                "lease_expires_at": active.lease_expires_at,
                "created_at": active.created_at,
            }
            if active
            else None
        ),
        "market_context_summary": {
            "calendar_status": "available" if details["calendar_sessions"] > 0 else "missing",
            "latest_eod_date": details["latest_eod_date"],
            "m1b_latest_period": details["cbc_m1b_period"],
        },
        "readiness_details": details,
    }


@router.get("/operations/{operation_id}")
def get_operation_details(operation_id: str, request: Request) -> dict[str, Any]:
    db_path = _get_db_path(request)
    repo = InstalledDataOperationsRepository(db_path)
    op = repo.get_operation_by_id(operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {operation_id} not found")

    items = repo.list_items_by_operation(operation_id)
    return {
        "operation_id": op.operation_id,
        "operation_type": op.operation_type,
        "status": op.status,
        "current_stage": op.current_stage,
        "lease_owner_id": op.lease_owner_id,
        "lease_expires_at": op.lease_expires_at,
        "created_at": op.created_at,
        "updated_at": op.updated_at,
        "completed_at": op.completed_at,
        "error_detail": op.error_detail,
        "items": [
            {
                "item_id": it.item_id,
                "stage": it.stage,
                "resource_id": it.resource_id,
                "status": it.status,
                "raw_resource_revision_id": it.raw_resource_revision_id,
                "ingestion_run_id": it.ingestion_run_id,
                "attempt_count": it.attempt_count,
                "created_at": it.created_at,
                "completed_at": it.completed_at,
                "error_code": it.error_code,
                "error_detail": it.error_detail,
            }
            for it in items
        ],
    }


@router.post("/sync")
def trigger_sync_operation(
    body: SyncRequestBody,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    db_path = _get_db_path(request)
    instance_id = _get_instance_id(request)
    sync_svc = InstalledDataSyncService(db_path=db_path, runtime_instance_id=instance_id)

    try:
        op_id, auth = sync_svc.create_operation_and_capability(
            operation_type=InstalledOperationType.SYNC.value,
            target_symbols=body.target_symbols,
        )
    except OperationActiveConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    deadline_sec = min(body.deadline_seconds, GLOBAL_OPERATION_DEADLINE_SECONDS)
    deadline_monotonic = time.monotonic() + deadline_sec

    def _run_bg():
        try:
            sync_svc.run_stage_prerequisites_calendar(op_id, auth, deadline_monotonic)
            sync_svc.run_stage_universe(op_id, auth, deadline_monotonic)
            sync_svc.run_stage_classification(op_id, auth, body.target_symbols, deadline_monotonic)
            sync_svc.run_stage_eod(op_id, auth, deadline_monotonic)
            sync_svc.run_stage_turnover_and_cbc(op_id, auth, deadline_monotonic)
            sync_svc.run_stage_projection(op_id, auth, deadline_monotonic)
        except Exception as exc:
            logger.exception("Background sync operation %s failed: %s", op_id, exc)
            try:
                auth.revoke()
                sync_svc.operation_repo.finalize_operation(
                    op_id, status=InstalledOperationStatus.FAILED.value, error_detail=str(exc)
                )
            except Exception:
                pass

    worker_thread = threading.Thread(target=_run_bg, name=f"data-ops-sync-{op_id}", daemon=True)
    worker_thread.start()

    return {
        "operation_id": op_id,
        "status": InstalledOperationStatus.RUNNING.value,
        "current_stage": "prerequisites_calendar",
    }


@router.post("/cancel")
def cancel_active_operation(request: Request) -> dict[str, Any]:
    db_path = _get_db_path(request)
    repo = InstalledDataOperationsRepository(db_path)
    active = repo.get_active_operation()
    if active is None:
        raise HTTPException(status_code=404, detail="No active operation to cancel")

    repo.request_cancel(active.operation_id)
    return {
        "operation_id": active.operation_id,
        "status": "cancelling",
    }


@router.post("/symbols/{symbol}/enable")
def enable_symbol(
    symbol: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    clean_sym = symbol.strip().upper()
    db_path = _get_db_path(request)
    instance_id = _get_instance_id(request)
    sync_svc = InstalledDataSyncService(db_path=db_path, runtime_instance_id=instance_id)

    try:
        op_id, auth = sync_svc.create_operation_and_capability(
            operation_type=InstalledOperationType.ENABLE_SYMBOL.value,
            target_symbols=[clean_sym],
        )
    except OperationActiveConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    deadline_sec = GLOBAL_OPERATION_DEADLINE_SECONDS
    deadline_monotonic = time.monotonic() + deadline_sec

    def _run_bg():
        try:
            sync_svc.run_symbol_enablement_pipeline(op_id, auth, clean_sym, deadline_monotonic)
        except Exception as exc:
            try:
                auth.revoke()
                sync_svc.operation_repo.finalize_operation(
                    op_id, status=InstalledOperationStatus.FAILED.value, error_detail=str(exc)
                )
            except Exception:
                pass

    worker_thread = threading.Thread(target=_run_bg, name=f"data-ops-enable-{clean_sym}-{op_id}", daemon=True)
    worker_thread.start()

    return {
        "operation_id": op_id,
        "status": InstalledOperationStatus.RUNNING.value,
        "current_stage": "classification",
    }
