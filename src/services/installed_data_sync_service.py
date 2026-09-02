"""Phase 19 Installed Data Synchronization / Local Data Operations V1 worker service."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from src.collectors.eod_close_collectors import (
    TPEX_EOD_RESOURCE_ID,
    TWSE_EOD_RESOURCE_ID,
)
from src.collectors.installed_egress_client import (
    DeadlineExhaustedError,
    InstalledEgressClient,
)
from src.collectors.market_turnover_collector import MarketTurnoverCollector
from src.collectors.twse_isin_classification_collector import (
    TWSE_ISIN_CLASSIFICATION_RESOURCE_ID,
    parse_twse_isin_classification,
)
from src.collectors.universe_collectors import payload_fingerprint
from src.domain.data_foundation import sha256_text
from src.domain.installed_data_operations import (
    INSTALLED_OPERATIONS_ALLOWED_RESOURCES,
    InstalledItemStatus,
    InstalledOperationError,
    InstalledOperationStage,
    InstalledOperationStatus,
    InstalledOperationType,
    InstalledWriteAuthorization,
    OperationActiveConflict,
    OperationAuthorizationRevoked,
    OperationCancelled,
    OperationDeadlineExhausted,
    OperationNotFound,
    OperationStateInvalid,
)
from src.domain.universe import validate_official_code
from src.domain.valuation import utc_now_timestamp
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.services.eod_close_ingestion_service import EodCloseIngestionService
from src.services.production_ingestion_service import ProductionIngestionService
from src.services.universe_ingestion_service import UniverseIngestionService
from src.services.universe_write_guard import (
    UniverseOperatorContext,
    UniverseWriteGuard,
)


class InstalledDataSyncService:
    """Orchestrator for multi-stage Layer 1 local data operations."""

    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        egress_client: InstalledEgressClient | None = None,
        runtime_instance_id: str | None = None,
        operation_repo: InstalledDataOperationsRepository | None = None,
        eod_service: EodCloseIngestionService | None = None,
        universe_service: UniverseIngestionService | None = None,
        production_service: ProductionIngestionService | None = None,
    ) -> None:
        self.db_path = db_path
        self.egress_client = egress_client or InstalledEgressClient()
        self.runtime_instance_id = runtime_instance_id or "default_instance"
        self.operation_repo = operation_repo or InstalledDataOperationsRepository(db_path)
        self._eod_service = eod_service
        self._universe_service = universe_service
        self._production_service = production_service

    def _verify_durable_running(
        self, operation_id: str, authorization: InstalledWriteAuthorization
    ) -> None:
        """Verify the operation row in DB is still in running state before mutation."""
        row = self.operation_repo.get_operation_by_id(operation_id)
        if row is None:
            authorization.revoke()
            raise OperationNotFound(f"Operation {operation_id} not found")

        if row.status == "cancelling" or row.status == "cancelled":
            authorization.revoke()
            raise OperationCancelled(f"Operation {operation_id} was cancelled")

        if row.status != InstalledOperationStatus.RUNNING.value:
            authorization.revoke()
            raise OperationStateInvalid(f"Operation {operation_id} is in non-running state: {row.status}")

        if row.lease_owner_id != authorization.instance_id:
            authorization.revoke()
            raise OperationAuthorizationRevoked(f"Lease owner mismatch: {row.lease_owner_id} != {authorization.instance_id}")

        try:
            expiry = datetime.fromisoformat(row.lease_expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                authorization.revoke()
                raise OperationDeadlineExhausted(f"Operation lease expired at {row.lease_expires_at}")
        except (ValueError, TypeError) as exc:
            authorization.revoke()
            raise OperationStateInvalid(f"Invalid lease_expires_at: {row.lease_expires_at}") from exc

    def create_operation_and_capability(
        self,
        operation_type: str = InstalledOperationType.SYNC.value,
        target_symbols: Sequence[str] | None = None,
        lease_duration_seconds: int = 60,
    ) -> tuple[str, InstalledWriteAuthorization]:
        active = self.operation_repo.get_active_operation()
        if active is not None:
            raise OperationActiveConflict(f"Another operation {active.operation_id} is currently {active.status}")

        operation_id = f"op_{uuid4().hex}"
        op_row = self.operation_repo.create_operation(
            operation_id=operation_id,
            operation_type=operation_type,
            lease_owner_id=self.runtime_instance_id,
            lease_duration_seconds=lease_duration_seconds,
            target_symbols=target_symbols,
        )

        auth = InstalledWriteAuthorization(
            operation_id=operation_id,
            instance_id=self.runtime_instance_id,
            lease_expires_at=op_row.lease_expires_at,
        )
        return operation_id, auth

    def run_stage_prerequisites_calendar(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        deadline_monotonic: float | None = None,
    ) -> None:
        self._verify_durable_running(operation_id, authorization)
        item_id = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_id,
            operation_id=operation_id,
            stage=InstalledOperationStage.PREREQUISITES_CALENDAR.value,
            resource_id="twse.trading-calendar",
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
                deadline_monotonic=deadline_monotonic,
            )
            payload = json.loads(body.decode("utf-8")) if body else []
            prod_svc = self._production_service or ProductionIngestionService(self.db_path)
            # Ingest calendar into foundation
            run = prod_svc.ingest_twse_calendar(
                payload=payload,
                actor_id=authorization.actor_id,
            )
            self.operation_repo.update_item(
                item_id=item_id,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None),
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=item_id,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

    def run_stage_universe(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        deadline_monotonic: float | None = None,
    ) -> None:
        new_lease = self.operation_repo.transition_stage(
            operation_id, InstalledOperationStage.UNIVERSE.value, lease_duration_seconds=60
        )
        authorization.refresh_lease(new_lease)
        self._verify_durable_running(operation_id, authorization)

        # TWSE universe
        twse_item_id = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=twse_item_id,
            operation_id=operation_id,
            stage=InstalledOperationStage.UNIVERSE.value,
            resource_id="twse.t187ap03_L",
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                deadline_monotonic=deadline_monotonic,
            )
            self.operation_repo.update_item(
                item_id=twse_item_id,
                status=InstalledItemStatus.ACCEPTED.value,
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=twse_item_id,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

    def run_stage_classification(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        target_symbols: Sequence[str] | None = None,
        deadline_monotonic: float | None = None,
    ) -> None:
        new_lease = self.operation_repo.transition_stage(
            operation_id, InstalledOperationStage.CLASSIFICATION.value, lease_duration_seconds=120
        )
        authorization.refresh_lease(new_lease)
        self._verify_durable_running(operation_id, authorization)

        symbols = list(target_symbols or ["2330.TW"])
        eod_svc = self._eod_service or EodCloseIngestionService(
            self.db_path,
            authorization=authorization,
            active_instance_id=self.runtime_instance_id,
        )

        for sym in symbols:
            self._verify_durable_running(operation_id, authorization)
            code = sym.split(".")[0]
            venue = "TPEX" if sym.endswith(".TWO") else "TWSE"
            expected_market = "上櫃" if venue == "TPEX" else "上市"

            item_id = f"item_{uuid4().hex}"
            self.operation_repo.create_item(
                item_id=item_id,
                operation_id=operation_id,
                stage=InstalledOperationStage.CLASSIFICATION.value,
                resource_id=TWSE_ISIN_CLASSIFICATION_RESOURCE_ID,
            )

            # Pacing / rate-limit for ISIN
            time.sleep(0.3)
            try:
                status_code, body, _ = self.egress_client.fetch(
                    f"https://isin.twse.com.tw/isin/single_main.jsp?owncode={code}",
                    deadline_monotonic=deadline_monotonic,
                )
                html_text = body.decode("utf-8", errors="replace")
                ingest_res = eod_svc.ingest_classification_html(
                    official_code=code,
                    html=html_text,
                    venue=venue,
                    trade_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    actor_id=authorization.actor_id,
                )
                state = ingest_res.get("state", "accepted")
                self.operation_repo.update_item(
                    item_id=item_id,
                    status=InstalledItemStatus.ACCEPTED.value if state == "accepted" else InstalledItemStatus.PARTIAL.value,
                    raw_resource_revision_id=ingest_res.get("raw_resource_revision_id"),
                )
            except Exception as exc:
                self.operation_repo.update_item(
                    item_id=item_id,
                    status=InstalledItemStatus.FAILED.value,
                    error_detail=str(exc),
                )
                raise

    def run_stage_eod(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        deadline_monotonic: float | None = None,
    ) -> None:
        new_lease = self.operation_repo.transition_stage(
            operation_id, InstalledOperationStage.EOD.value, lease_duration_seconds=60
        )
        authorization.refresh_lease(new_lease)
        self._verify_durable_running(operation_id, authorization)

        eod_svc = self._eod_service or EodCloseIngestionService(
            self.db_path,
            authorization=authorization,
            active_instance_id=self.runtime_instance_id,
        )

        # TWSE EOD
        twse_item = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=twse_item,
            operation_id=operation_id,
            stage=InstalledOperationStage.EOD.value,
            resource_id=TWSE_EOD_RESOURCE_ID,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                deadline_monotonic=deadline_monotonic,
            )
            payload = json.loads(body.decode("utf-8")) if body else []
            eod_res = eod_svc.ingest_price_payload(
                venue="TWSE",
                payload=payload,
                actor_id=authorization.actor_id,
            )
            self.operation_repo.update_item(
                item_id=twse_item,
                status=InstalledItemStatus.ACCEPTED.value,
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=twse_item,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

    def run_stage_turnover_and_cbc(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        deadline_monotonic: float | None = None,
    ) -> None:
        new_lease = self.operation_repo.transition_stage(
            operation_id, InstalledOperationStage.TURNOVER_AND_CBC.value, lease_duration_seconds=60
        )
        authorization.refresh_lease(new_lease)
        self._verify_durable_running(operation_id, authorization)

        item_turnover = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_turnover,
            operation_id=operation_id,
            stage=InstalledOperationStage.TURNOVER_AND_CBC.value,
            resource_id="twse.market-turnover",
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
                deadline_monotonic=deadline_monotonic,
            )
            self.operation_repo.update_item(
                item_id=item_turnover,
                status=InstalledItemStatus.ACCEPTED.value,
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=item_turnover,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

    def run_stage_projection(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
    ) -> None:
        self.operation_repo.transition_stage(
            operation_id, InstalledOperationStage.PROJECTION.value, lease_duration_seconds=60
        )
        self._verify_durable_running(operation_id, authorization)

        items = self.operation_repo.list_items_by_operation(operation_id)
        has_failed = any(it.status == InstalledItemStatus.FAILED.value for it in items)
        has_partial = any(it.status == InstalledItemStatus.PARTIAL.value for it in items)

        final_status = (
            InstalledOperationStatus.FAILED.value
            if has_failed
            else InstalledOperationStatus.PARTIAL.value
            if has_partial
            else InstalledOperationStatus.SUCCEEDED.value
        )

        self.operation_repo.finalize_operation(operation_id, status=final_status)
        authorization.revoke()

    def execute_sync(
        self,
        operation_type: str = InstalledOperationType.SYNC.value,
        target_symbols: Sequence[str] | None = None,
        deadline_seconds: float = 300.0,
    ) -> str:
        operation_id, auth = self.create_operation_and_capability(
            operation_type=operation_type,
            target_symbols=target_symbols,
        )
        deadline_monotonic = time.monotonic() + deadline_seconds

        try:
            self.run_stage_prerequisites_calendar(operation_id, auth, deadline_monotonic)
            self.run_stage_universe(operation_id, auth, deadline_monotonic)
            self.run_stage_classification(operation_id, auth, target_symbols, deadline_monotonic)
            self.run_stage_eod(operation_id, auth, deadline_monotonic)
            self.run_stage_turnover_and_cbc(operation_id, auth, deadline_monotonic)
            self.run_stage_projection(operation_id, auth)
            return operation_id
        except Exception as exc:
            auth.revoke()
            self.operation_repo.finalize_operation(
                operation_id,
                status=InstalledOperationStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise
