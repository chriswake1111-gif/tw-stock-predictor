"""Evidence-first Universe Foundation value objects and pure policies.

Universe is deliberately a neutral identity/context layer.  This module does
not contain prices, signals, ranking, valuation, or recommendation semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable

from src.domain.valuation import normalize_utc_timestamp, parse_aware_timestamp


class UniverseVenue(str, Enum):
    TWSE = "TWSE"
    TPEX = "TPEX"


class ResourceRole(str, Enum):
    MASTER_SNAPSHOT = "master_snapshot"
    LISTING_LIFECYCLE_EVENT = "listing_lifecycle_event"
    TRADING_OPERATIONAL_EVENT = "trading_operational_event"
    CORROBORATING_IDENTITY_OBSERVATION = "corroborating_identity_observation"
    LICENSED_REFERENCE_FILE = "licensed_reference_file"


class ListingStatus(str, Enum):
    LISTED = "listed"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class TradingState(str, Enum):
    NORMAL = "normal"
    SUSPENDED = "suspended"
    ALTERED = "altered"
    PERIODIC = "periodic"
    MANAGED = "managed"
    UNKNOWN = "unknown"


class MembershipState(str, Enum):
    VISIBLE = "visible"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class AvailabilityMode(str, Enum):
    OFFICIAL_TIMESTAMP = "official_timestamp"
    CONSERVATIVE_FIRST_OBSERVED = "conservative_first_observed"
    MANUAL_PUBLICATION_EVIDENCE_REQUIRED = "manual_publication_evidence_required"


class FreshnessMode(str, Enum):
    UNKNOWN_WITHOUT_OFFICIAL_CADENCE = "unknown_without_official_cadence"
    EVENT_OBSERVATION = "event_observation"
    OFFICIAL_CADENCE_WINDOW = "official_cadence_window"
    LICENSED_REFERENCE = "licensed_reference"


class FreshnessStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class UniverseStatus(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    NEEDS_HUMAN_INPUT = "needs_human_input"
    INSUFFICIENT_DATA = "insufficient_data"


class MappingPolicyVersion(str, Enum):
    V1 = "universe_symbol_mapping_v1"


ACTIONABLE_HUMAN_REASONS = frozenset(
    {
        "source_revision_awaiting_review",
        "manual_publication_evidence_required",
        "source_schema_review_required",
        "source_revision_revoked_without_corrected_revision",
        "canonical_mapping_unverified",
    }
)

_CODE_RE = re.compile(r"^[0-9]{4,6}$")
_CANONICAL_RE = re.compile(r"^(?P<code>[0-9]{4,6})\.(?P<venue>TW|TWO)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def coerce_venue(value: UniverseVenue | str) -> UniverseVenue:
    if isinstance(value, UniverseVenue):
        return value
    return UniverseVenue(str(value).strip().upper())


def normalize_universe_timestamp(value: str, field_name: str) -> str:
    """Normalize an aware timestamp to UTC; date-only values are rejected."""
    return normalize_utc_timestamp(value, field_name)


def validate_knowledge_cutoff_at(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("knowledge_cutoff_at is required")
    candidate = value.strip()
    if "T" not in candidate and "t" not in candidate:
        raise ValueError("knowledge_cutoff_at must include a timestamp and timezone")
    return normalize_universe_timestamp(candidate, "knowledge_cutoff_at")


def parse_source_temporal(value: str, field_name: str = "source_effective") -> tuple[str, str]:
    """Return the original precision: ``date`` or normalized ``timestamp``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    candidate = value.strip()
    try:
        if "T" not in candidate and "t" not in candidate:
            return date.fromisoformat(candidate).isoformat(), "date"
        return normalize_universe_timestamp(candidate, field_name), "timestamp"
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 date or aware timestamp") from exc


def validate_official_code(value: str) -> str:
    code = str(value).strip().upper()
    if not _CODE_RE.fullmatch(code):
        raise ValueError("official_code must be a 4-6 digit source code")
    return code


def canonical_symbol_for(venue: UniverseVenue | str, official_code: str) -> str:
    venue_value = coerce_venue(venue)
    code = validate_official_code(official_code)
    return f"{code}.{'TW' if venue_value is UniverseVenue.TWSE else 'TWO'}"


def parse_canonical_symbol(value: str) -> tuple[UniverseVenue, str]:
    if not isinstance(value, str):
        raise ValueError("canonical_symbol is required")
    match = _CANONICAL_RE.fullmatch(value.strip().upper())
    if not match:
        raise ValueError("canonical_symbol must use a .TW or .TWO suffix")
    venue = UniverseVenue.TWSE if match.group("venue") == "TW" else UniverseVenue.TPEX
    return venue, validate_official_code(match.group("code"))


def identity_binding_fingerprint(
    venue: UniverseVenue | str,
    official_code: str,
    identity_epoch: int,
    source_identity: str,
) -> str:
    venue_value = coerce_venue(venue).value
    code = validate_official_code(official_code)
    if int(identity_epoch) < 1:
        raise ValueError("identity_epoch must be at least 1")
    source = str(source_identity).strip()
    if not source:
        raise ValueError("source_identity is required")
    payload = {"venue": venue_value, "official_code": code, "identity_epoch": int(identity_epoch), "source_identity": source}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def payload_fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UniverseIdentityBinding:
    venue: UniverseVenue
    official_code: str
    identity_epoch: int
    source_identity: str
    instrument_id: str | None = None

    def validated(self) -> "UniverseIdentityBinding":
        validate_official_code(self.official_code)
        identity_binding_fingerprint(self.venue, self.official_code, self.identity_epoch, self.source_identity)
        return self

    @property
    def fingerprint(self) -> str:
        self.validated()
        return identity_binding_fingerprint(self.venue, self.official_code, self.identity_epoch, self.source_identity)


