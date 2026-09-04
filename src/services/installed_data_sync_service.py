"""Phase 19 Installed Data Synchronization / Local Data Operations V1 worker service."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
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
    TradingSessionStatus,
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
from src.repositories.universe_repository import (
    UniverseIdentityRepository,
    UniverseRepository,
)
from src.services.installed_readiness_evaluator import evaluate_installed_readiness
from src.services.universe_write_guard import (
    UniverseIngestionWritesDisabled,
    UniverseOperatorContext,
    UniverseWriteGuard,
)

GLOBAL_OPERATION_DEADLINE_SECONDS = 90.0

CAPABILITY_TO_STORAGE_RESOURCE = {
    "twse.t187ap03_L": "twse-universe-master",
    "tpex.mopsfin_t187ap03_O": "tpex-universe-master",
}


def _parse_source_listing_date(raw_val: Any) -> str | None:
    if not raw_val:
        return None
    s = str(raw_val).strip().replace("/", "").replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) == 7 and s.isdigit():
        year = int(s[:3]) + 1911
        return f"{year}-{s[3:5]}-{s[5:7]}"
    return None


class InstalledDataSyncService:
    """Orchestrates the approved Phase 19 six-stage installed synchronization pipeline."""

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

    def _extend_lease_if_needed(
        self,
        operation_id: str,
        authorization: InstalledWriteAuthorization,
        min_remaining_seconds: float = 30.0,
        lease_duration_seconds: int = 60,
    ) -> None:
        """Extends the durable lease if remaining time is below threshold."""
        if authorization is None or authorization.revoked:
            return
        try:
            expiry = datetime.fromisoformat(authorization.lease_expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            if remaining < min_remaining_seconds:
                new_lease = self.operation_repo.extend_lease(
                    operation_id,
                    lease_duration_seconds=lease_duration_seconds,
                    expected_owner_id=authorization.instance_id,
                )
                authorization.refresh_lease(new_lease)
        except Exception:
            pass

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
            payload = []
            last_exc: Exception | None = None
            for attempt in range(1, 6):
                try:
                    status_code, body, _ = self.egress_client.fetch(
                        "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
                        deadline_monotonic=deadline_monotonic,
                    )
                    text = body.decode("utf-8-sig", errors="replace").strip() if body else ""
                    if text and not text.startswith("<"):
                        candidate = json.loads(text)
                        if isinstance(candidate, list) and candidate:
                            payload = candidate
                            break
                        else:
                            last_exc = ValueError(
                                f"TWSE holiday schedule returned non-list or empty list: {type(candidate)}"
                            )
                    else:
                        preview = text[:60] if text else "empty"
                        last_exc = ValueError(
                            f"TWSE holiday schedule returned non-JSON payload: {preview}"
                        )
                except Exception as exc:
                    last_exc = exc
                if attempt < 5:
                    time.sleep(1.0 * attempt)
            else:
                if last_exc:
                    raise last_exc
                if not payload:
                    raise ValueError("TWSE holiday schedule returned empty payload")

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
        u_guard = UniverseWriteGuard(
            authorization=authorization,
            active_instance_id=authorization.instance_id,
        )
        universe_repo = UniverseRepository(self.db_path, guard=u_guard)
        identity_repo = UniverseIdentityRepository(self.db_path, guard=u_guard)
        universe_ingestion = UniverseIngestionService(self.db_path, guard=u_guard)

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
        child_run_id = f"run_twse_univ_{uuid4().hex[:10]}"
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8-sig", errors="replace"))
            payload = json.loads(body.decode("utf-8-sig", errors="replace")) if body else []
            rows = parse_universe_payload("twse.t187ap03_L", payload)

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
            twse_accepted = 0
            twse_rejected = 0
            twse_errors: list[str] = []
            for idx, r in enumerate(rows):
                if idx % 50 == 0:
                    self._extend_lease_if_needed(operation_id, authorization)
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
                    anchor = identity_repo.get_or_create(
                        venue="TWSE",
                        official_code=c,
                        source_identity=f"twse:{c}",
                        first_observed_at=now,
                        source_reference="twse.t187ap03_L",
                        context=u_ctx_twse,
                    )
                    universe_ingestion.ingest_revision(
                        context=u_ctx_twse,
                        idempotency_key=f"univ-twse-{c}-{raw['raw_resource_revision_id']}",
                        raw_resource_revision_id=raw["raw_resource_revision_id"],
                        raw_payload_sha256=raw_hash,
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
                    )
                    listing_d = _parse_source_listing_date(
                        r.get("上市日期") or r.get("listing_date") or r.get("DateOfListing")
                    )
                    eff_at = f"{listing_d}T00:00:00Z" if listing_d else None
                    universe_repo.add_lifecycle_event(
                        instrument_id=anchor["instrument_id"],
                        event_type="listed",
                        event_date=listing_d,
                        effective_at=eff_at,
                        available_at=now,
                        ingested_at=now,
                        source_reference="twse.t187ap03_L",
                        reason="initial_master_listing",
                        context=u_ctx_twse,
                    )
                    universe_repo.add_operational_event(
                        instrument_id=anchor["instrument_id"],
                        trading_state="normal",
                        effective_at=None,
                        available_at=now,
                        ingested_at=now,
                        source_reference="twse.t187ap03_L",
                        reason="initial_master_normal",
                        context=u_ctx_twse,
                    )
                    twse_accepted += 1
                except (
                    UniverseIngestionWritesDisabled,
                    OperationCancelled,
                    OperationDeadlineExhausted,
                    OperationAuthorizationRevoked,
                ):
                    raise
                except Exception as exc:
                    twse_rejected += 1
                    twse_errors.append(f"{c}: {exc}")

            if twse_rejected == 0 and twse_accepted > 0:
                twse_item_status = IngestionItemStatus.ACCEPTED
                twse_inst_status = InstalledItemStatus.ACCEPTED.value
                twse_run_status = IngestionRunStatus.SUCCEEDED
            elif twse_accepted > 0:
                twse_item_status = IngestionItemStatus.PARTIAL
                twse_inst_status = InstalledItemStatus.PARTIAL.value
                twse_run_status = IngestionRunStatus.PARTIAL
            else:
                twse_item_status = IngestionItemStatus.REJECTED
                twse_inst_status = InstalledItemStatus.FAILED.value
                twse_run_status = IngestionRunStatus.FAILED

            twse_reason = "; ".join(twse_errors[:5]) if twse_errors else ("ingestion rejected" if twse_rejected > 0 else None)
            child_item_id = f"item_twse_univ_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="twse",
                    resource_id=twse_storage,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=twse_item_status,
                    quality_status=DataHealthStatus.FRESH if twse_rejected == 0 else DataHealthStatus.PARTIAL,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("phase13"),
                    record_count=len(rows),
                    accepted_count=twse_accepted,
                    rejected_count=twse_rejected,
                    reason=twse_reason,
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
                    status=twse_run_status,
                )
            )
            foundation.release_resource_lock(twse_storage, child_run_id)

            self.operation_repo.update_item(
                item_id=twse_item_id,
                status=twse_inst_status,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
                error_detail="; ".join(twse_errors[:5]) if twse_errors else None,
            )
        except Exception as exc:
            try:
                foundation.release_resource_lock(twse_storage, child_run_id)
            except Exception:
                pass
            self.operation_repo.update_item(
                item_id=twse_item_id,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

        # 2. TPEx Universe
        new_lease = self.operation_repo.extend_lease(
            operation_id,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)

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
        child_run_id = f"run_tpex_univ_{uuid4().hex[:10]}"
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8-sig", errors="replace"))
            payload = json.loads(body.decode("utf-8-sig", errors="replace")) if body else []
            rows = parse_universe_payload("tpex.mopsfin_t187ap03_O", payload)

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
            tpex_accepted = 0
            tpex_rejected = 0
            tpex_errors: list[str] = []
            for idx, r in enumerate(rows):
                if idx % 50 == 0:
                    self._extend_lease_if_needed(operation_id, authorization)
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
                    anchor = identity_repo.get_or_create(
                        venue="TPEX",
                        official_code=c,
                        source_identity=f"tpex:{c}",
                        first_observed_at=now,
                        source_reference="tpex.mopsfin_t187ap03_O",
                        context=u_ctx_tpex,
                    )
                    universe_ingestion.ingest_revision(
                        context=u_ctx_tpex,
                        idempotency_key=f"univ-tpex-{c}-{raw['raw_resource_revision_id']}",
                        raw_resource_revision_id=raw["raw_resource_revision_id"],
                        raw_payload_sha256=raw_hash,
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
                    )
                    listing_d_tpex = _parse_source_listing_date(
                        r.get("DateOfListing") or r.get("listing_date") or r.get("上市日期")
                    )
                    eff_at_tpex = f"{listing_d_tpex}T00:00:00Z" if listing_d_tpex else None
                    universe_repo.add_lifecycle_event(
                        instrument_id=anchor["instrument_id"],
                        event_type="listed",
                        event_date=listing_d_tpex,
                        effective_at=eff_at_tpex,
                        available_at=now,
                        ingested_at=now,
                        source_reference="tpex.mopsfin_t187ap03_O",
                        reason="initial_master_listing",
                        context=u_ctx_tpex,
                    )
                    universe_repo.add_operational_event(
                        instrument_id=anchor["instrument_id"],
                        trading_state="normal",
                        effective_at=None,
                        available_at=now,
                        ingested_at=now,
                        source_reference="tpex.mopsfin_t187ap03_O",
                        reason="initial_master_normal",
                        context=u_ctx_tpex,
                    )
                    tpex_accepted += 1
                except (
                    UniverseIngestionWritesDisabled,
                    OperationCancelled,
                    OperationDeadlineExhausted,
                    OperationAuthorizationRevoked,
                ):
                    raise
                except Exception as exc:
                    tpex_rejected += 1
                    tpex_errors.append(f"{c}: {exc}")

            if tpex_rejected == 0 and tpex_accepted > 0:
                tpex_item_status = IngestionItemStatus.ACCEPTED
                tpex_inst_status = InstalledItemStatus.ACCEPTED.value
                tpex_run_status = IngestionRunStatus.SUCCEEDED
            elif tpex_accepted > 0:
                tpex_item_status = IngestionItemStatus.PARTIAL
                tpex_inst_status = InstalledItemStatus.PARTIAL.value
                tpex_run_status = IngestionRunStatus.PARTIAL
            else:
                tpex_item_status = IngestionItemStatus.REJECTED
                tpex_inst_status = InstalledItemStatus.FAILED.value
                tpex_run_status = IngestionRunStatus.FAILED

            tpex_reason = "; ".join(tpex_errors[:5]) if tpex_errors else ("ingestion rejected" if tpex_rejected > 0 else None)
            child_item_id = f"item_tpex_univ_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="tpex",
                    resource_id=tpex_storage,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=tpex_item_status,
                    quality_status=DataHealthStatus.FRESH if tpex_rejected == 0 else DataHealthStatus.PARTIAL,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("phase13"),
                    record_count=len(rows),
                    accepted_count=tpex_accepted,
                    rejected_count=tpex_rejected,
                    reason=tpex_reason,
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
                    status=tpex_run_status,
                )
            )
            foundation.release_resource_lock(tpex_storage, child_run_id)

            self.operation_repo.update_item(
                item_id=tpex_item_id,
                status=tpex_inst_status,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
                error_detail="; ".join(tpex_errors[:5]) if tpex_errors else None,
            )
        except Exception as exc:
            try:
                foundation.release_resource_lock(tpex_storage, child_run_id)
            except Exception:
                pass
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
                        "SELECT DISTINCT symbol FROM research_watchlist_items WHERE membership_state = 'active' ORDER BY symbol LIMIT 10"
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

        now = utc_now_timestamp()
        universe_repo = UniverseRepository(self.db_path)
        eod_repo = EodCloseRepository(self.db_path)

        with self.operation_repo._get_connection() as conn:
            classified_codes = {
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT official_code FROM eod_product_classification_evidence WHERE classification_state = 'accepted'"
                ).fetchall()
            }

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
            payload = json.loads(body.decode("utf-8-sig", errors="replace")) if body else []
            parsed_twse = parse_twse_snapshot(payload)
            snapshot_codes = {row.official_code for row in parsed_twse.rows}
            target_codes = classified_codes & snapshot_codes
            identity_by_code = {}
            classification_by_code = {}
            for code in target_codes:
                try:
                    ident = universe_repo.identity_context_for_eod(
                        canonical_symbol=f"{code}.TW",
                        knowledge_cutoff_at=now,
                    )
                    if ident:
                        identity_by_code[code] = ident
                except ValueError:
                    pass
                try:
                    classif = eod_repo.latest_classification_for_code(code)
                    if classif:
                        classification_by_code[code] = classif
                except ValueError:
                    pass

            eod_res = eod_svc.ingest_price_payload(
                venue="TWSE",
                payload=payload,
                actor_id=authorization.actor_id,
                identity_by_code=identity_by_code,
                classification_by_code=classification_by_code,
            )
            twse_raw_id = eod_res.get("raw_resource_revision_id")
            with sqlite3.connect(self.db_path) as conn:
                row_obs = conn.execute(
                    """
                    SELECT
                        COUNT(*),
                        SUM(CASE WHEN observation_status = 'available' AND public_eligibility_status = 'eligible' AND close_value IS NOT NULL THEN 1 ELSE 0 END),
                        SUM(CASE WHEN observation_status != 'available' OR public_eligibility_status != 'eligible' OR close_value IS NULL THEN 1 ELSE 0 END)
                    FROM eod_close_observations
                    WHERE raw_resource_revision_id = ?
                    """,
                    (twse_raw_id,),
                ).fetchone()
                total_obs = int(row_obs[0]) if row_obs and row_obs[0] else 0
                eligible_obs = int(row_obs[1]) if row_obs and row_obs[1] else 0
                unproven_obs = int(row_obs[2]) if row_obs and row_obs[2] else 0

                if target_codes:
                    placeholders = ",".join("?" for _ in target_codes)
                    row_t = conn.execute(
                        f"""
                        SELECT COUNT(DISTINCT official_code)
                        FROM eod_close_observations
                        WHERE raw_resource_revision_id = ?
                          AND observation_status = 'available'
                          AND public_eligibility_status = 'eligible'
                          AND close_value IS NOT NULL
                          AND official_code IN ({placeholders})
                        """,
                        (twse_raw_id, *target_codes),
                    ).fetchone()
                    target_eligible = int(row_t[0]) if row_t and row_t[0] else 0
                    if target_eligible == len(target_codes):
                        twse_status = InstalledItemStatus.ACCEPTED.value
                        twse_err = None
                    elif target_eligible > 0:
                        twse_status = InstalledItemStatus.PARTIAL.value
                        twse_err = f"target_eligible={target_eligible}/{len(target_codes)}"
                    else:
                        twse_status = InstalledItemStatus.FAILED.value
                        twse_err = f"target_eligible=0/{len(target_codes)}"
                else:
                    if eligible_obs > 0 and unproven_obs == 0:
                        twse_status = InstalledItemStatus.ACCEPTED.value
                        twse_err = None
                    elif eligible_obs > 0:
                        twse_status = InstalledItemStatus.PARTIAL.value
                        twse_err = f"eligible={eligible_obs}, unproven={unproven_obs}"
                    elif total_obs > 0:
                        twse_status = InstalledItemStatus.FAILED.value
                        twse_err = f"eligible=0, total={total_obs}"
                    else:
                        twse_status = InstalledItemStatus.ACCEPTED.value
                        twse_err = None

            self.operation_repo.update_item(
                item_id=twse_item,
                status=twse_status,
                ingestion_run_id=eod_res.get("run_id"),
                ingestion_run_item_id=eod_res.get("item_id"),
                raw_resource_revision_id=twse_raw_id,
                error_detail=twse_err,
            )
        except Exception as exc:
            self.operation_repo.update_item(
                item_id=twse_item,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

        # 2. TPEx EOD
        new_lease = self.operation_repo.extend_lease(
            operation_id,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)

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
            payload = json.loads(body.decode("utf-8-sig", errors="replace")) if body else []
            parsed_tpex = parse_tpex_snapshot(payload)
            snapshot_codes_tpex = {row.official_code for row in parsed_tpex.rows}
            target_codes_tpex = classified_codes & snapshot_codes_tpex
            identity_by_code_tpex = {}
            classification_by_code_tpex = {}
            for code in target_codes_tpex:
                try:
                    ident = universe_repo.identity_context_for_eod(
                        canonical_symbol=f"{code}.TWO",
                        knowledge_cutoff_at=now,
                    )
                    if ident:
                        identity_by_code_tpex[code] = ident
                except ValueError:
                    pass
                try:
                    classif = eod_repo.latest_classification_for_code(code)
                    if classif:
                        classification_by_code_tpex[code] = classif
                except ValueError:
                    pass

            eod_res = eod_svc.ingest_price_payload(
                venue="TPEX",
                payload=payload,
                actor_id=authorization.actor_id,
                identity_by_code=identity_by_code_tpex,
                classification_by_code=classification_by_code_tpex,
            )
            tpex_raw_id = eod_res.get("raw_resource_revision_id")
            with sqlite3.connect(self.db_path) as conn:
                row_obs = conn.execute(
                    """
                    SELECT
                        COUNT(*),
                        SUM(CASE WHEN observation_status = 'available' AND public_eligibility_status = 'eligible' AND close_value IS NOT NULL THEN 1 ELSE 0 END),
                        SUM(CASE WHEN observation_status != 'available' OR public_eligibility_status != 'eligible' OR close_value IS NULL THEN 1 ELSE 0 END)
                    FROM eod_close_observations
                    WHERE raw_resource_revision_id = ?
                    """,
                    (tpex_raw_id,),
                ).fetchone()
                total_obs = int(row_obs[0]) if row_obs and row_obs[0] else 0
                eligible_obs = int(row_obs[1]) if row_obs and row_obs[1] else 0
                unproven_obs = int(row_obs[2]) if row_obs and row_obs[2] else 0

                if target_codes_tpex:
                    placeholders = ",".join("?" for _ in target_codes_tpex)
                    row_t = conn.execute(
                        f"""
                        SELECT COUNT(DISTINCT official_code)
                        FROM eod_close_observations
                        WHERE raw_resource_revision_id = ?
                          AND observation_status = 'available'
                          AND public_eligibility_status = 'eligible'
                          AND close_value IS NOT NULL
                          AND official_code IN ({placeholders})
                        """,
                        (tpex_raw_id, *target_codes_tpex),
                    ).fetchone()
                    target_eligible = int(row_t[0]) if row_t and row_t[0] else 0
                    if target_eligible == len(target_codes_tpex):
                        tpex_status = InstalledItemStatus.ACCEPTED.value
                        tpex_err = None
                    elif target_eligible > 0:
                        tpex_status = InstalledItemStatus.PARTIAL.value
                        tpex_err = f"target_eligible={target_eligible}/{len(target_codes_tpex)}"
                    else:
                        tpex_status = InstalledItemStatus.FAILED.value
                        tpex_err = f"target_eligible=0/{len(target_codes_tpex)}"
                else:
                    if eligible_obs > 0 and unproven_obs == 0:
                        tpex_status = InstalledItemStatus.ACCEPTED.value
                        tpex_err = None
                    elif eligible_obs > 0:
                        tpex_status = InstalledItemStatus.PARTIAL.value
                        tpex_err = f"eligible={eligible_obs}, unproven={unproven_obs}"
                    elif total_obs > 0:
                        tpex_status = InstalledItemStatus.FAILED.value
                        tpex_err = f"eligible=0, total={total_obs}"
                    else:
                        tpex_status = InstalledItemStatus.ACCEPTED.value
                        tpex_err = None

            self.operation_repo.update_item(
                item_id=tpex_item,
                status=tpex_status,
                ingestion_run_id=eod_res.get("run_id"),
                ingestion_run_item_id=eod_res.get("item_id"),
                raw_resource_revision_id=tpex_raw_id,
                error_detail=tpex_err,
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
        child_run_twse_id = f"run_twse_turn_{uuid4().hex[:10]}"
        child_run_twse = IngestionRun(
            ingestion_run_id=child_run_twse_id,
            started_at=utc_now_timestamp(),
            trigger_type=TriggerType.MANUAL,
            runner_version="phase19-turnover-v1",
            requested_resources=(twse_turnover_res,),
            actor_id=authorization.actor_id,
        )
        raw_hash_twse = ""
        raw_twse: dict[str, Any] | None = None
        child_item_twse_id = f"item_twse_turn_{uuid4().hex[:10]}"
        twse_payload: list[dict] = []
        twse_fetch_error: str | None = None

        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash_twse = sha256_text(body.decode("utf-8-sig", errors="replace"))
            twse_payload = json.loads(body.decode("utf-8-sig", errors="replace")) if body else []
            foundation.add_run(child_run_twse)
            foundation.acquire_resource_lock(twse_turnover_res, child_run_twse_id, child_run_twse.started_at)
            now = utc_now_timestamp()
            raw_twse = foundation.add_raw_revision(
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
        except Exception as exc:
            twse_fetch_error = str(exc)
            try:
                now = utc_now_timestamp()
                foundation.add_run_item(
                    IngestionRunItem(
                        ingestion_run_item_id=child_item_twse_id,
                        ingestion_run_id=child_run_twse_id,
                        provider_id="twse",
                        resource_id=twse_turnover_res,
                        started_at=child_run_twse.started_at,
                        completed_at=now,
                        status=IngestionItemStatus.REJECTED,
                        quality_status=DataHealthStatus.CORRUPT,
                        raw_payload_sha256=raw_hash_twse or sha256_text(""),
                        parser_version="1",
                        schema_fingerprint=sha256_text("1"),
                        record_count=0,
                        accepted_count=0,
                        rejected_count=0,
                        reason=twse_fetch_error,
                    )
                )
                foundation.complete_run(
                    IngestionRun(
                        ingestion_run_id=child_run_twse_id,
                        started_at=child_run_twse.started_at,
                        completed_at=now,
                        trigger_type=child_run_twse.trigger_type,
                        runner_version=child_run_twse.runner_version,
                        requested_resources=child_run_twse.requested_resources,
                        actor_id=child_run_twse.actor_id,
                        status=IngestionRunStatus.FAILED,
                    )
                )
            except Exception:
                pass
            try:
                foundation.release_resource_lock(twse_turnover_res, child_run_twse_id)
            except Exception:
                pass
            self.operation_repo.update_item(
                item_id=item_twse_turnover,
                status=InstalledItemStatus.FAILED.value,
                error_detail=twse_fetch_error,
            )
            raise

        # 2. TPEx Turnover
        new_lease = self.operation_repo.extend_lease(
            operation_id,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)

        tpex_turnover_res = "tpex.market-turnover"
        self._require_live_write_authorization(operation_id, authorization, tpex_turnover_res)
        item_tpex_turnover = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_tpex_turnover,
            operation_id=operation_id,
            stage=InstalledOperationStage.TURNOVER_AND_CBC.value,
            resource_id=tpex_turnover_res,
        )
        child_run_tpex_id = f"run_tpex_turn_{uuid4().hex[:10]}"
        child_run_tpex = IngestionRun(
            ingestion_run_id=child_run_tpex_id,
            started_at=utc_now_timestamp(),
            trigger_type=TriggerType.MANUAL,
            runner_version="phase19-turnover-v1",
            requested_resources=(tpex_turnover_res,),
            actor_id=authorization.actor_id,
        )
        raw_hash_tpex = ""
        raw_tpex: dict[str, Any] | None = None
        child_item_tpex_id = f"item_tpex_turn_{uuid4().hex[:10]}"
        tpex_payload: list[dict] = []
        tpex_fetch_error: str | None = None

        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash_tpex = sha256_text(body.decode("utf-8-sig", errors="replace"))
            tpex_payload = json.loads(body.decode("utf-8-sig", errors="replace")) if body else []
            foundation.add_run(child_run_tpex)
            foundation.acquire_resource_lock(tpex_turnover_res, child_run_tpex_id, child_run_tpex.started_at)
            now = utc_now_timestamp()
            raw_tpex = foundation.add_raw_revision(
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
        except Exception as exc:
            tpex_fetch_error = str(exc)
            try:
                now = utc_now_timestamp()
                foundation.add_run_item(
                    IngestionRunItem(
                        ingestion_run_item_id=child_item_tpex_id,
                        ingestion_run_id=child_run_tpex_id,
                        provider_id="tpex",
                        resource_id=tpex_turnover_res,
                        started_at=child_run_tpex.started_at,
                        completed_at=now,
                        status=IngestionItemStatus.REJECTED,
                        quality_status=DataHealthStatus.CORRUPT,
                        raw_payload_sha256=raw_hash_tpex or sha256_text(""),
                        parser_version="1",
                        schema_fingerprint=sha256_text("1"),
                        record_count=0,
                        accepted_count=0,
                        rejected_count=0,
                        reason=tpex_fetch_error,
                    )
                )
                foundation.complete_run(
                    IngestionRun(
                        ingestion_run_id=child_run_tpex_id,
                        started_at=child_run_tpex.started_at,
                        completed_at=now,
                        trigger_type=child_run_tpex.trigger_type,
                        runner_version=child_run_tpex.runner_version,
                        requested_resources=child_run_tpex.requested_resources,
                        actor_id=child_run_tpex.actor_id,
                        status=IngestionRunStatus.FAILED,
                    )
                )
            except Exception:
                pass
            try:
                foundation.release_resource_lock(tpex_turnover_res, child_run_tpex_id)
            except Exception:
                pass
            self.operation_repo.update_item(
                item_id=item_tpex_turnover,
                status=InstalledItemStatus.FAILED.value,
                error_detail=tpex_fetch_error,
            )
            # Make sure TWSE lock is also cleaned up on abort
            try:
                foundation.release_resource_lock(twse_turnover_res, child_run_twse_id)
            except Exception:
                pass
            raise

        # 3. Materialize combined turnover observations into LiquidityRepository, then truthfully finalize both venues
        try:
            liquidity_repo = LiquidityRepository(self.db_path)
            dates_twse: dict[str, float] = {}
            twse_row_errors = 0
            for r in twse_payload:
                try:
                    d = MarketTurnoverCollector._iso_date(r.get("Date", ""))
                    val = float(str(r.get("TradeValue", 0)).replace(",", ""))
                    dates_twse[d] = val
                except Exception:
                    twse_row_errors += 1

            dates_tpex: dict[str, float] = {}
            tpex_row_errors = 0
            for r in tpex_payload:
                try:
                    d = MarketTurnoverCollector._iso_date(r.get("Date", ""))
                    val = float(str(r.get("TradeAmount", 0)).replace(",", ""))
                    dates_tpex[d] = val
                except Exception:
                    tpex_row_errors += 1

            twse_persisted = 0
            twse_rejected = 0
            tpex_persisted = 0
            tpex_rejected = 0
            all_turnover_dates = sorted(set(dates_twse.keys()) | set(dates_tpex.keys()))
            now = utc_now_timestamp()

            for d in all_turnover_dates:
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
                        twse_payload_hash=raw_hash_twse if tw_val is not None else None,
                        tpex_payload_hash=raw_hash_tpex if tp_val is not None else None,
                        available_at=now,
                        fetched_at=now,
                        revision=1,
                    )
                    liquidity_repo.add_turnover(obs)
                    if tw_val is not None:
                        twse_persisted += 1
                    if tp_val is not None:
                        tpex_persisted += 1
                except Exception:
                    if tw_val is not None:
                        twse_rejected += 1
                    if tp_val is not None:
                        tpex_rejected += 1

            # Finalize TWSE
            if len(dates_twse) > 0 and twse_row_errors == 0 and twse_rejected == 0 and twse_persisted > 0:
                twse_run_status = IngestionRunStatus.SUCCEEDED
                twse_inst_status = InstalledItemStatus.ACCEPTED.value
                twse_item_status = IngestionItemStatus.ACCEPTED
                twse_quality = DataHealthStatus.FRESH
            elif twse_persisted > 0:
                twse_run_status = IngestionRunStatus.PARTIAL
                twse_inst_status = InstalledItemStatus.PARTIAL.value
                twse_item_status = IngestionItemStatus.PARTIAL
                twse_quality = DataHealthStatus.PARTIAL
            else:
                twse_run_status = IngestionRunStatus.FAILED
                twse_inst_status = InstalledItemStatus.FAILED.value
                twse_item_status = IngestionItemStatus.REJECTED
                twse_quality = DataHealthStatus.PARTIAL

            twse_reason = (
                f"persisted={twse_persisted}, rejected={twse_rejected}, row_errors={twse_row_errors}"
                if (twse_rejected > 0 or twse_row_errors > 0 or twse_persisted == 0)
                else None
            )

            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_twse_id,
                    ingestion_run_id=child_run_twse_id,
                    provider_id="twse",
                    resource_id=twse_turnover_res,
                    started_at=child_run_twse.started_at,
                    completed_at=now,
                    status=twse_item_status,
                    quality_status=twse_quality,
                    raw_payload_sha256=raw_hash_twse,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                    record_count=len(twse_payload),
                    accepted_count=twse_persisted,
                    rejected_count=twse_rejected + twse_row_errors,
                    reason=twse_reason,
                )
            )

            foundation.complete_run(
                IngestionRun(
                    ingestion_run_id=child_run_twse_id,
                    started_at=child_run_twse.started_at,
                    completed_at=now,
                    trigger_type=child_run_twse.trigger_type,
                    runner_version=child_run_twse.runner_version,
                    requested_resources=child_run_twse.requested_resources,
                    actor_id=child_run_twse.actor_id,
                    status=twse_run_status,
                )
            )
            foundation.release_resource_lock(twse_turnover_res, child_run_twse_id)
            self.operation_repo.update_item(
                item_id=item_twse_turnover,
                status=twse_inst_status,
                ingestion_run_id=child_run_twse_id,
                ingestion_run_item_id=child_item_twse_id,
                raw_resource_revision_id=raw_twse["raw_resource_revision_id"] if raw_twse else None,
                error_detail=twse_reason,
            )

            # Finalize TPEx
            if len(dates_tpex) > 0 and tpex_row_errors == 0 and tpex_rejected == 0 and tpex_persisted > 0:
                tpex_run_status = IngestionRunStatus.SUCCEEDED
                tpex_inst_status = InstalledItemStatus.ACCEPTED.value
                tpex_item_status = IngestionItemStatus.ACCEPTED
                tpex_quality = DataHealthStatus.FRESH
            elif tpex_persisted > 0:
                tpex_run_status = IngestionRunStatus.PARTIAL
                tpex_inst_status = InstalledItemStatus.PARTIAL.value
                tpex_item_status = IngestionItemStatus.PARTIAL
                tpex_quality = DataHealthStatus.PARTIAL
            else:
                tpex_run_status = IngestionRunStatus.FAILED
                tpex_inst_status = InstalledItemStatus.FAILED.value
                tpex_item_status = IngestionItemStatus.REJECTED
                tpex_quality = DataHealthStatus.PARTIAL

            tpex_reason = (
                f"persisted={tpex_persisted}, rejected={tpex_rejected}, row_errors={tpex_row_errors}"
                if (tpex_rejected > 0 or tpex_row_errors > 0 or tpex_persisted == 0)
                else None
            )

            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_tpex_id,
                    ingestion_run_id=child_run_tpex_id,
                    provider_id="tpex",
                    resource_id=tpex_turnover_res,
                    started_at=child_run_tpex.started_at,
                    completed_at=now,
                    status=tpex_item_status,
                    quality_status=tpex_quality,
                    raw_payload_sha256=raw_hash_tpex,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                    record_count=len(tpex_payload),
                    accepted_count=tpex_persisted,
                    rejected_count=tpex_rejected + tpex_row_errors,
                    reason=tpex_reason,
                )
            )

            foundation.complete_run(
                IngestionRun(
                    ingestion_run_id=child_run_tpex_id,
                    started_at=child_run_tpex.started_at,
                    completed_at=now,
                    trigger_type=child_run_tpex.trigger_type,
                    runner_version=child_run_tpex.runner_version,
                    requested_resources=child_run_tpex.requested_resources,
                    actor_id=child_run_tpex.actor_id,
                    status=tpex_run_status,
                )
            )
            foundation.release_resource_lock(tpex_turnover_res, child_run_tpex_id)
            self.operation_repo.update_item(
                item_id=item_tpex_turnover,
                status=tpex_inst_status,
                ingestion_run_id=child_run_tpex_id,
                ingestion_run_item_id=child_item_tpex_id,
                raw_resource_revision_id=raw_tpex["raw_resource_revision_id"] if raw_tpex else None,
                error_detail=tpex_reason,
            )
        except Exception as exc:
            try:
                foundation.release_resource_lock(twse_turnover_res, child_run_twse_id)
            except Exception:
                pass
            try:
                foundation.release_resource_lock(tpex_turnover_res, child_run_tpex_id)
            except Exception:
                pass
            self.operation_repo.update_item(
                item_id=item_twse_turnover,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            self.operation_repo.update_item(
                item_id=item_tpex_turnover,
                status=InstalledItemStatus.FAILED.value,
                error_detail=str(exc),
            )
            raise

        # 3. CBC M1B (Supplementary / Non-blocking)
        new_lease = self.operation_repo.extend_lease(
            operation_id,
            lease_duration_seconds=60,
            expected_owner_id=authorization.instance_id,
        )
        authorization.refresh_lease(new_lease)

        cbc_resource = "cbc.m1b"
        self._require_live_write_authorization(operation_id, authorization, cbc_resource)
        item_cbc = f"item_{uuid4().hex}"
        self.operation_repo.create_item(
            item_id=item_cbc,
            operation_id=operation_id,
            stage=InstalledOperationStage.TURNOVER_AND_CBC.value,
            resource_id=cbc_resource,
        )
        child_run_id = f"run_cbc_m1b_{uuid4().hex[:10]}"
        try:
            status_code, body, _ = self.egress_client.fetch(
                "https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=EF15M01",
                deadline_monotonic=deadline_monotonic,
            )
            raw_hash = sha256_text(body.decode("utf-8"))
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
            cbc_parsed = 0
            cbc_accepted = 0
            cbc_rejected = 0
            cbc_error = None
            try:
                cbc_payload = json.loads(body.decode("utf-8-sig", errors="replace")) if body else {}
                data_rows = []
                official_data = cbc_payload.get("data", {})
                if isinstance(official_data, dict) and "dataSets" in official_data:
                    data_rows = official_data.get("dataSets", [])
                else:
                    data_rows = cbc_payload.get("result", {}).get("data", [])
                cbc_parsed = len(data_rows)
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
                        cbc_rejected += 1
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
                        cbc_accepted += 1
                    except Exception as exc:
                        cbc_rejected += 1
            except Exception as cbc_parse_exc:
                cbc_error = str(cbc_parse_exc)

            if cbc_error or (cbc_parsed > 0 and cbc_accepted == 0):
                cbc_item_status = IngestionItemStatus.REJECTED
                cbc_inst_status = InstalledItemStatus.FAILED.value
                cbc_run_status = IngestionRunStatus.FAILED
            elif cbc_rejected > 0:
                cbc_item_status = IngestionItemStatus.PARTIAL
                cbc_inst_status = InstalledItemStatus.PARTIAL.value
                cbc_run_status = IngestionRunStatus.PARTIAL
            elif cbc_accepted > 0:
                cbc_item_status = IngestionItemStatus.ACCEPTED
                cbc_inst_status = InstalledItemStatus.ACCEPTED.value
                cbc_run_status = IngestionRunStatus.SUCCEEDED
            else:
                cbc_item_status = IngestionItemStatus.PARTIAL
                cbc_inst_status = InstalledItemStatus.PARTIAL.value
                cbc_run_status = IngestionRunStatus.PARTIAL

            child_item_id = f"item_cbc_m1b_{uuid4().hex[:10]}"
            foundation.add_run_item(
                IngestionRunItem(
                    ingestion_run_item_id=child_item_id,
                    ingestion_run_id=child_run_id,
                    provider_id="cbc",
                    resource_id=cbc_resource,
                    started_at=child_run.started_at,
                    completed_at=now,
                    status=cbc_item_status,
                    quality_status=DataHealthStatus.FRESH if cbc_rejected == 0 and not cbc_error else DataHealthStatus.PARTIAL,
                    raw_payload_sha256=raw_hash,
                    parser_version="1",
                    schema_fingerprint=sha256_text("1"),
                    record_count=cbc_parsed,
                    accepted_count=cbc_accepted,
                    rejected_count=cbc_rejected,
                    reason=cbc_error or ("ingestion rejected" if cbc_rejected > 0 else None),
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
                    status=cbc_run_status,
                )
            )
            foundation.release_resource_lock(cbc_resource, child_run_id)

            self.operation_repo.update_item(
                item_id=item_cbc,
                status=cbc_inst_status,
                ingestion_run_id=child_run_id,
                ingestion_run_item_id=child_item_id,
                raw_resource_revision_id=raw["raw_resource_revision_id"],
                error_detail=cbc_error,
            )
        except Exception as exc:
            try:
                foundation.release_resource_lock(cbc_resource, child_run_id)
            except Exception:
                pass
            # Non-blocking for sync, but record failure accurately
            self.operation_repo.update_item(
                item_id=item_cbc,
                status=InstalledItemStatus.FAILED.value,
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
        eod_payload = json.loads(eod_body.decode("utf-8-sig", errors="replace")) if eod_body else []

        # 3. Explicit session validation against calendar proof
        parsed_snap = parse_tpex_snapshot(eod_payload) if venue == "TPEX" else parse_twse_snapshot(eod_payload)
        trade_date = parsed_snap.source_trade_date
        if not trade_date:
            raise ValueError(f"eod payload trade date could not be determined: status={parsed_snap.status}, reason={parsed_snap.reason}")

        with self.operation_repo._get_connection() as conn:
            year_prefix = f"{trade_date[:4]}%"
            cal_year_row = conn.execute(
                "SELECT COUNT(*) FROM trading_calendar_revisions WHERE trade_date LIKE ? AND status = 'available'",
                (year_prefix,),
            ).fetchone()
            cal_year_count = cal_year_row[0] if cal_year_row else 0
            if cal_year_count == 0:
                eod_item = f"item_{uuid4().hex}"
                self.operation_repo.create_item(
                    item_id=eod_item,
                    operation_id=operation_id,
                    stage=InstalledOperationStage.EOD.value,
                    resource_id=venue_resource,
                )
                self.operation_repo.update_item(
                    item_id=eod_item,
                    status=InstalledItemStatus.PARTIAL.value,
                    error_detail=f"source session {trade_date} is not an authorized trading session: calendar proof missing for year {trade_date[:4]}",
                )
                raise ValueError(
                    f"source session {trade_date} is not an authorized trading session: "
                    f"calendar proof missing for year {trade_date[:4]}"
                )

            cal_row = conn.execute(
                """
                SELECT session_status FROM trading_calendar_revisions
                WHERE trade_date = ? AND status = 'available'
                """,
                (trade_date,),
            ).fetchone()
            if not cal_row or cal_row[0] not in ("trading", "special"):
                eod_item = f"item_{uuid4().hex}"
                self.operation_repo.create_item(
                    item_id=eod_item,
                    operation_id=operation_id,
                    stage=InstalledOperationStage.EOD.value,
                    resource_id=venue_resource,
                )
                self.operation_repo.update_item(
                    item_id=eod_item,
                    status=InstalledItemStatus.PARTIAL.value,
                    error_detail=f"source session {trade_date} is not an authorized trading session: status={cal_row[0] if cal_row else 'missing'}",
                )
                raise ValueError(
                    f"source session {trade_date} is not an authorized trading session: "
                    f"status={cal_row[0] if cal_row else 'missing'}"
                )

        # 4. Reload persisted identity and classification proof
        now = utc_now_timestamp()
        universe_repo = UniverseRepository(self.db_path)
        canonical_sym = f"{code}.TWO" if venue == "TPEX" else f"{code}.TW"
        identity_context = universe_repo.identity_context_for_eod(
            canonical_symbol=canonical_sym,
            knowledge_cutoff_at=now,
        )
        if not identity_context:
            raise ValueError(
                f"persisted identity context missing for symbol {canonical_sym}"
            )

        eod_repo = EodCloseRepository(self.db_path)
        classification_context = eod_repo.latest_classification_for_code(code)
        if (
            not classification_context
            or classification_context.get("classification_state") != "accepted"
        ):
            raise ValueError(
                f"persisted accepted classification context missing for symbol {canonical_sym}: "
                f"state={classification_context.get('classification_state') if classification_context else 'missing'}"
            )

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
            identity_by_code={code: identity_context},
            classification_by_code={code: classification_context},
        )

        # 6. Verify requested symbol became publicly eligible before marking accepted
        with self.operation_repo._get_connection() as conn:
            obs_row = conn.execute(
                """
                SELECT observation_status, public_eligibility_status, close_value
                FROM eod_close_observations
                WHERE official_code = ? AND trade_date = ?
                ORDER BY revision_number DESC LIMIT 1
                """,
                (code, trade_date),
            ).fetchone()
            is_eligible = (
                obs_row is not None
                and obs_row[0] == "available"
                and obs_row[1] == "eligible"
                and obs_row[2] is not None
            )

        if is_eligible:
            self.operation_repo.update_item(
                item_id=eod_item,
                status=InstalledItemStatus.ACCEPTED.value,
                ingestion_run_id=eod_res.get("run_id"),
                ingestion_run_item_id=eod_res.get("item_id"),
                raw_resource_revision_id=eod_res.get("raw_resource_revision_id"),
            )
        else:
            self.operation_repo.update_item(
                item_id=eod_item,
                status=InstalledItemStatus.PARTIAL.value,
                ingestion_run_id=eod_res.get("run_id"),
                ingestion_run_item_id=eod_res.get("item_id"),
                raw_resource_revision_id=eod_res.get("raw_resource_revision_id"),
                error_detail=f"symbol {code} did not become publicly eligible after eod materialization",
            )
            raise ValueError(f"symbol {code} did not become publicly eligible after eod materialization")

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
