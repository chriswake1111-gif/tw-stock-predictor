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
    InstalledReadiness,
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
from src.collectors.cbc_collector import CBCCollector
from src.collectors.eod_close_collectors import (
    parse_tpex_snapshot,
    parse_twse_snapshot,
)
from src.domain.liquidity import MarketTurnoverObservation, M1BMonthlyObservation
from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.liquidity_repository import LiquidityRepository
from src.repositories.universe_repository import UniverseRepository
from src.services.installed_readiness_evaluator import evaluate_installed_readiness
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard

GLOBAL_OPERATION_DEADLINE_SECONDS = 90.0

CAPABILITY_TO_STORAGE_RESOURCE = {
    "twse.t187ap03_L": "twse-universe-master",
    "tpex.mopsfin_t187ap03_O": "tpex-universe-master",
}


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
        universe_repo = UniverseRepository(
            self.db_path,
            guard=UniverseWriteGuard(
                authorization=authorization,
                active_instance_id=authorization.instance_id,
            ),
        )

        # 1. TWSE Universe
        twse_resource = "twse.t187ap03_L"
        self._require_live_write_authorization(operation_id, authorization, twse_resource)
        twse_storage = CAPABILITY_TO_STORAGE_RESOURCE.get(twse_resource, twse_resource)
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
                requested_resources=(twse_storage,),
                actor_id=authorization.actor_id,
            )
            foundation.add_run(child_run)
            foundation.acquire_resource_lock(twse_storage, child_run_id, child_run.started_at)
            now = utc_now_timestamp()
            raw = foundation.add_raw_revision(
                RawResourceRevision(
                    raw_resource_revision_id=f"raw_twse_univ_{uuid4().hex[:10]}",
                    provider_id="twse-universe-official",
                    resource_id=twse_storage,
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

            # Materialize instruments, revisions, lifecycle and operational events in Universe domain
            u_ctx_twse = UniverseOperatorContext(
                actor_id=authorization.actor_id,
                run_id=child_run_id,
                lock_id=f"lock_{child_run_id}",
                audit_id=f"audit_{child_run_id}",
            )
            for r in rows:
                c = str(
                    r.get("official_code")
                    or r.get("code")
                    or r.get("公司代號")
                    or r.get("CompanyCode")
                    or ""
                ).strip()
                if not c:
                    continue
                n = str(
                    r.get("name")
                    or r.get("公司名稱")
                    or r.get("CompanyName")
                    or ""
                ).strip()
                try:
                    anchor = universe_repo.allocate_instrument(
                        venue="TWSE",
                        official_code=c,
                        source_identity=f"twse:{c}",
                        first_observed_at=now,
                        source_reference="twse.t187ap03_L",
                        context=u_ctx_twse,
                    )
                    universe_repo.add_revision(
                        instrument_id=anchor["instrument_id"],
                        resource_id="twse-universe-master",
                        logical_revision_key=f"twse:{c}:master",
                        revision_number=1,
                        payload={
                            "venue": "TWSE",
                            "official_code": c,
                            "canonical_symbol": f"{c}.TW",
                            "display_name": n,
                            "security_type": "股票",
                            "fetched_at": now,
                            "received_at": now,
                            "ingested_at": now,
                            "available_at": now,
                            "source_reference": "twse.t187ap03_L",
                            "status": "accepted",
                            "freshness_status": "current",
                            "freshness_mode": "official_cadence_window",
                            "current_complete": True,
                            "coverage_complete": True,
                            "raw_resource_revision_id": raw["raw_resource_revision_id"],
                            "raw_payload_sha256": raw_hash,
                        },
                        context=u_ctx_twse,
                        idempotency_key=f"univ-twse-{c}-{raw_hash[:8]}",
                    )
                    universe_repo.add_lifecycle_event(
                        instrument_id=anchor["instrument_id"],
                        event_type="listed",
                        available_at=now,
                        ingested_at=now,
                        source_reference="twse.t187ap03_L",
                        reason="initial_master_listing",
                        context=u_ctx_twse,
                    )
                    universe_repo.add_operational_event(
                        instrument_id=anchor["instrument_id"],
                        trading_state="normal",
                        available_at=now,
                        ingested_at=now,
                        source_reference="twse.t187ap03_L",
                        reason="initial_master_normal",
                        context=u_ctx_twse,
                    )
                except Exception as exc:
                    print(f"TWSE ROW EXC: {type(exc)} {exc}")

            child_item_id = f"item_twse_univ_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="twse",
                    resource_id=twse_storage,
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
            foundation.release_resource_lock(twse_storage, child_run_id)

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
        tpex_resource = "tpex.mopsfin_t187ap03_O"
        self._require_live_write_authorization(operation_id, authorization, tpex_resource)
        tpex_storage = CAPABILITY_TO_STORAGE_RESOURCE.get(tpex_resource, tpex_resource)
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
                requested_resources=(tpex_storage,),
                actor_id=authorization.actor_id,
            )
            foundation.add_run(child_run)
            foundation.acquire_resource_lock(tpex_storage, child_run_id, child_run.started_at)
            now = utc_now_timestamp()
            raw = foundation.add_raw_revision(
                RawResourceRevision(
                    raw_resource_revision_id=f"raw_tpex_univ_{uuid4().hex[:10]}",
                    provider_id="tpex-universe-official",
                    resource_id=tpex_storage,
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

            # Materialize TPEx instruments, revisions, lifecycle and operational events in Universe domain
            u_ctx_tpex = UniverseOperatorContext(
                actor_id=authorization.actor_id,
                run_id=child_run_id,
                lock_id=f"lock_{child_run_id}",
                audit_id=f"audit_{child_run_id}",
            )
            for r in rows:
                c = str(
                    r.get("official_code")
                    or r.get("code")
                    or r.get("SecuritiesCompanyCode")
                    or r.get("公司代號")
                    or ""
                ).strip()
                if not c:
                    continue
                n = str(
                    r.get("name")
                    or r.get("CompanyName")
                    or r.get("公司名稱")
                    or ""
                ).strip()
                try:
                    anchor = universe_repo.allocate_instrument(
                        venue="TPEX",
                        official_code=c,
                        source_identity=f"tpex:{c}",
                        first_observed_at=now,
                        source_reference="tpex.mopsfin_t187ap03_O",
                        context=u_ctx_tpex,
                    )
                    universe_repo.add_revision(
                        instrument_id=anchor["instrument_id"],
                        resource_id="tpex-universe-master",
                        logical_revision_key=f"tpex:{c}:master",
                        revision_number=1,
                        payload={
                            "venue": "TPEX",
                            "official_code": c,
                            "canonical_symbol": f"{c}.TWO",
                            "display_name": n,
                            "security_type": "股票",
                            "fetched_at": now,
                            "received_at": now,
                            "ingested_at": now,
                            "available_at": now,
                            "source_reference": "tpex.mopsfin_t187ap03_O",
                            "status": "accepted",
                            "freshness_status": "current",
                            "freshness_mode": "official_cadence_window",
                            "current_complete": True,
                            "coverage_complete": True,
                            "raw_resource_revision_id": raw["raw_resource_revision_id"],
                            "raw_payload_sha256": raw_hash,
                        },
                        context=u_ctx_tpex,
                        idempotency_key=f"univ-tpex-{c}-{raw_hash[:8]}",
                    )
                    universe_repo.add_lifecycle_event(
                        instrument_id=anchor["instrument_id"],
                        event_type="listed",
                        available_at=now,
                        ingested_at=now,
                        source_reference="tpex.mopsfin_t187ap03_O",
                        reason="initial_master_listing",
                        context=u_ctx_tpex,
                    )
                    universe_repo.add_operational_event(
                        instrument_id=anchor["instrument_id"],
                        trading_state="normal",
                        available_at=now,
                        ingested_at=now,
                        source_reference="tpex.mopsfin_t187ap03_O",
                        reason="initial_master_normal",
                        context=u_ctx_tpex,
                    )
                except Exception:
                    pass

            child_item_id = f"item_tpex_univ_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="tpex",
                    resource_id=tpex_storage,
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
            foundation.release_resource_lock(tpex_storage, child_run_id)

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
            lease_duration_seconds=60,
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
            # 1. TWSE Turnover
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash_twse = sha256_text(body.decode("utf-8"))
            twse_payload = json.loads(body.decode("utf-8")) if body else []
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
                    raw_payload_sha256=raw_hash_twse,
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
                    raw_payload_sha256=raw_hash_twse,
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
            raw_hash_tpex = sha256_text(body.decode("utf-8"))
            tpex_payload = json.loads(body.decode("utf-8")) if body else []
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
                    raw_payload_sha256=raw_hash_tpex,
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
                    raw_payload_sha256=raw_hash_tpex,
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

            # Materialize combined turnover observations into LiquidityRepository
            liquidity_repo = LiquidityRepository(self.db_path)
            dates_twse: dict[str, float] = {}
            for r in twse_payload:
                try:
                    d = MarketTurnoverCollector._iso_date(r.get("Date", ""))
                    val = float(str(r.get("TradeValue", 0)).replace(",", ""))
                    dates_twse[d] = val
                except Exception:
                    pass

            dates_tpex: dict[str, float] = {}
            for r in tpex_payload:
                try:
                    d = MarketTurnoverCollector._iso_date(r.get("Date", ""))
                    val = float(str(r.get("TradeAmount", 0)).replace(",", ""))
                    dates_tpex[d] = val
                except Exception:
                    pass

            for d in sorted(set(dates_twse.keys()) | set(dates_tpex.keys())):
                tw_val = dates_twse.get(d)
                tp_val = dates_tpex.get(d)
                try:
                    obs = MarketTurnoverObservation(
                        trade_date=d,
                        twse_turnover_twd=tw_val,
                        tpex_turnover_twd=tp_val,
                        twse_source="TWSE" if tw_val is not None else None,
                        tpex_source="TPEx" if tp_val is not None else None,
                        twse_dataset="exchangeReport/FMTQIK" if tw_val is not None else None,
                        tpex_dataset="tpex_daily_trading_index" if tp_val is not None else None,
                        twse_payload_hash=raw_hash_twse,
                        tpex_payload_hash=raw_hash_tpex,
                        available_at=now,
                        fetched_at=now,
                        revision=1,
                    )
                    liquidity_repo.add_turnover(obs)
                except Exception:
                    pass

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

            # Materialize CBC M1B into LiquidityRepository
            liquidity_repo = LiquidityRepository(self.db_path)
            try:
                cbc_payload = json.loads(body.decode("utf-8")) if body else {}
                data_rows = []
                official_data = cbc_payload.get("data", {})
                if isinstance(official_data, dict) and "dataSets" in official_data:
                    data_rows = official_data.get("dataSets", [])
                else:
                    data_rows = cbc_payload.get("result", {}).get("data", [])

                for r in data_rows:
                    if not r or not isinstance(r, list) or len(r) < 2:
                        continue
                    period_str = str(r[0]).strip()
                    if "M" not in period_str:
                        continue
                    period, data_date = CBCCollector._cbc_period(period_str)
                    val_str = str(r[1]).replace(",", "")
                    try:
                        val = float(val_str)
                    except ValueError:
                        continue
                    m1b_obs = M1BMonthlyObservation(
                        period=period,
                        value_raw=val,
                        raw_unit="TWD_million",
                        data_date=data_date,
                        available_at=now,
                        fetched_at=now,
                        source="CBC",
                        source_dataset="CBC EF15M01",
                        source_url="https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=EF15M01",
                        payload_hash=raw_hash,
                    )
                    try:
                        liquidity_repo.add_m1b(m1b_obs)
                    except Exception:
                        pass
            except Exception as cbc_parse_exc:
                logger.debug("CBC parsing error: %s", cbc_parse_exc)

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

        # Positive Domain Proof: verify that positive evidence exists in DB using authoritative evaluator
        with self.operation_repo._get_connection() as conn:
            readiness_enum, details = evaluate_installed_readiness(conn)

        items = self.operation_repo.list_items_by_operation(operation_id)
        has_failed = any(
            it.status == InstalledItemStatus.FAILED.value
            for it in items
            if it.resource_id != "cbc.m1b"
        )
        has_partial = any(it.status == InstalledItemStatus.PARTIAL.value for it in items)

        if has_failed or readiness_enum == InstalledReadiness.NOT_INITIALIZED:
            final_status = InstalledOperationStatus.FAILED.value
        elif has_partial or readiness_enum == InstalledReadiness.PARTIAL:
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

        # 3. Explicit session validation against calendar proof
        parsed_snap = parse_tpex_snapshot(eod_payload) if venue == "TPEX" else parse_twse_snapshot(eod_payload)
        trade_date = parsed_snap.source_trade_date
        if not trade_date:
            raise ValueError(f"eod payload trade date could not be determined: status={parsed_snap.status}, reason={parsed_snap.reason}")

        with self.operation_repo._get_connection() as conn:
            cal_row = conn.execute(
                "SELECT session_status FROM trading_calendar_revisions WHERE trade_date = ? AND status = 'available'",
                (trade_date,),
            ).fetchone()
            if cal_row and cal_row[0] != "trading":
                raise ValueError(f"source session {trade_date} is not an authorized trading session: status={cal_row[0]}")

        # 4. Reload persisted identity and classification proof
        now = utc_now_timestamp()
        universe_repo = UniverseRepository(self.db_path)
        canonical_sym = f"{code}.TWO" if venue == "TPEX" else f"{code}.TW"
        identity_context = universe_repo.identity_context_for_eod(
            canonical_symbol=canonical_sym,
            knowledge_cutoff_at=now,
        )
        eod_repo = EodCloseRepository(self.db_path)
        classification_context = eod_repo.latest_classification_for_code(code)

        # 5. Governed EOD materialization using approved Phase 14 path with bound contexts
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
            identity_by_code={code: identity_context} if identity_context else None,
            classification_by_code={code: classification_context} if classification_context else None,
        )
        self.operation_repo.update_item(
            item_id=eod_item,
            status=InstalledItemStatus.ACCEPTED.value,
            ingestion_run_id=eod_res.get("run_id"),
            ingestion_run_item_id=eod_res.get("item_id"),
            raw_resource_revision_id=eod_res.get("raw_resource_revision_id"),
        )

        # 6. Readiness refresh & completion
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
