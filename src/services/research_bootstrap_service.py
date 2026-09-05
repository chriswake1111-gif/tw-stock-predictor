"""Research bootstrap orchestrator service for installed stock research."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from src.domain.installed_data_operations import (
    InstalledOperationStatus,
    InstalledOperationType,
    OperationActiveConflict,
)
from src.domain.universe import parse_canonical_symbol
from src.repositories.current_research_repository import CurrentResearchRepository
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.services.current_research_service import CurrentResearchService
from src.services.installed_data_sync_service import (
    GLOBAL_OPERATION_DEADLINE_SECONDS,
    InstalledDataSyncService,
)

logger = logging.getLogger(__name__)


class ResearchBootstrapService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        runtime_instance_id: str | None = None,
        *,
        current_research_service: CurrentResearchService | None = None,
        operations_repo: InstalledDataOperationsRepository | None = None,
        sync_service: InstalledDataSyncService | None = None,
    ):
        self.db_path = os.getenv("DATABASE_PATH", db_path)
        self.runtime_instance_id = runtime_instance_id or "installed-runtime"
        self.current_research_service = current_research_service or CurrentResearchService(
            self.db_path,
            repository=CurrentResearchRepository(self.db_path),
        )
        self.operations_repo = operations_repo or InstalledDataOperationsRepository(self.db_path)
        self.sync_service = sync_service or InstalledDataSyncService(
            db_path=self.db_path,
            runtime_instance_id=self.runtime_instance_id,
        )

    def bootstrap_symbol(self, canonical_symbol: str) -> dict[str, Any]:
        """Bootstrap research readiness for canonical_symbol.

        Evaluates installed readiness, active data operations, and target coverage.
        Maps active conflicts gracefully without raw errors.
        """
        parse_canonical_symbol(canonical_symbol)

        # 1. Check if current settled research is already available
        context = self.current_research_service.get_context(canonical_symbol)
        official_close = context.get("official_close") or {}
        if official_close.get("status") == "available":
            return {
                "status": "ready",
                "canonical_symbol": canonical_symbol,
                "operation_id": None,
                "message": f"Research data for {canonical_symbol} is ready",
            }

        # 2. Check if an operation is currently active
        active = self.operations_repo.get_active_operation()
        if active is not None:
            try:
                targets = json.loads(active.target_symbols_json or "[]")
            except Exception:
                targets = []

            # Does active operation cover this symbol?
            if canonical_symbol in targets:
                return {
                    "status": "preparing",
                    "canonical_symbol": canonical_symbol,
                    "operation_id": active.operation_id,
                    "message": f"Data operation {active.operation_id} is preparing research data",
                }
            if active.operation_type in (
                InstalledOperationType.SYNC.value,
                InstalledOperationType.BOOTSTRAP.value,
            ) and (not targets or canonical_symbol in targets):
                return {
                    "status": "preparing",
                    "canonical_symbol": canonical_symbol,
                    "operation_id": active.operation_id,
                    "message": f"Sync operation {active.operation_id} is preparing research data",
                }
            # Active operation does not cover target
            return {
                "status": "waiting_for_data_operation",
                "canonical_symbol": canonical_symbol,
                "operation_id": active.operation_id,
                "message": f"Another data operation ({active.operation_id}) is currently active",
            }

        # 3. No active operation: Launch ENABLE_SYMBOL operation
        try:
            op_id, auth = self.sync_service.create_operation_and_capability(
                operation_type=InstalledOperationType.ENABLE_SYMBOL.value,
                target_symbols=[canonical_symbol],
            )
        except OperationActiveConflict:
            active_after = self.operations_repo.get_active_operation()
            op_id_after = active_after.operation_id if active_after else None
            return {
                "status": "waiting_for_data_operation",
                "canonical_symbol": canonical_symbol,
                "operation_id": op_id_after,
                "message": "Another data operation is currently active",
            }

        deadline_monotonic = time.monotonic() + GLOBAL_OPERATION_DEADLINE_SECONDS
        sync_svc = self.sync_service

        def _run_bg():
            try:
                sync_svc.run_stage_prerequisites_calendar(op_id, auth, deadline_monotonic)
                sync_svc.run_stage_universe(op_id, auth, deadline_monotonic)
                sync_svc.run_stage_classification(op_id, auth, [canonical_symbol], deadline_monotonic)
                sync_svc.run_stage_eod(op_id, auth, deadline_monotonic)
                sync_svc.run_stage_turnover_and_cbc(op_id, auth, deadline_monotonic)
                sync_svc.run_stage_projection(op_id, auth, deadline_monotonic)
            except Exception as exc:
                logger.exception("Background bootstrap operation %s failed: %s", op_id, exc)
                try:
                    auth.revoke()
                    sync_svc.operation_repo.finalize_operation(
                        op_id,
                        status=InstalledOperationStatus.FAILED.value,
                        error_detail=str(exc),
                    )
                except Exception:
                    pass

        worker = threading.Thread(target=_run_bg, name=f"bootstrap-{op_id}", daemon=True)
        worker.start()

        return {
            "status": "preparing",
            "canonical_symbol": canonical_symbol,
            "operation_id": op_id,
            "message": f"Bootstrap operation {op_id} started for {canonical_symbol}",
        }


__all__ = ["ResearchBootstrapService"]
