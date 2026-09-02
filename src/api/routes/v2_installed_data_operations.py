"""Phase 19 Installed Data Synchronization and Local Data Operations API endpoints."""

from __future__ import annotations

import os
from typing import Any, Sequence

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from src.domain.installed_data_operations import (
    InstalledOperationStatus,
    InstalledOperationType,
    InstalledReadiness,
    OperationActiveConflict,
    OperationNotFound,
)
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.services.installed_data_sync_service import InstalledDataSyncService

router = APIRouter(prefix="/api/v2/installed-data", tags=["Installed Data Operations"])


class SyncRequestBody(BaseModel):
    target_symbols: list[str] | None = None
    deadline_seconds: float = Field(default=300.0, ge=10.0, le=3600.0)


class BootstrapRequestBody(BaseModel):
    target_symbols: list[str] | None = None


class EnableSymbolRequestBody(BaseModel):
    symbol: str


def _get_db_path(request: Request) -> str:
    settings = getattr(request.app.state, "runtime_settings", None)
    if settings and getattr(settings, "paths", None):
        return str(settings.paths.database_path)
    return os.getenv("DATABASE_PATH", "data/cache.db")


def _get_instance_id(request: Request) -> str:
    handshake = getattr(request.app.state, "launch_handshake", None)
    if handshake and isinstance(handshake, dict) and handshake.get("launch_id"):
        return str(handshake["launch_id"])
    settings = getattr(request.app.state, "runtime_settings", None)
    if settings and getattr(settings, "expected_launch_id", None):
        return str(settings.expected_launch_id)
    return "installed_app_instance"


@router.get("/status")
def get_installed_data_status(request: Request) -> dict[str, Any]:
    db_path = _get_db_path(request)
    repo = InstalledDataOperationsRepository(db_path)
    active = repo.get_active_operation()

    # Query summary state directly from DB
    latest_date: str | None = None
    m1b_period: str | None = None

    with repo._get_connection() as conn:
        try:
            row_eod = conn.execute(
                "SELECT source_trade_date FROM eod_close_source_snapshots WHERE source_trade_date_status = 'valid' ORDER BY source_trade_date DESC LIMIT 1"
            ).fetchone()
            if row_eod:
                latest_date = str(row_eod[0])
        except Exception:
            pass

        try:
            row_m1b = conn.execute(
                "SELECT period FROM cbc_m1b_monthly ORDER BY period DESC LIMIT 1"
            ).fetchone()
            if row_m1b:
                m1b_period = str(row_m1b[0])
        except Exception:
            pass

    # Compute overall readiness
    readiness = (
        InstalledReadiness.READY.value
        if latest_date is not None
        else InstalledReadiness.PARTIAL.value
        if active is not None
        else InstalledReadiness.NOT_INITIALIZED.value
    )

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
            "calendar_status": "available" if latest_date else "missing",
            "latest_eod_date": latest_date,
            "m1b_latest_period": m1b_period,
        },
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

    def _run_bg():
        try:
            sync_svc.run_stage_prerequisites_calendar(op_id, auth)
            sync_svc.run_stage_universe(op_id, auth)
            sync_svc.run_stage_classification(op_id, auth, body.target_symbols)
            sync_svc.run_stage_eod(op_id, auth)
            sync_svc.run_stage_turnover_and_cbc(op_id, auth)
            sync_svc.run_stage_projection(op_id, auth)
        except Exception as exc:
            auth.revoke()
            sync_svc.operation_repo.finalize_operation(
                op_id, status=InstalledOperationStatus.FAILED.value, error_detail=str(exc)
            )

    background_tasks.add_task(_run_bg)

    return {
        "operation_id": op_id,
        "status": InstalledOperationStatus.RUNNING.value,
        "current_stage": "prerequisites_calendar",
    }


@router.post("/bootstrap")
def trigger_bootstrap_operation(
    body: BootstrapRequestBody,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    db_path = _get_db_path(request)
    instance_id = _get_instance_id(request)
    sync_svc = InstalledDataSyncService(db_path=db_path, runtime_instance_id=instance_id)

    try:
        op_id, auth = sync_svc.create_operation_and_capability(
            operation_type=InstalledOperationType.BOOTSTRAP.value,
            target_symbols=body.target_symbols,
        )
    except OperationActiveConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _run_bg():
        try:
            sync_svc.run_stage_prerequisites_calendar(op_id, auth)
            sync_svc.run_stage_universe(op_id, auth)
            sync_svc.run_stage_classification(op_id, auth, body.target_symbols)
            sync_svc.run_stage_eod(op_id, auth)
            sync_svc.run_stage_turnover_and_cbc(op_id, auth)
            sync_svc.run_stage_projection(op_id, auth)
        except Exception as exc:
            auth.revoke()
            sync_svc.operation_repo.finalize_operation(
                op_id, status=InstalledOperationStatus.FAILED.value, error_detail=str(exc)
            )

    background_tasks.add_task(_run_bg)

    return {
        "operation_id": op_id,
        "status": InstalledOperationStatus.RUNNING.value,
        "current_stage": "prerequisites_calendar",
    }


@router.post("/enable-symbol")
def trigger_enable_symbol_operation(
    body: EnableSymbolRequestBody,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    db_path = _get_db_path(request)
    instance_id = _get_instance_id(request)
    sync_svc = InstalledDataSyncService(db_path=db_path, runtime_instance_id=instance_id)

    try:
        op_id, auth = sync_svc.create_operation_and_capability(
            operation_type=InstalledOperationType.ENABLE_SYMBOL.value,
            target_symbols=[body.symbol],
        )
    except OperationActiveConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _run_bg():
        try:
            sync_svc.run_stage_classification(op_id, auth, [body.symbol])
            sync_svc.run_stage_projection(op_id, auth)
        except Exception as exc:
            auth.revoke()
            sync_svc.operation_repo.finalize_operation(
                op_id, status=InstalledOperationStatus.FAILED.value, error_detail=str(exc)
            )

    background_tasks.add_task(_run_bg)

    return {
        "operation_id": op_id,
        "status": InstalledOperationStatus.RUNNING.value,
        "current_stage": "classification",
    }


@router.post("/operations/{operation_id}/cancel")
def cancel_operation(operation_id: str, request: Request) -> dict[str, Any]:
    db_path = _get_db_path(request)
    repo = InstalledDataOperationsRepository(db_path)
    op = repo.get_operation_by_id(operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {operation_id} not found")

    if op.status != InstalledOperationStatus.RUNNING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel operation with status '{op.status}'",
        )

    repo.request_cancel(operation_id)
    return {
        "operation_id": operation_id,
        "status": "cancelling",
    }
