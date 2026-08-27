"""Phase 10 production-data and freshness governance contracts.

These contracts describe operational evidence only.  They deliberately keep
provider fetch success, validation, human approval, and model eligibility as
separate states.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.domain.valuation import normalize_utc_timestamp, parse_aware_timestamp


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProviderType(str, Enum):
    OFFICIAL = "official"
    AGGREGATOR = "aggregator"
    MANUAL = "manual"
    INTERNAL = "internal"


class AuthorityTier(str, Enum):
    AUTHORITATIVE = "authoritative"
    PROFESSIONAL = "professional"
    AGGREGATOR = "aggregator"
    MANUAL_RESEARCH = "manual_research"


class ResourceType(str, Enum):
    MARKET_TURNOVER = "market_turnover"
    MONETARY_STATISTIC = "monetary_statistic"
    TRADING_CALENDAR = "trading_calendar"
    SYMBOL_MASTER = "symbol_master"
    CORPORATE_ACTION = "corporate_action"
    EOD_CLOSE = "eod_close"
    PRODUCT_CLASSIFICATION = "product_classification"


class ExpectedFrequency(str, Enum):
    DAILY = "daily"
    MONTHLY_PUBLICATION = "monthly_publication"
    PERIODIC = "periodic"
    MANUAL = "manual"


class TriggerType(str, Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    RETRY = "retry"


class IngestionRunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class IngestionItemStatus(str, Enum):
    FETCHED = "fetched"
    VALIDATED = "validated"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_CHANGED = "schema_changed"
    AWAITING_REVIEW = "awaiting_review"
    QUALITY_WARNING = "quality_warning"
    REJECTED = "rejected"


class DataHealthStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    PARTIAL = "partial"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_CHANGED = "schema_changed"
    AWAITING_REVIEW = "awaiting_review"
    QUALITY_WARNING = "quality_warning"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    AWAITING_REVIEW = "awaiting_review"


class StoragePolicy(str, Enum):
    ARCHIVE_RAW = "archive_raw"
    ARCHIVE_NORMALIZED = "archive_normalized"
    HASH_ONLY = "hash_only"


class SnapshotFreshnessStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class TradingSessionStatus(str, Enum):
    TRADING = "trading"
    HOLIDAY = "holiday"
    NO_TRADING = "no_trading"
    SPECIAL = "special"


class PublicationEvidenceStatus(str, Enum):
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class PublicationVerificationMode(str, Enum):
    MANUAL_OFFICIAL_SOURCE_REVIEW = "manual_official_source_review"
    OFFICIAL_MACHINE_SOURCE = "official_machine_source"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_fingerprint(required_fields: list[str] | tuple[str, ...]) -> str:
    normalized = sorted({field.strip() for field in required_fields if field.strip()})
    if not normalized:
        raise ValueError("schema required_fields cannot be empty")
    return sha256_text(canonical_json(normalized))


def _identifier(value: str, field_name: str) -> str:
    normalized = str(value).strip().lower()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must use lowercase letters, numbers, '.', '_', ':', or '-'"
        )
    return normalized


def _non_blank(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _sha256(value: str, field_name: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return normalized


@dataclass(frozen=True)
class DataProvider:
    provider_id: str
    display_name: str
    authority_tier: AuthorityTier
    provider_type: ProviderType
    base_identity: str
    created_at: str
    enabled: bool = True

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "provider_id": _identifier(self.provider_id, "provider_id"),
            "display_name": _non_blank(self.display_name, "display_name"),
            "authority_tier": self.authority_tier.value,
            "provider_type": self.provider_type.value,
            "base_identity": _non_blank(self.base_identity, "base_identity"),
            "enabled": bool(self.enabled),
            "created_at": normalize_utc_timestamp(self.created_at, "created_at"),
        }


@dataclass(frozen=True)
class DataResource:
    resource_id: str
    provider_id: str
    logical_resource_key: str
    resource_type: ResourceType
    market: str
    expected_frequency: ExpectedFrequency
    freshness_policy: str
    parser_id: str
    parser_version: str
    schema_version: str
    storage_policy: StoragePolicy
    created_at: str
    enabled: bool = True

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "resource_id": _identifier(self.resource_id, "resource_id"),
            "provider_id": _identifier(self.provider_id, "provider_id"),
            "logical_resource_key": _identifier(
                self.logical_resource_key, "logical_resource_key"
            ),
            "resource_type": self.resource_type.value,
            "market": _non_blank(self.market, "market").upper(),
            "expected_frequency": self.expected_frequency.value,
            "freshness_policy": _non_blank(
                self.freshness_policy, "freshness_policy"
            ),
            "parser_id": _identifier(self.parser_id, "parser_id"),
            "parser_version": _non_blank(self.parser_version, "parser_version"),
            "schema_version": _non_blank(self.schema_version, "schema_version"),
            "storage_policy": self.storage_policy.value,
            "enabled": bool(self.enabled),
            "created_at": normalize_utc_timestamp(self.created_at, "created_at"),
        }


@dataclass(frozen=True)
class IngestionRun:
    ingestion_run_id: str
    started_at: str
    trigger_type: TriggerType
    runner_version: str
    requested_resources: tuple[str, ...]
    actor_id: str
    status: IngestionRunStatus = IngestionRunStatus.RUNNING
    completed_at: str | None = None
    retry_of_run_id: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        resources = sorted(
            {_identifier(value, "requested_resource") for value in self.requested_resources}
        )
        if not resources:
            raise ValueError("requested_resources cannot be empty")
        started = normalize_utc_timestamp(self.started_at, "started_at")
        completed = (
            normalize_utc_timestamp(self.completed_at, "completed_at")
            if self.completed_at
            else None
        )
        if completed and parse_aware_timestamp(completed, "completed_at") < parse_aware_timestamp(
            started, "started_at"
        ):
            raise ValueError("completed_at cannot precede started_at")
        if self.status is IngestionRunStatus.RUNNING and completed is not None:
            raise ValueError("running ingestion cannot have completed_at")
        if self.status is not IngestionRunStatus.RUNNING and completed is None:
            raise ValueError("completed ingestion requires completed_at")
        if self.trigger_type is TriggerType.RETRY and not self.retry_of_run_id:
            raise ValueError("retry ingestion requires retry_of_run_id")
        return {
            "ingestion_run_id": _identifier(
                self.ingestion_run_id, "ingestion_run_id"
            ),
            "started_at": started,
            "completed_at": completed,
            "trigger_type": self.trigger_type.value,
            "runner_version": _non_blank(self.runner_version, "runner_version"),
            "requested_resources": resources,
            "actor_id": _identifier(self.actor_id, "actor_id"),
            "status": self.status.value,
            "retry_of_run_id": (
                _identifier(self.retry_of_run_id, "retry_of_run_id")
                if self.retry_of_run_id
                else None
            ),
        }


@dataclass(frozen=True)
class IngestionRunItem:
    ingestion_run_item_id: str
    ingestion_run_id: str
    provider_id: str
    resource_id: str
    started_at: str
    status: IngestionItemStatus
    quality_status: DataHealthStatus
    completed_at: str | None = None
    http_status: int | None = None
    raw_payload_sha256: str | None = None
    parser_version: str | None = None
    schema_fingerprint: str | None = None
    record_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    reason: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        started = normalize_utc_timestamp(self.started_at, "started_at")
        completed = (
            normalize_utc_timestamp(self.completed_at, "completed_at")
            if self.completed_at
            else None
        )
        if completed and parse_aware_timestamp(completed, "completed_at") < parse_aware_timestamp(
            started, "started_at"
        ):
            raise ValueError("completed_at cannot precede started_at")
        counts = (self.record_count, self.accepted_count, self.rejected_count)
        if any(int(value) < 0 for value in counts):
            raise ValueError("ingestion item counts cannot be negative")
        if self.accepted_count + self.rejected_count > self.record_count:
            raise ValueError("accepted and rejected counts cannot exceed record_count")
        if self.status in {
            IngestionItemStatus.PROVIDER_ERROR,
            IngestionItemStatus.SCHEMA_CHANGED,
            IngestionItemStatus.REJECTED,
        } and not (self.reason and self.reason.strip()):
            raise ValueError("failed ingestion item requires a reason")
        return {
            "ingestion_run_item_id": _identifier(
                self.ingestion_run_item_id, "ingestion_run_item_id"
            ),
            "ingestion_run_id": _identifier(
                self.ingestion_run_id, "ingestion_run_id"
            ),
            "provider_id": _identifier(self.provider_id, "provider_id"),
            "resource_id": _identifier(self.resource_id, "resource_id"),
            "started_at": started,
            "completed_at": completed,
            "status": self.status.value,
            "http_status": self.http_status,
            "raw_payload_sha256": (
                _sha256(self.raw_payload_sha256, "raw_payload_sha256")
                if self.raw_payload_sha256
                else None
            ),
            "parser_version": self.parser_version,
            "schema_fingerprint": (
                _sha256(self.schema_fingerprint, "schema_fingerprint")
                if self.schema_fingerprint
                else None
            ),
            "record_count": int(self.record_count),
            "accepted_count": int(self.accepted_count),
            "rejected_count": int(self.rejected_count),
            "quality_status": self.quality_status.value,
            "reason": self.reason.strip() if self.reason else None,
        }


@dataclass(frozen=True)
class RawResourceRevision:
    raw_resource_revision_id: str
    provider_id: str
    resource_id: str
    logical_revision_key: str
    received_at: str
    ingested_at: str
    raw_payload_sha256: str
    parser_version: str
    schema_fingerprint: str
    storage_policy: StoragePolicy
    quality_status: DataHealthStatus
    eligibility_status: EligibilityStatus
    source_published_at: str | None = None
    available_at: str | None = None
    storage_location: str | None = None
    supersedes_revision_id: str | None = None
    reason: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        received = normalize_utc_timestamp(self.received_at, "received_at")
        ingested = normalize_utc_timestamp(self.ingested_at, "ingested_at")
        if parse_aware_timestamp(ingested, "ingested_at") < parse_aware_timestamp(
            received, "received_at"
        ):
            raise ValueError("ingested_at cannot precede received_at")
        published = (
            normalize_utc_timestamp(self.source_published_at, "source_published_at")
            if self.source_published_at
            else None
        )
        available = (
            normalize_utc_timestamp(self.available_at, "available_at")
            if self.available_at
            else None
        )
        if published and available and parse_aware_timestamp(
            available, "available_at"
        ) < parse_aware_timestamp(published, "source_published_at"):
            raise ValueError("available_at cannot precede source_published_at")
        if available and parse_aware_timestamp(
            available, "available_at"
        ) > parse_aware_timestamp(received, "received_at"):
            raise ValueError("available_at cannot be later than received_at")
        if self.eligibility_status is EligibilityStatus.ELIGIBLE:
            if available is None:
                raise ValueError("eligible resource revision requires available_at")
            if self.quality_status not in {
                DataHealthStatus.FRESH,
                DataHealthStatus.QUALITY_WARNING,
            }:
                raise ValueError("eligible resource revision has unusable quality status")
        if self.eligibility_status is EligibilityStatus.AWAITING_REVIEW:
            if self.quality_status is not DataHealthStatus.AWAITING_REVIEW:
                raise ValueError("awaiting-review eligibility requires matching quality status")
        return {
            "raw_resource_revision_id": _identifier(
                self.raw_resource_revision_id, "raw_resource_revision_id"
            ),
            "provider_id": _identifier(self.provider_id, "provider_id"),
            "resource_id": _identifier(self.resource_id, "resource_id"),
            "logical_revision_key": _non_blank(
                self.logical_revision_key, "logical_revision_key"
            ),
            "source_published_at": published,
            "available_at": available,
            "received_at": received,
            "ingested_at": ingested,
            "raw_payload_sha256": _sha256(
                self.raw_payload_sha256, "raw_payload_sha256"
            ),
            "parser_version": _non_blank(self.parser_version, "parser_version"),
            "schema_fingerprint": _sha256(
                self.schema_fingerprint, "schema_fingerprint"
            ),
            "storage_policy": self.storage_policy.value,
            "storage_location": self.storage_location,
            "quality_status": self.quality_status.value,
            "eligibility_status": self.eligibility_status.value,
            "supersedes_revision_id": (
                _identifier(self.supersedes_revision_id, "supersedes_revision_id")
                if self.supersedes_revision_id
                else None
            ),
            "reason": self.reason.strip() if self.reason else None,
        }

    def deterministic_identity(self) -> str:
        payload = self.canonical_payload()
        identity = {
            key: payload[key]
            for key in (
                "provider_id",
                "resource_id",
                "logical_revision_key",
                "source_published_at",
                "raw_payload_sha256",
            )
        }
        return f"rawrev_{sha256_text(canonical_json(identity))[:24]}"


@dataclass(frozen=True)
class ResourcePublicationEvidence:
    provider_id: str
    resource_id: str
    logical_revision_key: str
    official_release_at: str
    source_reference: str
    source_identity: str
    evidence_file_sha256: str
    captured_at: str
    verification_mode: PublicationVerificationMode
    verified_by: str
    status: PublicationEvidenceStatus = PublicationEvidenceStatus.ACCEPTED

    def canonical_payload(self) -> dict[str, Any]:
        release = normalize_utc_timestamp(
            self.official_release_at, "official_release_at"
        )
        captured = normalize_utc_timestamp(self.captured_at, "captured_at")
        if parse_aware_timestamp(release, "official_release_at") > parse_aware_timestamp(
            captured, "captured_at"
        ):
            raise ValueError("official_release_at cannot be later than captured_at")
        return {
            "provider_id": _identifier(self.provider_id, "provider_id"),
            "resource_id": _identifier(self.resource_id, "resource_id"),
            "logical_revision_key": _non_blank(
                self.logical_revision_key, "logical_revision_key"
            ),
            "official_release_at": release,
            "source_reference": _non_blank(
                self.source_reference, "source_reference"
            ),
            "source_identity": _non_blank(self.source_identity, "source_identity"),
            "evidence_file_sha256": _sha256(
                self.evidence_file_sha256, "evidence_file_sha256"
            ),
            "captured_at": captured,
            "verification_mode": self.verification_mode.value,
            "verified_by": _identifier(self.verified_by, "verified_by"),
            "status": self.status.value,
        }

    def deterministic_identity(self) -> str:
        return f"publication_{sha256_text(canonical_json(self.canonical_payload()))[:24]}"


@dataclass(frozen=True)
class SnapshotFreshnessResult:
    snapshot_id: str
    comparison_cutoff: str
    checked_at: str
    freshness_status: SnapshotFreshnessStatus
    reasons: tuple[str, ...] = field(default_factory=tuple)
    checked_dependencies: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def canonical_payload(self) -> dict[str, Any]:
        reasons = sorted({_identifier(reason, "freshness reason") for reason in self.reasons})
        if self.freshness_status is SnapshotFreshnessStatus.CURRENT and reasons:
            raise ValueError("current snapshot cannot have freshness reasons")
        if self.freshness_status is not SnapshotFreshnessStatus.CURRENT and not reasons:
            raise ValueError("non-current freshness status requires at least one reason")
        return {
            "snapshot_id": _non_blank(self.snapshot_id, "snapshot_id"),
            "comparison_cutoff": normalize_utc_timestamp(
                self.comparison_cutoff, "comparison_cutoff"
            ),
            "checked_at": normalize_utc_timestamp(self.checked_at, "checked_at"),
            "freshness_status": self.freshness_status.value,
            "reasons": reasons,
            "checked_dependencies": sorted(
                (dict(item) for item in self.checked_dependencies),
                key=canonical_json,
            ),
            "snapshot_validity": "immutable_historical_evidence_unchanged",
        }