@dataclass(frozen=True)
class UniverseRevisionInput:
    resource_id: str
    logical_revision_key: str
    fetched_at: str
    received_at: str
    ingested_at: str
    status: str
    available_at: str | None = None
    source_published_at: str | None = None
    source_effective_date: str | None = None
    first_observed_at: str | None = None
    reason: str | None = None
    payload_sha256: str | None = None
    schema_fingerprint: str | None = None
    parser_version: str | None = None
    source_reference: str | None = None
    supersedes_revision_id: str | None = None

    def validated(self) -> "UniverseRevisionInput":
        for name in ("fetched_at", "received_at", "ingested_at"):
            normalize_universe_timestamp(getattr(self, name), name)
        if self.available_at:
            normalize_universe_timestamp(self.available_at, "available_at")
        if self.first_observed_at:
            normalize_universe_timestamp(self.first_observed_at, "first_observed_at")
        if self.source_published_at and "T" in self.source_published_at:
            normalize_universe_timestamp(self.source_published_at, "source_published_at")
        if self.source_effective_date:
            parse_source_temporal(self.source_effective_date, "source_effective_date")
        if self.status not in {"accepted", "partial", "provider_error", "schema_changed", "awaiting_review", "revoked", "rejected"}:
            raise ValueError("unsupported universe revision status")
        if self.status not in {"accepted", "partial"} and not (self.reason and self.reason.strip()):
            raise ValueError("blocking universe revision requires a reason")
        if self.payload_sha256 and not _SHA256_RE.fullmatch(self.payload_sha256.lower()):
            raise ValueError("payload_sha256 must be a SHA-256 digest")
        return self


@dataclass(frozen=True)
class UniverseStatusInput:
    identity_reference: dict[str, Any] | None
    operational_freshness: str = FreshnessStatus.UNKNOWN.value
    current_complete: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _safe_reference(reference: dict[str, Any] | None) -> bool:
    return bool(reference and (reference.get("instrument_id") or reference.get("official_code")))


def universe_status_matrix_v1(value: UniverseStatusInput | dict[str, Any]) -> dict[str, Any]:
    """Pure, shared exact/list status precedence for Universe public DTOs."""
    if isinstance(value, dict):
        value = UniverseStatusInput(
            identity_reference=value.get("identity_reference"),
            operational_freshness=value.get("operational_freshness", FreshnessStatus.UNKNOWN.value),
            current_complete=bool(value.get("current_complete", False)),
            reasons=tuple(value.get("reasons", ())),
        )
    reasons = list(dict.fromkeys(str(reason) for reason in value.reasons if reason))
    if not _safe_reference(value.identity_reference):
        status = UniverseStatus.INSUFFICIENT_DATA.value
    elif any(reason in ACTIONABLE_HUMAN_REASONS for reason in reasons):
        status = UniverseStatus.NEEDS_HUMAN_INPUT.value
    elif value.current_complete and value.operational_freshness == FreshnessStatus.CURRENT.value:
        status = UniverseStatus.AVAILABLE.value
    else:
        status = UniverseStatus.PARTIAL.value
    return {
        "status": status,
        "status_policy_version": "universe_status_matrix_v1",
        "reasons": reasons,
    }


def compose_universe_status(items: Iterable[dict[str, Any]], scoped_venues: Iterable[str]) -> str:
    rows = list(items)
    venues = {str(v).upper() for v in scoped_venues}
    if not rows:
        return UniverseStatus.INSUFFICIENT_DATA.value
    statuses = [str(row.get("status")) for row in rows]
    if any(status == UniverseStatus.NEEDS_HUMAN_INPUT.value for status in statuses):
        return UniverseStatus.NEEDS_HUMAN_INPUT.value
    if any(status == UniverseStatus.PARTIAL.value for status in statuses):
        return UniverseStatus.PARTIAL.value
    if any(status == UniverseStatus.INSUFFICIENT_DATA.value for status in statuses):
        return UniverseStatus.PARTIAL.value if venues else UniverseStatus.INSUFFICIENT_DATA.value
    return UniverseStatus.AVAILABLE.value


__all__ = [
    "ACTIONABLE_HUMAN_REASONS", "AvailabilityMode", "FreshnessMode", "FreshnessStatus",
    "ListingStatus", "MappingPolicyVersion", "MembershipState", "ResourceRole",
    "TradingState", "UniverseIdentityBinding", "UniverseRevisionInput", "UniverseStatus",
    "UniverseStatusInput", "UniverseVenue", "canonical_symbol_for", "coerce_venue", "compose_universe_status",
    "identity_binding_fingerprint", "map_canonical_symbol", "normalize_universe_timestamp",
    "parse_canonical_symbol", "parse_source_temporal", "payload_fingerprint",
    "universe_status_matrix_v1", "validate_knowledge_cutoff_at", "validate_official_code",
]


def map_canonical_symbol(
    *, venue: UniverseVenue | str,
    official_code: str,
    approved_scope: bool,
    identity_verified: bool,
    security_type: str | None = None,
) -> dict[str, Any]:
    """Apply ``universe_symbol_mapping_v1`` without inferring security type."""
    venue_value = coerce_venue(venue)
    try:
        code = validate_official_code(official_code)
    except ValueError:
        return {"canonical_symbol": None, "mapping_basis": None, "reason": "canonical_mapping_unverified"}
    if not approved_scope or not identity_verified:
        return {"canonical_symbol": None, "mapping_basis": None, "reason": "canonical_mapping_unverified"}
    return {
        "canonical_symbol": canonical_symbol_for(venue_value, code),
        "mapping_basis": "approved_resource_scope",
        "security_type": str(security_type or "unknown").strip() or "unknown",
        "reason": None,
    }
