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
)
from src.collectors.universe_collectors import (
    parse_universe_payload,
)
from src.domain.data_foundation import (
    DataHealthStatus,
    EligibilityStatus,
    IngestionItemStatus,
    IngestionRun,
    IngestionRunItem,
    IngestionRunStatus,
    RawResourceRevision,
    StoragePolicy,
    TriggerType,
    sha256_text,
)
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
from src.domain.valuation import utc_now_timestamp
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.services.eod_close_ingestion_service import EodCloseIngestionService
from src.services.production_ingestion_service import ProductionIngestionService
from src.services.universe_ingestion_service import UniverseIngestionService

GLOBAL_OPERATION_DEADLINE_SECONDS = 90.0


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

    def _require_live_write_authorization(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        resource_id: str,
    ) -> None:
        """Enforces durable row validation immediately before every protected mutation."""
        if authorization is None or authorization.revoked:
            raise OperationAuthorizationRevoked("capability_revoked_or_missing")

        row = self.operation_repo.get_operation_by_id(operation_id)
        if row is None:
            authorization.revoke()
            raise OperationNotFound(f"Operation {operation_id} not found")

        if row.status in ("cancelling", "cancelled"):
            authorization.revoke()
            raise OperationCancelled(f"Operation {operation_id} was cancelled")

        if row.status != InstalledOperationStatus.RUNNING.value:
            authorization.revoke()
            raise OperationStateInvalid(
                f"Operation {operation_id} is in non-running state: {row.status}"
            )

        if row.lease_owner_id != authorization.instance_id:
            authorization.revoke()
            raise OperationAuthorizationRevoked(
                f"Lease owner mismatch: {row.lease_owner_id} != {authorization.instance_id}"
            )

        try:
            expiry = datetime.fromisoformat(row.lease_expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                authorization.revoke()
                raise OperationDeadlineExhausted(
                    f"Operation lease expired at {row.lease_expires_at}"
                )
        except (ValueError, TypeError) as exc:
            authorization.revoke()
            raise OperationStateInvalid(f"Invalid lease_expires_at: {row.lease_expires_at}") from exc

        if resource_id not in INSTALLED_OPERATIONS_ALLOWED_RESOURCES:
            authorization.revoke()
            raise OperationAuthorizationRevoked(f"Resource {resource_id} not authorized")

        if resource_id not in authorization.allowed_resource_ids:
            authorization.revoke()
            raise OperationAuthorizationRevoked(
                f"Resource {resource_id} not in capability allowlist"
            )

        # Synchronize in-memory capability with durable lease
        authorization.refresh_lease(row.lease_expires_at)

    def _verify_durable_running(
        self, operation_id: str, authorization: InstalledWriteAuthorization
    ) -> None:
        """Backward-compatible alias for _require_live_write_authorization."""
        self._require_live_write_authorization(operation_id, authorization, "twse.trading-calendar")

    def create_operation_and_capability(
        self,
        operation_type: str = InstalledOperationType.SYNC.value,
        target_symbols: Sequence[str] | None = None,
        lease_duration_seconds: int = 60,
    ) -> tuple[str, InstalledWriteAuthorization]:
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
        resource_id = "twse.trading-calendar"
        self._require_live_write_authorization(operation_id, authorization, resource_id)

        item_id = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_id,
            operation_id=operation_id,
            stage=InstalledOperationStage.PREREQUISITES_CALENDAR.value,
            resource_id=resource_id,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
                deadline_monotonic=deadline_monotonic,
            )
            payload = json.loads(body.decode("utf-8")) if body else []
            prod_svc = self._production_service or ProductionIngestionService(self.db_path)
            run = prod_svc.ingest_twse_calendar(
                payload=payload,
                actor_id=authorization.actor_id,
            )
            revisions = run.get("calendar_revisions") or []
            items = run.get("items") or []
            child_run_id = run.get("run_id")
            child_item_id = (
                items[0]["ingestion_run_item_id"]
                if items and isinstance(items[0], dict)
                else getattr(items[0], "ingestion_run_item_id", None)
                if items
                else None
            )
            raw_rev_id = (
                revisions[0].get("raw_resource_revision_id")
                if revisions and isinstance(revisions[0], dict)
                else getattr(revisions[0], "raw_resource_revision_id", None)
                if revisions
                else None
            )

            self.operation_repo.update_item(
                item_id=item_id,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw_rev_id,
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
            operation_id,
            InstalledOperationStage.UNIVERSE.value,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)
        foundation = DataFoundationRepository(self.db_path)

        # 1. TWSE Universe
        twse_resource = "twse-universe-master"
        self._require_live_write_authorization(operation_id, authorization, twse_resource)
        twse_item_id = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=twse_item_id,
            operation_id=operation_id,
            stage=InstalledOperationStage.UNIVERSE.value,
            resource_id=twse_resource,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8"))
            payload = json.loads(body.decode("utf-8")) if body else []
            rows = parse_universe_payload("twse.t187ap03_L", payload)

            child_run_id = f"run_twse_univ_{uuid4().hex[:10]}"
            child_run = IngestionRun(
                ingestion_run_id=child_run_id,
                started_at=utc_now_timestamp(),
                trigger_type=TriggerType.MANUAL,
                runner_version="phase19-universe-sync-v1",
                requested_resources=(twse_resource,),
                actor_id=authorization.actor_id,
            )
            foundation.add_run(child_run)
            foundation.acquire_resource_lock(twse_resource, child_run_id, child_run.started_at)
            now = utc_now_timestamp()
            raw = foundation.add_raw_revision(
                RawResourceRevision(
                    raw_resource_revision_id=f"raw_twse_univ_{uuid4().hex[:10]}",
                    provider_id="twse-universe-official",
                    resource_id=twse_resource,
                    logical_revision_key="twse.t187ap03_L:latest",
                    source_published_at=None,
                    available_at=now,
                    received_at=now,
                    ingested_at=now,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("phase13"),
                    storage_policy=StoragePolicy.ARCHIVE_RAW,
                    quality_status=DataHealthStatus.FRESH,
                    eligibility_status=EligibilityStatus.ELIGIBLE,
                )
            )
            child_item_id = f"item_twse_univ_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="twse",
                    resource_id=twse_resource,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=IngestionItemStatus.ACCEPTED,
                    quality_status=DataHealthStatus.FRESH,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("phase13"),
                    record_count=len(rows),
                    accepted_count=len(rows),
                    rejected_count=0,
                )
            )
            foundation.complete_run(
                IngestionRun(
                    ingestion_run_id=child_run_id,
                    started_at=child_run.started_at,
                    completed_at=now,
                    trigger_type=child_run.trigger_type,
                    runner_version=child_run.runner_version,
                    requested_resources=child_run.requested_resources,
                    actor_id=child_run.actor_id,
                    status=IngestionRunStatus.SUCCEEDED,
                )
            )
            foundation.release_resource_lock(twse_resource, child_run_id)

            self.operation_repo.update_item(
                item_id=twse_item_id,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=twse_item_id,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

        # 2. TPEx Universe
        tpex_resource = "tpex-universe-master"
        self._require_live_write_authorization(operation_id, authorization, tpex_resource)
        tpex_item_id = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=tpex_item_id,
            operation_id=operation_id,
            stage=InstalledOperationStage.UNIVERSE.value,
            resource_id=tpex_resource,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8"))
            payload = json.loads(body.decode("utf-8")) if body else []
            rows = parse_universe_payload("tpex.mopsfin_t187ap03_O", payload)

            child_run_id = f"run_tpex_univ_{uuid4().hex[:10]}"
            child_run = IngestionRun(
                ingestion_run_id=child_run_id,
                started_at=utc_now_timestamp(),
                trigger_type=TriggerType.MANUAL,
                runner_version="phase19-universe-sync-v1",
                requested_resources=(tpex_resource,),
                actor_id=authorization.actor_id,
            )
            foundation.add_run(child_run)
            foundation.acquire_resource_lock(tpex_resource, child_run_id, child_run.started_at)
            now = utc_now_timestamp()
            raw = foundation.add_raw_revision(
                RawResourceRevision(
                    raw_resource_revision_id=f"raw_tpex_univ_{uuid4().hex[:10]}",
                    provider_id="tpex-universe-official",
                    resource_id=tpex_resource,
                    logical_revision_key="tpex.mopsfin_t187ap03_O:latest",
                    source_published_at=None,
                    available_at=now,
                    received_at=now,
                    ingested_at=now,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("phase13"),
                    storage_policy=StoragePolicy.ARCHIVE_RAW,
                    quality_status=DataHealthStatus.FRESH,
                    eligibility_status=EligibilityStatus.ELIGIBLE,
                )
            )
            child_item_id = f"item_tpex_univ_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="tpex",
                    resource_id=tpex_resource,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=IngestionItemStatus.ACCEPTED,
                    quality_status=DataHealthStatus.FRESH,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("phase13"),
                    record_count=len(rows),
                    accepted_count=len(rows),
                    rejected_count=0,
                )
            )
            foundation.complete_run(
                IngestionRun(
                    ingestion_run_id=child_run_id,
                    started_at=child_run.started_at,
                    completed_at=now,
                    trigger_type=child_run.trigger_type,
                    runner_version=child_run.runner_version,
                    requested_resources=child_run.requested_resources,
                    actor_id=child_run.actor_id,
                    status=IngestionRunStatus.SUCCEEDED,
                )
            )
            foundation.release_resource_lock(tpex_resource, child_run_id)

            self.operation_repo.update_item(
                item_id=tpex_item_id,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=tpex_item_id,
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
            operation_id,
            InstalledOperationStage.CLASSIFICATION.value,
            lease_duration_seconds=120,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)

        if target_symbols and len(target_symbols) > 0:
            symbols = [str(s).strip().upper() for s in target_symbols][:10]
        else:
            with self.operation_repo._get_connection() as conn:
                try:
                    rows = conn.execute(
                        "SELECT DISTINCT symbol FROM research_workflow_items ORDER BY symbol LIMIT 10"
                    ).fetchall()
                    symbols = [str(r[0]) for r in rows]
                except Exception:
                    symbols = []

        if not symbols:
            # Zero targets is valid and non-blocking
            return

        eod_svc = self._eod_service or EodCloseIngestionService(
            self.db_path,
            authorization=authorization,
            active_instance_id=self.runtime_instance_id,
        )

        for sym in symbols:
            self._require_live_write_authorization(
                operation_id, authorization, TWSE_ISIN_CLASSIFICATION_RESOURCE_ID
            )
            code = sym.split(".")[0]
            venue = "TPEX" if sym.endswith(".TWO") else "TWSE"

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
                    max_retries=1,
                    deadline_monotonic=deadline_monotonic,
                )
                try:
                    html_text = body.decode("ms950")
                except UnicodeDecodeError:
                    html_text = body.decode("utf-8", errors="replace")
                ingest_res = eod_svc.ingest_classification_html(
                    official_code=code,
                    html=html_text,
                    venue=venue,
                    trade_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    actor_id=authorization.actor_id,
                )
                state = ingest_res.get("classification_state", "accepted")
                self.operation_repo.update_item(
                    item_id=item_id,
                    status=(
                        InstalledItemStatus.ACCEPTED.value
                        if state == "accepted"
                        else InstalledItemStatus.PARTIAL.value
                    ),
                    ingestion_run_id=ingest_res.get("run_id"),
                    ingestion_run_item_id=ingest_res.get("item_id"),
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
            operation_id,
            InstalledOperationStage.EOD.value,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)

        eod_svc = self._eod_service or EodCloseIngestionService(
            self.db_path,
            authorization=authorization,
            active_instance_id=self.runtime_instance_id,
        )

        # 1. TWSE EOD
        self._require_live_write_authorization(operation_id, authorization, TWSE_EOD_RESOURCE_ID)
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
                ingestion_run_id=eod_res.get("run_id"),
                ingestion_run_item_id=eod_res.get("item_id"),
                raw_resource_revision_id=eod_res.get("raw_resource_revision_id"),
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=twse_item,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

        # 2. TPEx EOD
        self._require_live_write_authorization(operation_id, authorization, TPEX_EOD_RESOURCE_ID)
        tpex_item = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=tpex_item,
            operation_id=operation_id,
            stage=InstalledOperationStage.EOD.value,
            resource_id=TPEX_EOD_RESOURCE_ID,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                deadline_monotonic=deadline_monotonic,
            )
            payload = json.loads(body.decode("utf-8")) if body else []
            eod_res = eod_svc.ingest_price_payload(
                venue="TPEX",
                payload=payload,
                actor_id=authorization.actor_id,
            )
            self.operation_repo.update_item(
                item_id=tpex_item,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=eod_res.get("run_id"),
                ingestion_run_item_id=eod_res.get("item_id"),
                raw_resource_revision_id=eod_res.get("raw_resource_revision_id"),
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=tpex_item,
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
            operation_id,
            InstalledOperationStage.TURNOVER_AND_CBC.value,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)
        foundation = DataFoundationRepository(self.db_path)

        # 1. TWSE Turnover
        twse_turnover_res = "twse.market-turnover"
        self._require_live_write_authorization(operation_id, authorization, twse_turnover_res)
        item_twse_turnover = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_twse_turnover,
            operation_id=operation_id,
            stage=InstalledOperationStage.TURNOVER_AND_CBC.value,
            resource_id=twse_turnover_res,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8"))
            child_run_id = f"run_twse_turn_{uuid4().hex[:10]}"
            child_run = IngestionRun(
                ingestion_run_id=child_run_id,
                started_at=utc_now_timestamp(),
                trigger_type=TriggerType.MANUAL,
                runner_version="phase19-turnover-v1",
                requested_resources=(twse_turnover_res,),
                actor_id=authorization.actor_id,
            )
            foundation.add_run(child_run)
            foundation.acquire_resource_lock(twse_turnover_res, child_run_id, child_run.started_at)
            now = utc_now_timestamp()
            raw = foundation.add_raw_revision(
                RawResourceRevision(
                    raw_resource_revision_id=f"raw_twse_turn_{uuid4().hex[:10]}",
                    provider_id="twse",
                    resource_id=twse_turnover_res,
                    logical_revision_key=f"{twse_turnover_res}:latest",
                    source_published_at=None,
                    available_at=now,
                    received_at=now,
                    ingested_at=now,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                    storage_policy=StoragePolicy.ARCHIVE_NORMALIZED,
                    quality_status=DataHealthStatus.FRESH,
                    eligibility_status=EligibilityStatus.ELIGIBLE,
                )
            )
            child_item_id = f"item_twse_turn_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="twse",
                    resource_id=twse_turnover_res,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=IngestionItemStatus.ACCEPTED,
                    quality_status=DataHealthStatus.FRESH,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                )
            )
            foundation.complete_run(
                IngestionRun(
                    ingestion_run_id=child_run_id,
                    started_at=child_run.started_at,
                    completed_at=now,
                    trigger_type=child_run.trigger_type,
                    runner_version=child_run.runner_version,
                    requested_resources=child_run.requested_resources,
                    actor_id=child_run.actor_id,
                    status=IngestionRunStatus.SUCCEEDED,
                )
            )
            foundation.release_resource_lock(twse_turnover_res, child_run_id)

            self.operation_repo.update_item(
                item_id=item_twse_turnover,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=item_twse_turnover,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

        # 2. TPEx Turnover
        tpex_turnover_res = "tpex.market-turnover"
        self._require_live_write_authorization(operation_id, authorization, tpex_turnover_res)
        item_tpex_turnover = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_tpex_turnover,
            operation_id=operation_id,
            stage=InstalledOperationStage.TURNOVER_AND_CBC.value,
            resource_id=tpex_turnover_res,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8"))
            child_run_id = f"run_tpex_turn_{uuid4().hex[:10]}"
            child_run = IngestionRun(
                ingestion_run_id=child_run_id,
                started_at=utc_now_timestamp(),
                trigger_type=TriggerType.MANUAL,
                runner_version="phase19-turnover-v1",
                requested_resources=(tpex_turnover_res,),
                actor_id=authorization.actor_id,
            )
            foundation.add_run(child_run)
            foundation.acquire_resource_lock(tpex_turnover_res, child_run_id, child_run.started_at)
            now = utc_now_timestamp()
            raw = foundation.add_raw_revision(
                RawResourceRevision(
                    raw_resource_revision_id=f"raw_tpex_turn_{uuid4().hex[:10]}",
                    provider_id="tpex",
                    resource_id=tpex_turnover_res,
                    logical_revision_key=f"{tpex_turnover_res}:latest",
                    source_published_at=None,
                    available_at=now,
                    received_at=now,
                    ingested_at=now,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                    storage_policy=StoragePolicy.ARCHIVE_NORMALIZED,
                    quality_status=DataHealthStatus.FRESH,
                    eligibility_status=EligibilityStatus.ELIGIBLE,
                )
            )
            child_item_id = f"item_tpex_turn_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="tpex",
                    resource_id=tpex_turnover_res,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=IngestionItemStatus.ACCEPTED,
                    quality_status=DataHealthStatus.FRESH,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                )
            )
            foundation.complete_run(
                IngestionRun(
                    ingestion_run_id=child_run_id,
                    started_at=child_run.started_at,
                    completed_at=now,
                    trigger_type=child_run.trigger_type,
                    runner_version=child_run.runner_version,
                    requested_resources=child_run.requested_resources,
                    actor_id=child_run.actor_id,
                    status=IngestionRunStatus.SUCCEEDED,
                )
            )
            foundation.release_resource_lock(tpex_turnover_res, child_run_id)

            self.operation_repo.update_item(
                item_id=item_tpex_turnover,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=item_tpex_turnover,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

        # 3. CBC M1B (Supplementary / Non-blocking)
        cbc_resource = "cbc.m1b"
        self._require_live_write_authorization(operation_id, authorization, cbc_resource)
        item_cbc = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_cbc,
            operation_id=operation_id,
            stage=InstalledOperationStage.TURNOVER_AND_CBC.value,
            resource_id=cbc_resource,
        )
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=EF15M01",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8"))
            child_run_id = f"run_cbc_m1b_{uuid4().hex[:10]}"
            child_run = IngestionRun(
                ingestion_run_id=child_run_id,
                started_at=utc_now_timestamp(),
                trigger_type=TriggerType.MANUAL,
                runner_version="phase19-cbc-v1",
                requested_resources=(cbc_resource,),
                actor_id=authorization.actor_id,
            )
            foundation.add_run(child_run)
            foundation.acquire_resource_lock(cbc_resource, child_run_id, child_run.started_at)
            now = utc_now_timestamp()
            raw = foundation.add_raw_revision(
                RawResourceRevision(
                    raw_resource_revision_id=f"raw_cbc_m1b_{uuid4().hex[:10]}",
                    provider_id="cbc",
                    resource_id=cbc_resource,
                    logical_revision_key=f"{cbc_resource}:latest",
                    source_published_at=None,
                    available_at=now,
                    received_at=now,
                    ingested_at=now,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                    storage_policy=StoragePolicy.ARCHIVE_NORMALIZED,
                    quality_status=DataHealthStatus.FRESH,
                    eligibility_status=EligibilityStatus.ELIGIBLE,
                )
            )
            child_item_id = f"item_cbc_m1b_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="cbc",
                    resource_id=cbc_resource,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=IngestionItemStatus.ACCEPTED,
                    quality_status=DataHealthStatus.FRESH,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                )
            )
            foundation.complete_run(
                IngestionRun(
                    ingestion_run_id=child_run_id,
                    started_at=child_run.started_at,
                    completed_at=now,
                    trigger_type=child_run.trigger_type,
                    runner_version=child_run.runner_version,
                    requested_resources=child_run.requested_resources,
                    actor_id=child_run.actor_id,
                    status=IngestionRunStatus.SUCCEEDED,
                )
            )
            foundation.release_resource_lock(cbc_resource, child_run_id)

            self.operation_repo.update_item(
                item_id=item_cbc,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
            )
        except Exception as exc:
            # Non-blocking
            self.operation_repo.update_item(
                item_id=item_cbc,
                status=InstalledItemStatus.PARTIAL.value,
                error_detail=str(exc),
            )

    def run_stage_projection(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        deadline_monotonic: float | None = None,
    ) -> None:
        self.operation_repo.transition_stage(
            operation_id,
            InstalledOperationStage.PROJECTION.value,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        self._require_live_write_authorization(operation_id, authorization, "twse.trading-calendar")

        # Positive Domain Proof: verify that positive evidence exists in DB
        with self.operation_repo._get_connection() as conn:
            calendar_ok = conn.execute(
                "SELECT COUNT(*) FROM trading_calendar_revisions"
            ).fetchone()[0] > 0
            eod_ok = conn.execute(
                "SELECT COUNT(*) FROM eod_close_source_snapshots WHERE source_trade_date_status = 'valid'"
            ).fetchone()[0] > 0
            universe_ok = conn.execute(
                "SELECT COUNT(*) FROM raw_resource_revisions WHERE resource_id IN ('twse.t187ap03_L', 'twse-universe-master')"
            ).fetchone()[0] > 0

        items = self.operation_repo.list_items_by_operation(operation_id)
        has_failed = any(
            it.status == InstalledItemStatus.FAILED.value
            for it in items
            if it.resource_id != "cbc.m1b"
        )
        has_partial = any(it.status == InstalledItemStatus.PARTIAL.value for it in items)

        if has_failed or not (calendar_ok or universe_ok or eod_ok):
            final_status = InstalledOperationStatus.FAILED.value
        elif has_partial or not (calendar_ok and eod_ok):
            final_status = InstalledOperationStatus.PARTIAL.value
        else:
            final_status = InstalledOperationStatus.SUCCEEDED.value

        self.operation_repo.finalize_operation(operation_id, status=final_status)
        authorization.revoke()

    def run_symbol_enablement_pipeline(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        symbol: str,
        deadline_monotonic: float | None = None,
    ) -> None:
        """Executes the full approved symbol enablement flow:

        classify -> refetch exact official venue EOD -> validate session ->
        reload identity/classification -> governed EOD materialization (Phase 14) -> readiness refresh
        """
        clean_sym = symbol.strip().upper()
        code = clean_sym.split(".")[0]
        venue = "TPEX" if clean_sym.endswith(".TWO") else "TWSE"
        venue_resource = TPEX_EOD_RESOURCE_ID if venue == "TPEX" else TWSE_EOD_RESOURCE_ID

        eod_svc = self._eod_service or EodCloseIngestionService(
            self.db_path,
            authorization=authorization,
            active_instance_id=self.runtime_instance_id,
        )

        # 1. Classify
        self._require_live_write_authorization(
            operation_id, authorization, TWSE_ISIN_CLASSIFICATION_RESOURCE_ID
        )
        isin_item = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=isin_item,
            operation_id=operation_id,
            stage=InstalledOperationStage.CLASSIFICATION.value,
            resource_id=TWSE_ISIN_CLASSIFICATION_RESOURCE_ID,
        )
        status_code, body, _ = self.egress_client.fetch(
            f"https://isin.twse.com.tw/isin/single_main.jsp?owncode={code}",
            max_retries=1,
            deadline_monotonic=deadline_monotonic,
        )
        try:
            html_text = body.decode("ms950")
        except UnicodeDecodeError:
            html_text = body.decode("utf-8", errors="replace")
        ingest_res = eod_svc.ingest_classification_html(
            official_code=code,
            html=html_text,
            venue=venue,
            trade_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            actor_id=authorization.actor_id,
        )
        state = ingest_res.get("classification_state", "accepted")
        self.operation_repo.update_item(
            item_id=isin_item,
            status=(
                InstalledItemStatus.ACCEPTED.value
                if state == "accepted"
                else InstalledItemStatus.PARTIAL.value
            ),
            ingestion_run_id=ingest_res.get("run_id"),
            ingestion_run_item_id=ingest_res.get("item_id"),
            raw_resource_revision_id=ingest_res.get("raw_resource_revision_id"),
        )

        # 2. Refetch exact official venue EOD
        eod_url = (
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
            if venue == "TPEX"
            else "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        )
        status_code, eod_body, _ = self.egress_client.fetch(
            eod_url,
            deadline_monotonic=deadline_monotonic,
        )
        eod_payload = json.loads(eod_body.decode("utf-8")) if eod_body else []

        # 3. Governed EOD materialization using approved Phase 14 path
        self._require_live_write_authorization(operation_id, authorization, venue_resource)
        eod_item = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=eod_item,
            operation_id=operation_id,
            stage=InstalledOperationStage.EOD.value,
            resource_id=venue_resource,
        )
        eod_res = eod_svc.ingest_price_payload(
            venue=venue,
            payload=eod_payload,
            actor_id=authorization.actor_id,
        )
        self.operation_repo.update_item(
            item_id=eod_item,
            status=InstalledItemStatus.ACCEPTED.value,
            ingestion_run_id=eod_res.get("run_id"),
            ingestion_run_item_id=eod_res.get("item_id"),
            raw_resource_revision_id=eod_res.get("raw_resource_revision_id"),
        )

        # 4. Readiness refresh & completion
        self.run_stage_projection(operation_id, authorization)

    def execute_sync(
        self,
        operation_type: str = InstalledOperationType.SYNC.value,
        target_symbols: Sequence[str] | None = None,
        deadline_seconds: float = GLOBAL_OPERATION_DEADLINE_SECONDS,
    ) -> str:
        effective_deadline = min(deadline_seconds, GLOBAL_OPERATION_DEADLINE_SECONDS)
        operation_id, auth = self.create_operation_and_capability(
            operation_type=operation_type,
            target_symbols=target_symbols,
        )
        deadline_monotonic = time.monotonic() + effective_deadline

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
