"""Operator-only append workflow for Phase 14 EOD evidence.

The public API is read-only.  This service is an explicit ingestion boundary:
it accepts already fetched payloads, validates them through the allowlisted
parsers, reuses the Phase 10 raw-revision identity, and appends immutable EOD
lineage.  It is disabled unless an operator explicitly enables writes.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Mapping

from src.collectors.eod_close_collectors import (
    TPEX_EOD_RESOURCE_ID,
    TPEX_EOD_SOURCE_SCOPE,
    TWSE_EOD_RESOURCE_ID,
    TWSE_EOD_SOURCE_SCOPE,
    parse_tpex_snapshot,
    parse_twse_snapshot,
)
from src.collectors.twse_isin_classification_collector import (
    TWSE_ISIN_CLASSIFICATION_RESOURCE_ID,
    parse_twse_isin_classification,
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
    canonical_json,
    sha256_text,
)
from src.domain.eod_close import (
    EOD_PRICE_SEMANTICS_VERSION,
    EodProductScope,
    positive_decimal_text,
)
from src.domain.valuation import (
    normalize_utc_timestamp,
    parse_aware_timestamp,
    utc_now_timestamp,
)
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.eod_close_repository import (
    CLASSIFICATION_RESOURCE_ID,
    EodCloseRepository,
)
from src.services.universe_audit import write_universe_audit
from src.services.universe_write_guard import UniverseOperatorContext


class EodIngestionDisabled(PermissionError):
    """Raised when an operator write is attempted without an explicit gate."""


_RESOURCE_METADATA = {
    TWSE_EOD_RESOURCE_ID: {
        "provider_id": "twse-universe-official",
        "source_scope": TWSE_EOD_SOURCE_SCOPE,
    },
    TPEX_EOD_RESOURCE_ID: {
        "provider_id": "tpex-universe-official",
        "source_scope": TPEX_EOD_SOURCE_SCOPE,
    },
    TWSE_ISIN_CLASSIFICATION_RESOURCE_ID: {
        "provider_id": "twse-isin-official",
        "source_scope": "twse_isin_security_classification",
    },
}

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now_or(value: str | None) -> str:
    return normalize_utc_timestamp(value or utc_now_timestamp(), "received_at")


def _raw_status(parsed_status: str) -> tuple[DataHealthStatus, EligibilityStatus]:
    if parsed_status == "available":
        return DataHealthStatus.FRESH, EligibilityStatus.ELIGIBLE
    if parsed_status == "partial":
        return DataHealthStatus.PARTIAL, EligibilityStatus.AWAITING_REVIEW
    if parsed_status == "schema_changed":
        return DataHealthStatus.SCHEMA_CHANGED, EligibilityStatus.INELIGIBLE
    return DataHealthStatus.UNKNOWN, EligibilityStatus.INELIGIBLE


def _normalize_raw_hash(value: str | None, fallback: str) -> str:
    candidate = str(value or fallback).strip()
    if not _SHA256_RE.fullmatch(candidate):
        raise ValueError("raw_payload_sha256 must be a SHA-256 hex digest")
    return candidate.lower()


class EodCloseIngestionService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        enabled: bool | None = None,
        foundation: DataFoundationRepository | None = None,
        repository: EodCloseRepository | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.db_path = db_path
        self.enabled = (
            os.getenv("EOD_INGESTION_WRITES_ENABLED", "false").strip().lower() == "true"
            if enabled is None
            else bool(enabled)
        )
        self.foundation = foundation or DataFoundationRepository(db_path)
        self.repository = repository or EodCloseRepository(db_path)
        self.failure_injector = failure_injector

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise EodIngestionDisabled("EOD ingestion writes require explicit operator enablement")

    def _check_idempotency(self, key: str | None, fingerprint: str) -> dict[str, Any] | None:
        if not key:
            return None
        existing = self.repository.ingestion_idempotency(key)
        if existing and existing["payload_fingerprint"] != fingerprint:
            raise ValueError("idempotency_key_reused")
        return existing

    def _inject_failure(self, point: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point)

    def _raw_revision(
        self,
        *,
        resource_id: str,
        raw_payload_sha256: str,
        schema_fingerprint: str,
        received_at: str,
        source_published_at: str | None,
        parsed_status: str,
        source_trade_date: str | None,
        reason: str | None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        metadata = _RESOURCE_METADATA[resource_id]
        quality_status, eligibility_status = _raw_status(parsed_status)
        logical_key = f"{resource_id}:{source_trade_date or 'undated'}"
        previous = self.foundation.latest_raw_revision(
            provider_id=metadata["provider_id"],
            resource_id=resource_id,
            logical_revision_key=logical_key,
            connection=connection,
        )
        revision = RawResourceRevision(
            raw_resource_revision_id="pending",
            provider_id=metadata["provider_id"],
            resource_id=resource_id,
            logical_revision_key=logical_key,
            source_published_at=source_published_at,
            available_at=received_at,
            received_at=received_at,
            ingested_at=received_at,
            raw_payload_sha256=raw_payload_sha256,
            parser_version="1",
            schema_fingerprint=schema_fingerprint,
            storage_policy=StoragePolicy.ARCHIVE_RAW,
            quality_status=quality_status,
            eligibility_status=eligibility_status,
            supersedes_revision_id=previous["raw_resource_revision_id"] if previous else None,
            reason=reason,
        )
        revision = RawResourceRevision(
            **{**revision.__dict__, "raw_resource_revision_id": revision.deterministic_identity()}
        )
        existing = self.foundation.add_raw_revision(revision, connection=connection)
        return existing

    @staticmethod
    def _completed_reservation_result(reservation: Mapping[str, Any]) -> dict[str, Any]:
        try:
            result = json.loads(str(reservation["result_json"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("completed EOD command has invalid result state") from exc
        if not isinstance(result, dict):
            raise RuntimeError("completed EOD command result is not an object")
        return {**result, "created": False}

    @staticmethod
    def _reservation_is_stale(reservation: Mapping[str, Any]) -> bool:
        if reservation.get("status") != "running":
            return False
        if reservation.get("run_status") != "running":
            return True
        now = parse_aware_timestamp(utc_now_timestamp(), "now")
        lease_expires = reservation.get("lease_expires_at")
        if lease_expires:
            return parse_aware_timestamp(lease_expires, "lease_expires_at") <= now
        # A short claim-to-lock interval is expected.  A longer interval means
        # the process likely died between the durable claim and lock acquire.
        updated_at = reservation.get("updated_at")
        if not updated_at:
            return True
        return (now - parse_aware_timestamp(updated_at, "updated_at")).total_seconds() > 5

    def _reserve_or_wait(
        self,
        *,
        idempotency_key: str | None,
        payload_fingerprint: str,
        resource_id: str,
        actor_id: str,
        command_received_at: str,
        source_published_at: str | None,
    ) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        deadline = time.monotonic() + 10
        while True:
            reservation = self.repository.reserve_ingestion_command(
                idempotency_key=idempotency_key,
                payload_fingerprint=payload_fingerprint,
                resource_id=resource_id,
                actor_id=actor_id,
                command_received_at=command_received_at,
                source_published_at=source_published_at,
            )
            if reservation["status"] == "completed":
                return reservation
            if reservation["status"] == "reserved":
                return reservation
            if self._reservation_is_stale(reservation):
                run_id = reservation.get("run_id")
                if run_id:
                    self.repository.reset_ingestion_command(
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                        error_code="stale_command_recovered",
                    )
                    continue
            if time.monotonic() >= deadline:
                raise RuntimeError("idempotent EOD ingestion command remains in progress")
            time.sleep(0.02)

    def _finish_run_without_lock(
        self,
        run: IngestionRun,
        *,
        resource_id: str,
        reason: str,
        status: IngestionRunStatus,
    ) -> None:
        completed_at = utc_now_timestamp()
        metadata = _RESOURCE_METADATA[resource_id]
        item = IngestionRunItem(
            ingestion_run_item_id=f"eoditem_{run.ingestion_run_id}",
            ingestion_run_id=run.ingestion_run_id,
            provider_id=metadata["provider_id"],
            resource_id=resource_id,
            started_at=run.started_at,
            completed_at=completed_at,
            status=IngestionItemStatus.REJECTED,
            quality_status=DataHealthStatus.REJECTED,
            raw_payload_sha256=None,
            parser_version="1",
            record_count=0,
            accepted_count=0,
            rejected_count=0,
            reason=reason,
        )
        self.foundation.add_run_item(item)
        self.foundation.complete_run(IngestionRun(
            ingestion_run_id=run.ingestion_run_id,
            started_at=run.started_at,
            completed_at=completed_at,
            trigger_type=run.trigger_type,
            runner_version=run.runner_version,
            requested_resources=run.requested_resources,
            actor_id=run.actor_id,
            status=status,
            retry_of_run_id=run.retry_of_run_id,
        ))

    def _prepare_command(
        self,
        *,
        resource_id: str,
        payload_fingerprint: str,
        received_at: str,
        source_published_at: str | None,
        idempotency_key: str | None,
        actor_id: str,
        venue: str | None,
    ) -> dict[str, Any]:
        while True:
            reservation = self._reserve_or_wait(
                idempotency_key=idempotency_key,
                payload_fingerprint=payload_fingerprint,
                resource_id=resource_id,
                actor_id=actor_id,
                command_received_at=received_at,
                source_published_at=source_published_at,
            )
            if reservation and reservation["status"] == "completed":
                return {"completed": True, "result": self._completed_reservation_result(reservation)}

            effective_received = str(
                reservation["command_received_at"] if reservation else received_at
            )
            effective_published = (
                reservation["source_published_at"] if reservation else source_published_at
            )
            previous_run_id = reservation.get("run_id") if reservation else None
            run = IngestionRun(
                ingestion_run_id=f"eodrun_{uuid.uuid4().hex}",
                started_at=utc_now_timestamp(),
                trigger_type=TriggerType.RETRY if previous_run_id else TriggerType.MANUAL,
                runner_version="phase14-eod-ingestion-v2",
                requested_resources=(resource_id,),
                actor_id=actor_id,
                retry_of_run_id=previous_run_id,
            )
            context = UniverseOperatorContext(
                actor_id=actor_id,
                run_id=run.ingestion_run_id,
                lock_id=f"eodlock_{run.ingestion_run_id}",
                audit_id=f"eodaudit_{run.ingestion_run_id}",
            )
            with self.repository.write_transaction() as conn:
                self.foundation.add_run(run, connection=conn)
                claimed = True
                if idempotency_key:
                    claimed = self.repository.claim_ingestion_command(
                        idempotency_key=idempotency_key,
                        run_id=run.ingestion_run_id,
                        lock_id=context.lock_id or "",
                        audit_id=context.audit_id or "",
                        connection=conn,
                    ) is not None
            if not claimed:
                self._finish_run_without_lock(
                    run,
                    resource_id=resource_id,
                    reason="idempotency_command_claim_lost",
                    status=IngestionRunStatus.BLOCKED,
                )
                continue
            try:
                self.foundation.acquire_resource_lock(
                    resource_id,
                    run.ingestion_run_id,
                    run.started_at,
                )
            except Exception as exc:
                if idempotency_key:
                    self.repository.reset_ingestion_command(
                        idempotency_key=idempotency_key,
                        run_id=run.ingestion_run_id,
                        error_code="resource_lock_unavailable",
                    )
                self._finish_run_without_lock(
                    run,
                    resource_id=resource_id,
                    reason="resource_lock_unavailable",
                    status=IngestionRunStatus.BLOCKED,
                )
                write_universe_audit(
                    context,
                    command="eod_ingestion",
                    outcome="blocked",
                    resource_id=resource_id,
                    resource_role="eod_close",
                    venue=venue,
                    reason=type(exc).__name__,
                )
                raise
            write_universe_audit(
                context,
                command="eod_ingestion",
                outcome="started",
                resource_id=resource_id,
                resource_role="eod_close",
                venue=venue,
            )
            return {
                "completed": False,
                "reservation": reservation,
                "received_at": effective_received,
                "source_published_at": effective_published,
                "run": run,
                "context": context,
            }

    @staticmethod
    def _terminal_run_status(parsed_status: str) -> IngestionRunStatus:
        if parsed_status == "available" or parsed_status == "accepted":
            return IngestionRunStatus.SUCCEEDED
        if parsed_status in {"schema_changed", "blocked"}:
            return IngestionRunStatus.BLOCKED
        return IngestionRunStatus.PARTIAL

    @staticmethod
    def _item_status(parsed_status: str) -> IngestionItemStatus:
        return {
            "available": IngestionItemStatus.ACCEPTED,
            "accepted": IngestionItemStatus.ACCEPTED,
            "partial": IngestionItemStatus.PARTIAL,
            "schema_changed": IngestionItemStatus.SCHEMA_CHANGED,
            "blocked": IngestionItemStatus.REJECTED,
            "ambiguous": IngestionItemStatus.AWAITING_REVIEW,
        }.get(parsed_status, IngestionItemStatus.QUALITY_WARNING)

    @staticmethod
    def _item_quality(parsed_status: str) -> DataHealthStatus:
        return {
            "available": DataHealthStatus.FRESH,
            "accepted": DataHealthStatus.FRESH,
            "partial": DataHealthStatus.PARTIAL,
            "schema_changed": DataHealthStatus.SCHEMA_CHANGED,
            "blocked": DataHealthStatus.REJECTED,
            "ambiguous": DataHealthStatus.AWAITING_REVIEW,
        }.get(parsed_status, DataHealthStatus.UNKNOWN)

    def _execute_command(
        self,
        *,
        resource_id: str,
        payload_fingerprint: str,
        received_at: str,
        source_published_at: str | None,
        idempotency_key: str | None,
        actor_id: str,
        venue: str | None,
        operation: Callable[[sqlite3.Connection, str, str | None], dict[str, Any]],
    ) -> dict[str, Any]:
        execution = self._prepare_command(
            resource_id=resource_id,
            payload_fingerprint=payload_fingerprint,
            received_at=received_at,
            source_published_at=source_published_at,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            venue=venue,
        )
        if execution["completed"]:
            return execution["result"]

        run: IngestionRun = execution["run"]
        context: UniverseOperatorContext = execution["context"]
        effective_received = execution["received_at"]
        effective_published = execution["source_published_at"]
        try:
            with self.repository.write_transaction() as conn:
                outcome = operation(conn, effective_received, effective_published)
                result = {
                    **outcome["result"],
                    "governance": {
                        "run_id": run.ingestion_run_id,
                        "lock_id": context.lock_id,
                        "audit_id": context.audit_id,
                        "trigger_type": run.trigger_type.value,
                    },
                }
                completed_at = utc_now_timestamp()
                item_spec = outcome["item"]
                item = IngestionRunItem(
                    ingestion_run_item_id=f"eoditem_{run.ingestion_run_id}",
                    ingestion_run_id=run.ingestion_run_id,
                    provider_id=_RESOURCE_METADATA[resource_id]["provider_id"],
                    resource_id=resource_id,
                    started_at=run.started_at,
                    completed_at=completed_at,
                    status=item_spec["status"],
                    quality_status=item_spec["quality_status"],
                    raw_payload_sha256=payload_fingerprint,
                    parser_version=item_spec.get("parser_version"),
                    schema_fingerprint=item_spec.get("schema_fingerprint"),
                    record_count=item_spec.get("record_count", 0),
                    accepted_count=item_spec.get("accepted_count", 0),
                    rejected_count=item_spec.get("rejected_count", 0),
                    reason=item_spec.get("reason"),
                )
                self.foundation.add_run_item(item, connection=conn)
                self.foundation.complete_run(IngestionRun(
                    ingestion_run_id=run.ingestion_run_id,
                    started_at=run.started_at,
                    completed_at=completed_at,
                    trigger_type=run.trigger_type,
                    runner_version=run.runner_version,
                    requested_resources=run.requested_resources,
                    actor_id=run.actor_id,
                    status=outcome["terminal_status"],
                    retry_of_run_id=run.retry_of_run_id,
                ), connection=conn)
                if idempotency_key:
                    self.repository.complete_ingestion_command(
                        idempotency_key=idempotency_key,
                        run_id=run.ingestion_run_id,
                        result_json=_json(result),
                        connection=conn,
                    )
        except Exception as exc:
            failure_reason = f"operator_command_failed:{type(exc).__name__}"
            try:
                self._finish_run_without_lock(
                    run,
                    resource_id=resource_id,
                    reason=failure_reason,
                    status=IngestionRunStatus.FAILED,
                )
            finally:
                try:
                    self.foundation.release_resource_lock(
                        resource_id, run.ingestion_run_id
                    )
                finally:
                    if idempotency_key:
                        self.repository.reset_ingestion_command(
                            idempotency_key=idempotency_key,
                            run_id=run.ingestion_run_id,
                            error_code=failure_reason,
                        )
                write_universe_audit(
                    context,
                    command="eod_ingestion",
                    outcome="failed",
                    resource_id=resource_id,
                    resource_role="eod_close",
                    venue=venue,
                    reason=type(exc).__name__,
                )
            raise

        self.foundation.release_resource_lock(resource_id, run.ingestion_run_id)
        write_universe_audit(
            context,
            command="eod_ingestion",
            outcome="succeeded",
            resource_id=resource_id,
            resource_role="eod_close",
            venue=venue,
        )
        return result

    def ingest_price_payload(
        self,
        venue: str,
        payload: Any,
        *,
        received_at: str | None = None,
        source_published_at: str | None = None,
        raw_payload_sha256: str | None = None,
        idempotency_key: str | None = None,
        actor_id: str = "phase14-operator",
        identity_by_code: Mapping[str, Mapping[str, Any]] | None = None,
        classification_by_code: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._require_enabled()
        normalized_venue = str(venue).strip().upper()
        if normalized_venue == "TWSE":
            parsed = parse_twse_snapshot(payload)
        elif normalized_venue == "TPEX":
            parsed = parse_tpex_snapshot(payload)
        else:
            raise ValueError("venue must be TWSE or TPEX")
        received = _now_or(received_at)
        published = normalize_utc_timestamp(source_published_at, "source_published_at") if source_published_at else None
        raw_payload_sha256 = _normalize_raw_hash(
            raw_payload_sha256, sha256_text(canonical_json(payload))
        )
        identity_by_code = identity_by_code or {}
        classification_by_code = classification_by_code or {}

        def operation(
            conn: sqlite3.Connection,
            command_received: str,
            command_published: str | None,
        ) -> dict[str, Any]:
            raw_revision = self._raw_revision(
                resource_id=parsed.resource_id,
                raw_payload_sha256=raw_payload_sha256,
                schema_fingerprint=parsed.schema_fingerprint,
                received_at=command_received,
                source_published_at=command_published,
                parsed_status=parsed.status,
                source_trade_date=parsed.source_trade_date,
                reason=parsed.reason,
                connection=conn,
            )
            self._inject_failure("after_raw_revision")
            raw_id = raw_revision["raw_resource_revision_id"]
            logical_key = f"{parsed.resource_id}:{parsed.source_trade_date or 'undated'}"
            previous_source = self.repository.latest_source_snapshot_for_logical(
                resource_id=parsed.resource_id,
                logical_revision_key=logical_key,
                connection=conn,
            )
            source_id = self.repository.add_source_snapshot({
                "resource_id": parsed.resource_id,
                "raw_resource_revision_id": raw_id,
                "logical_revision_key": logical_key,
                "supersedes_source_snapshot_id": previous_source["source_snapshot_id"] if previous_source else None,
                "source_trade_date": parsed.source_trade_date,
                "source_trade_date_status": parsed.source_trade_date_status,
                "status": parsed.status,
                "coverage_state": parsed.coverage_state,
                "row_count": len(parsed.rows),
                "source_date_min": parsed.source_trade_date,
                "source_date_max": parsed.source_trade_date,
                "source_published_at": command_published,
                "fetched_at": command_received,
                "received_at": command_received,
                "available_at": command_received,
                "ingested_at": command_received,
                "source_url": parsed.source_url,
                "contract_version": "eod_close_v1",
                "parser_version": "1",
                "schema_fingerprint": parsed.schema_fingerprint,
                "raw_payload_sha256": raw_payload_sha256,
                "normalized_payload_sha256": parsed.normalized_payload_sha256,
                "source_record_reference": f"{parsed.resource_id}:{parsed.source_trade_date or 'undated'}",
                "source_scope": parsed.source_scope,
                "reason": parsed.reason,
            }, connection=conn)
            self._inject_failure("after_source_snapshot")
            observation_ids: list[str] = []
            accepted_count = 0
            policy = self.repository.price_policy(conn, parsed.resource_id)
            for row in parsed.rows:
                code = row.official_code
                identity = identity_by_code.get(code)
                classification = classification_by_code.get(code)
                close = positive_decimal_text(row.close_value)
                volume = positive_decimal_text(row.volume_value)
                supported = bool(
                    classification
                    and classification.get("classification_state") == "accepted"
                    and classification.get("classification_decision") == EodProductScope.SUPPORTED_STOCK.value
                )
                identity_ok = bool(identity and identity.get("instrument_revision_id"))
                policy_ok = bool(policy and policy.get("currency") == "TWD" and policy.get("unit") == "TWD_per_share")
                if supported and identity_ok and policy_ok and close is not None and volume is not None:
                    product_scope = EodProductScope.SUPPORTED_STOCK.value
                    observation_status = "available"
                    eligibility = "eligible"
                    accepted_count += 1
                elif (
                    classification
                    and classification.get("classification_state") == "accepted"
                    and classification.get("classification_decision")
                    == EodProductScope.NOT_APPLICABLE.value
                ):
                    product_scope = EodProductScope.NOT_APPLICABLE.value
                    observation_status = "not_applicable"
                    eligibility = "ineligible"
                elif close is None or volume is None:
                    product_scope = EodProductScope.NEEDS_HUMAN_INPUT.value
                    observation_status = "insufficient_data"
                    eligibility = "ineligible"
                else:
                    product_scope = EodProductScope.NEEDS_HUMAN_INPUT.value
                    observation_status = "needs_human_input"
                    eligibility = "awaiting_review"
                quality_flags: list[str] = []
                if command_published is None:
                    quality_flags.append("publication_instant_unproven")
                if row.volume_parse_status == "valid" and row.volume_value == "0":
                    quality_flags.append("official_zero_volume_not_public_eligible")
                previous_observation = self.repository.latest_observation(
                    resource_id=parsed.resource_id,
                    official_code=code,
                    trade_date=row.trade_date,
                    connection=conn,
                )
                created = self.repository.add_observation({
                    "resource_id": parsed.resource_id,
                    "raw_resource_revision_id": raw_id,
                    "source_snapshot_id": source_id["source_snapshot_id"],
                    "supersedes_observation_id": previous_observation["close_observation_id"] if previous_observation else None,
                    "revision_number": int(previous_observation["revision_number"]) + 1 if previous_observation else 1,
                    "classification_evidence_id": classification.get("classification_evidence_id") if classification else None,
                    "instrument_id": identity.get("instrument_id") if identity else None,
                    "instrument_revision_id": identity.get("instrument_revision_id") if identity else None,
                    "venue": row.venue,
                    "official_code": code,
                    "trade_date": row.trade_date,
                    "trade_date_status": "valid",
                    "raw_close_text": row.raw_close_text,
                    "close_value": row.close_value,
                    "raw_volume_text": row.raw_volume_text,
                    "volume_value": row.volume_value,
                    "raw_trade_indication_text": row.raw_trade_indication_text,
                    "trade_indication_value": row.trade_indication_value,
                    "currency": policy.get("currency") if policy else None,
                    "unit": policy.get("unit") if policy else None,
                    "price_semantics_version": EOD_PRICE_SEMANTICS_VERSION,
                    "product_scope": product_scope,
                    "observation_status": observation_status,
                    "source_observation_state": "source_observed",
                    "public_eligibility_status": eligibility,
                    "quality_status": "fresh" if observation_status == "available" else "quality_warning",
                    "quality_flags_json": _json(quality_flags),
                    "row_fingerprint": row.row_fingerprint,
                    "raw_payload_sha256": raw_payload_sha256,
                    "normalized_payload_sha256": parsed.normalized_payload_sha256,
                    "source_trading_scope": parsed.source_scope,
                    "available_at": command_received,
                    "ingested_at": command_received,
                    "source_record_reference": f"{parsed.resource_id}:{row.trade_date}:{code}",
                }, connection=conn)
                observation_ids.append(created["close_observation_id"])
                self._inject_failure("after_observation")
            self._inject_failure("after_observations")
            command = None
            if idempotency_key:
                command = self.repository.add_ingestion_idempotency({
                    "idempotency_key": idempotency_key,
                    "payload_fingerprint": raw_payload_sha256,
                    "resource_id": parsed.resource_id,
                    "raw_resource_revision_id": raw_id,
                    "source_snapshot_id": source_id["source_snapshot_id"],
                    "actor_id": actor_id,
                    "created_at": command_received,
                }, connection=conn)
            return {
                "result": {
                    "resource_id": parsed.resource_id,
                    "raw_resource_revision_id": raw_id,
                    "source_snapshot_id": source_id["source_snapshot_id"],
                    "observation_ids": observation_ids,
                    "idempotency": command,
                    "created": bool(source_id.get("created")),
                },
                "item": {
                    "status": self._item_status(parsed.status),
                    "quality_status": self._item_quality(parsed.status),
                    "parser_version": "1",
                    "schema_fingerprint": parsed.schema_fingerprint,
                    "record_count": len(parsed.rows),
                    "accepted_count": accepted_count,
                    "rejected_count": len(parsed.rows) - accepted_count,
                    "reason": parsed.reason,
                },
                "terminal_status": self._terminal_run_status(parsed.status),
            }

        return self._execute_command(
            resource_id=parsed.resource_id,
            payload_fingerprint=raw_payload_sha256,
            received_at=received,
            source_published_at=published,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            venue=normalized_venue,
            operation=operation,
        )

    def ingest_classification_html(
        self,
        official_code: str,
        html: str,
        *,
        venue: str,
        trade_date: str | None = None,
        received_at: str | None = None,
        source_published_at: str | None = None,
        raw_payload_sha256: str | None = None,
        idempotency_key: str | None = None,
        actor_id: str = "phase14-operator",
    ) -> dict[str, Any]:
        self._require_enabled()
        normalized_venue = str(venue).strip().upper()
        expected_market = {"TWSE": "上市", "TPEX": "上櫃"}.get(normalized_venue)
        if expected_market is None:
            raise ValueError("venue must be TWSE or TPEX")
        parsed = parse_twse_isin_classification(
            html,
            official_code=official_code,
            expected_market=expected_market,
            trade_date=trade_date,
        )
        received = _now_or(received_at)
        published = normalize_utc_timestamp(source_published_at, "source_published_at") if source_published_at else None
        raw_payload_sha256 = _normalize_raw_hash(raw_payload_sha256, sha256_text(html))
        def operation(
            conn: sqlite3.Connection,
            command_received: str,
            command_published: str | None,
        ) -> dict[str, Any]:
            quality_status = {
                "accepted": DataHealthStatus.FRESH,
                "ambiguous": DataHealthStatus.AWAITING_REVIEW,
                "schema_changed": DataHealthStatus.SCHEMA_CHANGED,
            }.get(parsed.state, DataHealthStatus.UNKNOWN)
            eligibility_status = (
                EligibilityStatus.ELIGIBLE
                if parsed.state == "accepted"
                else EligibilityStatus.AWAITING_REVIEW
            )
            logical_key = f"{TWSE_ISIN_CLASSIFICATION_RESOURCE_ID}:{official_code.strip()}"
            revision = RawResourceRevision(
                raw_resource_revision_id="pending",
                provider_id="twse-isin-official",
                resource_id=TWSE_ISIN_CLASSIFICATION_RESOURCE_ID,
                logical_revision_key=logical_key,
                source_published_at=command_published,
                available_at=command_received,
                received_at=command_received,
                ingested_at=command_received,
                raw_payload_sha256=raw_payload_sha256,
                parser_version="1",
                schema_fingerprint=parsed.schema_fingerprint,
                storage_policy=StoragePolicy.ARCHIVE_RAW,
                quality_status=quality_status,
                eligibility_status=eligibility_status,
                supersedes_revision_id=(
                    self.foundation.latest_raw_revision(
                        provider_id="twse-isin-official",
                        resource_id=TWSE_ISIN_CLASSIFICATION_RESOURCE_ID,
                        logical_revision_key=logical_key,
                        connection=conn,
                    ) or {}
                ).get("raw_resource_revision_id"),
                reason=parsed.reason,
            )
            revision = RawResourceRevision(
                **{**revision.__dict__, "raw_resource_revision_id": revision.deterministic_identity()}
            )
            raw_revision = self.foundation.add_raw_revision(revision, connection=conn)
            self._inject_failure("after_raw_revision")
            previous_classification = self.repository.latest_classification_for_code(
                official_code.strip(), connection=conn
            )
            evidence = self.repository.add_classification_evidence({
                "resource_id": CLASSIFICATION_RESOURCE_ID,
                "raw_resource_revision_id": raw_revision["raw_resource_revision_id"],
                "logical_revision_key": logical_key,
                "official_code": parsed.official_code,
                "market_raw": parsed.market_raw,
                "security_type_raw": parsed.security_type_raw,
                "isin_raw": parsed.isin_raw,
                "listing_date": parsed.listing_date,
                "cfi_raw": parsed.cfi_raw,
                "currency_raw": parsed.currency_raw,
                "remarks_raw": parsed.remarks_raw,
                "raw_cells_json": _json(parsed.raw_cells),
                "classification_decision": parsed.decision,
                "classification_state": parsed.state,
                "supersedes_classification_evidence_id": previous_classification["classification_evidence_id"] if previous_classification else None,
                "reason": parsed.reason,
                "contract_version": "eod_product_scope_v1",
                "source_published_at": command_published,
                "fetched_at": command_received,
                "received_at": command_received,
                "available_at": command_received,
                "ingested_at": command_received,
                "source_url": "https://isin.twse.com.tw/isin/single_main.jsp?owncode=" + parsed.official_code + "&stockname=",
                "parser_version": "1",
                "schema_fingerprint": parsed.schema_fingerprint,
                "raw_payload_sha256": raw_payload_sha256,
                "normalized_payload_sha256": sha256_text(canonical_json(parsed.to_dict())),
                "source_record_reference": f"{CLASSIFICATION_RESOURCE_ID}:{parsed.official_code}",
            }, connection=conn)
            self._inject_failure("after_source_snapshot")
            self._inject_failure("after_observations")
            command = None
            if idempotency_key:
                command = self.repository.add_ingestion_idempotency({
                    "idempotency_key": idempotency_key,
                    "payload_fingerprint": raw_payload_sha256,
                    "resource_id": CLASSIFICATION_RESOURCE_ID,
                    "raw_resource_revision_id": raw_revision["raw_resource_revision_id"],
                    "actor_id": actor_id,
                    "created_at": command_received,
                }, connection=conn)
            return {
                "result": {
                    "resource_id": CLASSIFICATION_RESOURCE_ID,
                    "raw_resource_revision_id": raw_revision["raw_resource_revision_id"],
                    "classification_evidence_id": evidence["classification_evidence_id"],
                    "classification_state": parsed.state,
                    "classification_decision": parsed.decision,
                    "idempotency": command,
                    "created": bool(evidence.get("created")),
                },
                "item": {
                    "status": self._item_status(parsed.state),
                    "quality_status": self._item_quality(parsed.state),
                    "parser_version": "1",
                    "schema_fingerprint": parsed.schema_fingerprint,
                    "record_count": 1,
                    "accepted_count": 1 if parsed.state == "accepted" else 0,
                    "rejected_count": 0 if parsed.state == "accepted" else 1,
                    "reason": parsed.reason,
                },
                "terminal_status": self._terminal_run_status(parsed.state),
            }

        return self._execute_command(
            resource_id=CLASSIFICATION_RESOURCE_ID,
            payload_fingerprint=raw_payload_sha256,
            received_at=received,
            source_published_at=published,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            venue=normalized_venue,
            operation=operation,
        )


__all__ = ["EodCloseIngestionService", "EodIngestionDisabled"]
