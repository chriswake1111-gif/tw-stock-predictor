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
from datetime import datetime, timezone
from typing import Any, Mapping

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
    RawResourceRevision,
    StoragePolicy,
    canonical_json,
    sha256_text,
)
from src.domain.eod_close import (
    EOD_PRICE_SEMANTICS_VERSION,
    EodProductScope,
    positive_decimal_text,
)
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.eod_close_repository import (
    CLASSIFICATION_RESOURCE_ID,
    EodCloseRepository,
)


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
    ) -> None:
        self.db_path = db_path
        self.enabled = (
            os.getenv("EOD_INGESTION_WRITES_ENABLED", "false").strip().lower() == "true"
            if enabled is None
            else bool(enabled)
        )
        self.foundation = foundation or DataFoundationRepository(db_path)
        self.repository = repository or EodCloseRepository(db_path)

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
    ) -> dict[str, Any]:
        metadata = _RESOURCE_METADATA[resource_id]
        quality_status, eligibility_status = _raw_status(parsed_status)
        logical_key = f"{resource_id}:{source_trade_date or 'undated'}"
        previous = self.foundation.latest_raw_revision(
            provider_id=metadata["provider_id"],
            resource_id=resource_id,
            logical_revision_key=logical_key,
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
        existing = self.foundation.add_raw_revision(revision)
        return existing

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
        existing_command = self._check_idempotency(idempotency_key, raw_payload_sha256)
        if existing_command:
            return {**existing_command, "created": False}
        raw_revision = self._raw_revision(
            resource_id=parsed.resource_id,
            raw_payload_sha256=raw_payload_sha256,
            schema_fingerprint=parsed.schema_fingerprint,
            received_at=received,
            source_published_at=published,
            parsed_status=parsed.status,
            source_trade_date=parsed.source_trade_date,
            reason=parsed.reason,
        )
        raw_id = raw_revision["raw_resource_revision_id"]
        logical_key = f"{parsed.resource_id}:{parsed.source_trade_date or 'undated'}"
        previous_source = self.repository.latest_source_snapshot_for_logical(
            resource_id=parsed.resource_id, logical_revision_key=logical_key
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
            "source_published_at": published,
            "fetched_at": received,
            "received_at": received,
            "available_at": received,
            "ingested_at": received,
            "source_url": parsed.source_url,
            "contract_version": "eod_close_v1",
            "parser_version": "1",
            "schema_fingerprint": parsed.schema_fingerprint,
            "raw_payload_sha256": raw_payload_sha256,
            "normalized_payload_sha256": parsed.normalized_payload_sha256,
            "source_record_reference": f"{parsed.resource_id}:{parsed.source_trade_date or 'undated'}",
            "source_scope": parsed.source_scope,
            "reason": parsed.reason,
        })
        identity_by_code = identity_by_code or {}
        classification_by_code = classification_by_code or {}
        observation_ids: list[str] = []
        policy = self.repository.price_policy_for_resource(parsed.resource_id)
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
            elif classification and classification.get("classification_decision") == EodProductScope.NOT_APPLICABLE.value:
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
            if published is None:
                quality_flags.append("publication_instant_unproven")
            if row.volume_parse_status == "valid" and row.volume_value == "0":
                quality_flags.append("official_zero_volume_not_public_eligible")
            previous_observation = self.repository.latest_observation(
                resource_id=parsed.resource_id,
                official_code=code,
                trade_date=row.trade_date,
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
                "public_eligibility_status": eligibility,
                "quality_status": "fresh" if observation_status == "available" else "quality_warning",
                "quality_flags_json": _json(quality_flags),
                "row_fingerprint": row.row_fingerprint,
                "raw_payload_sha256": raw_payload_sha256,
                "normalized_payload_sha256": parsed.normalized_payload_sha256,
                "source_trading_scope": parsed.source_scope,
                "available_at": received,
                "ingested_at": received,
                "source_record_reference": f"{parsed.resource_id}:{row.trade_date}:{code}",
            })
            observation_ids.append(created["close_observation_id"])
        command = None
        if idempotency_key:
            command = self.repository.add_ingestion_idempotency({
                "idempotency_key": idempotency_key,
                "payload_fingerprint": raw_payload_sha256,
                "resource_id": parsed.resource_id,
                "raw_resource_revision_id": raw_id,
                "source_snapshot_id": source_id["source_snapshot_id"],
                "actor_id": actor_id,
                "created_at": received,
            })
        return {
            "resource_id": parsed.resource_id,
            "raw_resource_revision_id": raw_id,
            "source_snapshot_id": source_id["source_snapshot_id"],
            "observation_ids": observation_ids,
            "idempotency": command,
            "created": bool(source_id.get("created")),
        }

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
        existing_command = self._check_idempotency(idempotency_key, raw_payload_sha256)
        if existing_command:
            return {**existing_command, "created": False}
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
        revision = RawResourceRevision(
            raw_resource_revision_id="pending",
            provider_id="twse-isin-official",
            resource_id=TWSE_ISIN_CLASSIFICATION_RESOURCE_ID,
            logical_revision_key=f"{TWSE_ISIN_CLASSIFICATION_RESOURCE_ID}:{official_code.strip()}",
            source_published_at=published,
            available_at=received,
            received_at=received,
            ingested_at=received,
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
                    logical_revision_key=f"{TWSE_ISIN_CLASSIFICATION_RESOURCE_ID}:{official_code.strip()}",
                ) or {}
            ).get("raw_resource_revision_id"),
            reason=parsed.reason,
        )
        revision = RawResourceRevision(
            **{**revision.__dict__, "raw_resource_revision_id": revision.deterministic_identity()}
        )
        raw_revision = self.foundation.add_raw_revision(revision)
        previous_classification = self.repository.latest_classification_for_code(official_code.strip())
        decision = parsed.decision
        evidence = self.repository.add_classification_evidence({
            "resource_id": CLASSIFICATION_RESOURCE_ID,
            "raw_resource_revision_id": raw_revision["raw_resource_revision_id"],
            "logical_revision_key": f"{CLASSIFICATION_RESOURCE_ID}:{official_code.strip()}",
            "official_code": parsed.official_code,
            "market_raw": parsed.market_raw,
            "security_type_raw": parsed.security_type_raw,
            "isin_raw": parsed.isin_raw,
            "listing_date": parsed.listing_date,
            "cfi_raw": parsed.cfi_raw,
            "currency_raw": parsed.currency_raw,
            "remarks_raw": parsed.remarks_raw,
            "raw_cells_json": _json(parsed.raw_cells),
            "classification_decision": decision,
            "classification_state": parsed.state,
            "supersedes_classification_evidence_id": previous_classification["classification_evidence_id"] if previous_classification else None,
            "reason": parsed.reason,
            "contract_version": "eod_product_scope_v1",
            "source_published_at": published,
            "fetched_at": received,
            "received_at": received,
            "available_at": received,
            "ingested_at": received,
            "source_url": "https://isin.twse.com.tw/isin/single_main.jsp?owncode=" + parsed.official_code + "&stockname=",
            "parser_version": "1",
            "schema_fingerprint": parsed.schema_fingerprint,
            "raw_payload_sha256": raw_payload_sha256,
            "normalized_payload_sha256": sha256_text(canonical_json(parsed.to_dict())),
            "source_record_reference": f"{CLASSIFICATION_RESOURCE_ID}:{parsed.official_code}",
        })
        command = None
        if idempotency_key:
            command = self.repository.add_ingestion_idempotency({
                "idempotency_key": idempotency_key,
                "payload_fingerprint": raw_payload_sha256,
                "resource_id": CLASSIFICATION_RESOURCE_ID,
                "raw_resource_revision_id": raw_revision["raw_resource_revision_id"],
                "actor_id": actor_id,
                "created_at": received,
            })
        return {
            "resource_id": CLASSIFICATION_RESOURCE_ID,
            "raw_resource_revision_id": raw_revision["raw_resource_revision_id"],
            "classification_evidence_id": evidence["classification_evidence_id"],
            "classification_state": parsed.state,
            "classification_decision": parsed.decision,
            "idempotency": command,
            "created": bool(evidence.get("created")),
        }


__all__ = ["EodCloseIngestionService", "EodIngestionDisabled"]
