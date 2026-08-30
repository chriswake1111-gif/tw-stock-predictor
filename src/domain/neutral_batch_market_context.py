"""Domain contract for the Phase 16 neutral batch market context.

This module contains only validation, deterministic status policies, cursor
encoding, and public DTO allowlists.  It deliberately has no database or
network access so the request path cannot silently acquire a collector or
write side effect.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.domain.eod_close import (
    EOD_REASON_CODES,
    EodFreshnessState,
    eod_close_status_matrix_v1,
    normalize_decimal_text,
    parse_iso_date,
)
from src.domain.eod_coverage import (
    CoverageItemKind,
    CoverageSourceState,
    CoverageStatus,
    DenominatorMembership,
    normalize_reason_codes,
    resource_for_venue,
    source_scope_for_venue,
    validate_target_trade_date,
)
from src.domain.universe import validate_knowledge_cutoff_at


NEUTRAL_BATCH_MARKET_CONTEXT_CONTRACT_VERSION = "neutral_batch_market_context_v1"
NEUTRAL_BATCH_MARKET_CONTEXT_D_K_VERSION = "neutral_batch_market_context_d_k_v1"
NEUTRAL_BATCH_MARKET_CONTEXT_STATUS_VERSION = "neutral_batch_market_context_status_v1"
NEUTRAL_BATCH_MARKET_CONTEXT_ASSEMBLY_VERSION = "neutral_batch_market_context_assembly_v1"
NEUTRAL_BATCH_MARKET_CONTEXT_ORDER_VERSION = "neutral_batch_market_context_order_v1"
NEUTRAL_BATCH_MARKET_CONTEXT_CURSOR_VERSION = "neutral_batch_market_context_cursor_v1"
EOD_CLOSE_CONTEXT_CONTRACT_VERSION = "eod_close_context_v1"
EOD_CLOSE_CONTEXT_PRICE_SEMANTICS_VERSION = "official_reported_close_v1"
EOD_COVERAGE_DENOMINATOR_CONTRACT_VERSION = "eod_coverage_denominator_v1"
UNIVERSE_STATUS_MATRIX_VERSION = "universe_status_matrix_v1"
EOD_CLOSE_CONTEXT_SOURCE_STATUS_VERSION = "eod_close_context_v1"

NEUTRAL_BATCH_MARKET_CONTEXT_MODE = "historical_as_of"
NEUTRAL_BATCH_MARKET_CONTEXT_CUTOFF_POLICY = {
    "target_date_timezone": "Asia/Taipei",
    "knowledge_cutoff_timezone": "UTC",
    "no_end_of_day_expansion": True,
}

_TAIPEI = ZoneInfo("Asia/Taipei")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CURSOR_CHECKSUM_PREFIX = b"neutral-batch-market-context-cursor-v1:"
_VENUE_ORDER = {"TWSE": 0, "TPEX": 1}
_VENUE_SCOPES = {
    "TWSE": ("TWSE",),
    "TPEX": ("TPEX",),
    "TWSE_TPEX": ("TWSE", "TPEX"),
}
_ITEM_ORDER = {
    CoverageItemKind.DENOMINATOR_CANDIDATE.value: 0,
    CoverageItemKind.SOURCE_OBSERVATION_ORPHAN.value: 1,
}
_VALID_SOURCE_STATES = {state.value for state in CoverageSourceState}
_PHASE14_REASON_SET = frozenset(EOD_REASON_CODES)
_PHASE14_REASON_MAP = {
    "source_empty": "source_empty",
    "no_exact_D": "no_same_day_snapshot",
    "source_unknown": "no_same_day_snapshot",
    "source_blocked": "latest_revision_blocked",
    "source_date_in_future_or_invalid": "source_date_in_future_or_invalid",
    "provider_error": "provider_error",
    "schema_changed": "schema_changed",
    "latest_revision_blocked": "latest_revision_blocked",
    "source_revoke_without_replacement": "source_revoke_without_replacement",
    "source_partial": "partial_venue_payload",
    "identity_unresolved": "identity_mapping_unverified",
    "identity_mapping_unverified": "identity_mapping_unverified",
    "identity_d_applicability_unresolved": "identity_epoch_ambiguous",
    "event_d_applicability_unresolved": "identity_epoch_ambiguous",
    "classification_unresolved": "classification_evidence_missing",
    "classification_d_applicability_unresolved": "classification_evidence_missing",
    "excluded_by_lifecycle": "unsupported_security_type",
    "not_yet_listed_on_source_trade_date": "unsupported_security_type",
    "excluded_by_operational_state": "instrument_suspended",
    "excluded_by_product_scope": "unsupported_security_type",
    "close_unusable": "close_missing",
    "volume_unusable": "volume_unusable",
    "official_zero_volume_not_public_eligible": "official_zero_volume_not_public_eligible",
    "product_scope_unverified": "classification_evidence_missing",
    "currency_unit_unproven": "currency_unit_unproven",
    "observation_source_scope_mismatch": "source_usage_not_approved",
}


def _normalize_phase16_decimal(value: Any) -> tuple[str | None, Any, str]:
    """Normalize SQLite text/numeric affinity without routing through float."""

    if value is None:
        return normalize_decimal_text(None)
    return normalize_decimal_text(value if isinstance(value, str) else str(value))


class NeutralBatchMarketContextCursorError(ValueError):
    """A malformed, corrupt, mismatched, or impossible continuation token."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _local_date_from_cutoff(cutoff: str) -> date:
    return datetime.fromisoformat(cutoff.replace("Z", "+00:00")).astimezone(_TAIPEI).date()


def validate_market_date(value: str) -> str:
    """Validate a caller-supplied Taipei market date without date shifting."""

    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()):
        raise ValueError("market_date must be YYYY-MM-DD")
    try:
        return parse_iso_date(value.strip(), "market_date")
    except ValueError as exc:
        raise ValueError("market_date must be YYYY-MM-DD") from exc


def validate_market_date_and_cutoff(*, market_date: str, knowledge_cutoff_at: str) -> tuple[str, str]:
    target = validate_market_date(market_date)
    cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
    if target > _local_date_from_cutoff(cutoff).isoformat():
        raise ValueError("market_date_after_cutoff")
    return target, cutoff


@dataclass(frozen=True)
class VenueMapping:
    venue: str
    resource_id: str
    source_scope: str
    provider: str

    def to_dict(self) -> dict[str, str]:
        return {
            "venue": self.venue,
            "resource_id": self.resource_id,
            "source_scope": self.source_scope,
            "provider": self.provider,
        }


def venue_mapping(venue: str) -> VenueMapping:
    normalized = str(venue).strip().upper()
    if normalized not in _VENUE_ORDER:
        raise ValueError("venue_scope must be TWSE, TPEX, or TWSE_TPEX")
    return VenueMapping(
        venue=normalized,
        resource_id=resource_for_venue(normalized),
        source_scope=source_scope_for_venue(normalized),
        provider="TWSE" if normalized == "TWSE" else "TPEx",
    )


def venue_order(venue: str) -> int:
    """Return the canonical global order used by every venue cursor."""

    normalized = str(venue).strip().upper()
    if normalized not in _VENUE_ORDER:
        raise ValueError("item venue is not supported")
    return _VENUE_ORDER[normalized]


@dataclass(frozen=True)
class NeutralBatchMarketContextRequest:
    """Validated D×K request; no implicit latest-date behavior exists."""

    market_date: str
    knowledge_cutoff_at: str
    venue_scope: str
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        market_date, cutoff = validate_market_date_and_cutoff(
            market_date=self.market_date,
            knowledge_cutoff_at=self.knowledge_cutoff_at,
        )
        scope = str(self.venue_scope).strip().upper()
        if scope not in _VENUE_SCOPES:
            raise ValueError("venue_scope must be TWSE, TPEX, or TWSE_TPEX")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or not self.cursor.strip()
        ):
            raise ValueError("cursor must be a non-empty opaque token")
        object.__setattr__(self, "market_date", market_date)
        object.__setattr__(self, "knowledge_cutoff_at", cutoff)
        object.__setattr__(self, "venue_scope", scope)
        object.__setattr__(self, "cursor", self.cursor.strip() if self.cursor else None)

    @property
    def venues(self) -> tuple[str, ...]:
        return _VENUE_SCOPES[self.venue_scope]

    @property
    def venue_mappings(self) -> tuple[VenueMapping, ...]:
        return tuple(venue_mapping(venue) for venue in self.venues)

    @property
    def d_k_policy_version(self) -> str:
        return NEUTRAL_BATCH_MARKET_CONTEXT_D_K_VERSION

    def cursor_context(self) -> dict[str, Any]:
        return {
            "contract_version": NEUTRAL_BATCH_MARKET_CONTEXT_CONTRACT_VERSION,
            "mode": NEUTRAL_BATCH_MARKET_CONTEXT_MODE,
            "market_date": self.market_date,
            "knowledge_cutoff_at": self.knowledge_cutoff_at,
            "venue_scope": self.venue_scope,
            "venue_mapping": [mapping.to_dict() for mapping in self.venue_mappings],
            "filters": {},
            "order_version": NEUTRAL_BATCH_MARKET_CONTEXT_ORDER_VERSION,
            "limit": self.limit,
        }


# Short aliases keep the repository/service code readable while retaining the
# full contract name for callers and documentation.
NeutralBatchRequest = NeutralBatchMarketContextRequest


def _cursor_error(code: str) -> NeutralBatchMarketContextCursorError:
    return NeutralBatchMarketContextCursorError(code)


def item_order_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the locked total order, including explicit NULL ranks."""

    venue = str(item.get("venue") or "").strip().upper()
    venue_rank = venue_order(venue)
    official_code = item.get("official_code")
    code_rank = 1 if official_code is None else 0
    code_value = "" if official_code is None else str(official_code)
    epoch = item.get("identity_epoch")
    epoch_rank = 1 if epoch is None else 0
    epoch_value = 2147483647 if epoch is None else int(epoch)
    item_kind = str(item.get("item_kind") or "")
    if item_kind not in _ITEM_ORDER:
        raise ValueError("item kind is not supported")
    return (
        venue_rank,
        venue,
        code_rank,
        code_value,
        epoch_rank,
        epoch_value,
        _ITEM_ORDER[item_kind],
        str(item.get("_stable_id") or item.get("stable_id") or ""),
    )


def _validate_last_key(value: Any, *, request: NeutralBatchMarketContextRequest) -> tuple[Any, ...]:
    if not isinstance(value, dict):
        raise _cursor_error("cursor_impossible_tuple")
    expected = {
        "venue_order",
        "venue",
        "official_code_null_rank",
        "official_code",
        "identity_epoch_null_rank",
        "identity_epoch",
        "item_kind",
        "stable_id",
    }
    if set(value) != expected:
        raise _cursor_error("cursor_impossible_tuple")
    venue = value.get("venue")
    if venue not in request.venues or value.get("venue_order") != venue_order(venue):
        raise _cursor_error("cursor_context_mismatch")
    code = value.get("official_code")
    code_rank = value.get("official_code_null_rank")
    if code_rank not in (0, 1) or (code_rank == 1 and code is not None):
        raise _cursor_error("cursor_impossible_tuple")
    if code_rank == 0 and (
        not isinstance(code, str) or not code.strip() or code != code.strip() or len(code) > 64
    ):
        raise _cursor_error("cursor_impossible_tuple")
    epoch = value.get("identity_epoch")
    epoch_rank = value.get("identity_epoch_null_rank")
    if epoch_rank not in (0, 1) or (epoch_rank == 1 and epoch is not None):
        raise _cursor_error("cursor_impossible_tuple")
    if epoch_rank == 0 and (
        isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1
    ):
        raise _cursor_error("cursor_impossible_tuple")
    item_kind = value.get("item_kind")
    if item_kind not in _ITEM_ORDER:
        raise _cursor_error("cursor_impossible_tuple")
    if item_kind == CoverageItemKind.DENOMINATOR_CANDIDATE.value and epoch is None:
        raise _cursor_error("cursor_impossible_tuple")
    stable_id = value.get("stable_id")
    if not isinstance(stable_id, str) or not stable_id.strip() or len(stable_id) > 256:
        raise _cursor_error("cursor_impossible_tuple")
    return (
        value["venue_order"],
        venue,
        code_rank,
        code if code_rank == 0 else "",
        epoch_rank,
        epoch if epoch_rank == 0 else 2147483647,
        _ITEM_ORDER[item_kind],
        stable_id,
    )


@dataclass(frozen=True)
class NeutralBatchMarketContextCursor:
    context: Mapping[str, Any]
    last_key: tuple[Any, ...]

    def payload(self) -> dict[str, Any]:
        venue_order, venue, code_rank, code, epoch_rank, epoch, item_order, stable_id = self.last_key
        item_kind = next(kind for kind, order in _ITEM_ORDER.items() if order == item_order)
        return {
            "context": dict(self.context),
            "last_key": {
                "venue_order": venue_order,
                "venue": venue,
                "official_code_null_rank": code_rank,
                "official_code": None if code_rank else code,
                "identity_epoch_null_rank": epoch_rank,
                "identity_epoch": None if epoch_rank else epoch,
                "item_kind": item_kind,
                "stable_id": stable_id,
            },
        }

    def encode(self) -> str:
        payload = self.payload()
        payload_text = _canonical_json(payload)
        checksum = hashlib.sha256(
            _CURSOR_CHECKSUM_PREFIX + payload_text.encode("utf-8")
        ).hexdigest()
        envelope = _canonical_json({"checksum": checksum, "payload": payload}).encode("utf-8")
        return base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")


NeutralBatchCursor = NeutralBatchMarketContextCursor


def cursor_for_item(
    request: NeutralBatchMarketContextRequest,
    item: Mapping[str, Any],
) -> str:
    order = item_order_key(item)
    return NeutralBatchMarketContextCursor(
        context=request.cursor_context(),
        last_key=order,
    ).encode()


def decode_neutral_batch_cursor(
    token: str,
    *,
    request: NeutralBatchMarketContextRequest,
) -> NeutralBatchMarketContextCursor:
    if not isinstance(token, str) or not token or len(token) > 4096 or not _TOKEN_RE.fullmatch(token):
        raise _cursor_error("cursor_malformed")
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        envelope = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise _cursor_error("cursor_malformed") from None
    if not isinstance(envelope, dict) or set(envelope) != {"checksum", "payload"}:
        raise _cursor_error("cursor_malformed")
    try:
        canonical = base64.urlsafe_b64encode(
            _canonical_json(envelope).encode("utf-8")
        ).decode("ascii").rstrip("=")
    except (TypeError, ValueError):
        raise _cursor_error("cursor_malformed") from None
    if canonical != token:
        raise _cursor_error("cursor_malformed")
    checksum = envelope.get("checksum")
    payload = envelope.get("payload")
    if (
        not isinstance(checksum, str)
        or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        or not isinstance(payload, dict)
    ):
        raise _cursor_error("cursor_malformed")
    expected_checksum = hashlib.sha256(
        _CURSOR_CHECKSUM_PREFIX + _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    if checksum != expected_checksum:
        raise _cursor_error("cursor_checksum_mismatch")
    if set(payload) != {"context", "last_key"} or not isinstance(payload.get("context"), dict):
        raise _cursor_error("cursor_malformed")
    if payload["context"] != request.cursor_context():
        raise _cursor_error("cursor_context_mismatch")
    last_key = _validate_last_key(payload.get("last_key"), request=request)
    return NeutralBatchMarketContextCursor(
        context=request.cursor_context(),
        last_key=last_key,
    )


def _counts(aggregate: Mapping[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(aggregate.get("denominator_candidate_count", 0)),
        int(aggregate.get("denominator_expected_count", 0)),
        int(aggregate.get("denominator_excluded_count", 0)),
        int(aggregate.get("denominator_unresolved_count", 0)),
    )


def neutral_batch_market_context_assembly_v1(
    *,
    source_state: str,
    aggregate: Mapping[str, Any],
    denominator_projection_state: str | None = None,
    denominator_blocked: bool = False,
    partial_proof_present: bool = False,
) -> dict[str, Any]:
    """Apply the locked per-venue 4x4 status matrix."""

    source = str(source_state or CoverageSourceState.UNKNOWN.value)
    if source not in _VALID_SOURCE_STATES:
        source = CoverageSourceState.BLOCKED.value
    candidate_count, _, _, unresolved_count = _counts(aggregate)
    projection_state = denominator_projection_state
    if projection_state is None:
        if denominator_blocked:
            projection_state = "blocked"
        elif candidate_count == 0:
            projection_state = "empty"
        elif unresolved_count == candidate_count:
            projection_state = "entirely_unresolved"
        else:
            projection_state = "usable"
    if projection_state not in {"blocked", "empty", "entirely_unresolved", "usable"}:
        projection_state = "blocked"
    if source == CoverageSourceState.BLOCKED.value or projection_state == "blocked":
        status = "blocked"
        assertion = "blocked"
    elif projection_state in {"empty", "entirely_unresolved"}:
        status = "insufficient_data"
        assertion = "unknown"
    elif source == CoverageSourceState.PARTIAL.value and partial_proof_present:
        status = "partial"
        assertion = "partial"
    elif source == CoverageSourceState.PARTIAL.value:
        status = "unknown"
        assertion = "unknown"
    elif source == CoverageSourceState.UNKNOWN.value:
        status = "unknown"
        assertion = "unknown"
    else:
        status = "available"
        assertion = "not_proven"
    return {
        "status": status,
        "aggregate_assertion_state": assertion,
        "aggregate_completeness_proven": False,
        "status_policy_version": NEUTRAL_BATCH_MARKET_CONTEXT_STATUS_VERSION,
        "assembly_version": NEUTRAL_BATCH_MARKET_CONTEXT_ASSEMBLY_VERSION,
    }


def neutral_batch_market_context_status_v1(
    *,
    per_venue: Iterable[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce per-venue diagnostic states without making completeness claims."""

    venues = list(per_venue)
    candidate_count, _, _, unresolved_count = _counts(aggregate)
    statuses = {
        str(value.get("assembly_status") or value.get("status") or "unknown")
        for value in venues
    }
    if "blocked" in statuses:
        status, assertion = "blocked", "blocked"
    elif candidate_count == 0 or unresolved_count == candidate_count:
        status, assertion = "insufficient_data", "unknown"
    elif "partial" in statuses:
        status, assertion = "partial", "partial"
    elif "available" in statuses and any(value != "available" for value in statuses):
        status, assertion = "partial", "partial"
    elif "unknown" in statuses or "insufficient_data" in statuses:
        status, assertion = "unknown", "unknown"
    else:
        status, assertion = "available", "not_proven"
    return {
        "status": status,
        "aggregate_assertion_state": assertion,
        "aggregate_completeness_proven": False,
        "status_policy_version": NEUTRAL_BATCH_MARKET_CONTEXT_STATUS_VERSION,
    }


# The shorter name is the normative plan/API spelling.
neutral_batch_context_assembly_v1 = neutral_batch_market_context_assembly_v1


def _phase14_reason_codes(item: Mapping[str, Any]) -> list[str]:
    """Translate Phase 15 diagnostics into the Phase 14 close vocabulary."""

    reasons: list[str] = []

    def add(reason: Any) -> None:
        mapped = _PHASE14_REASON_MAP.get(str(reason).strip())
        if mapped in _PHASE14_REASON_SET and mapped not in reasons:
            reasons.append(mapped)

    for reason in item.get("reason_codes", ()) or ():
        add(reason)

    source_state = str(item.get("_source_state") or CoverageSourceState.UNKNOWN.value)
    if source_state == CoverageSourceState.BLOCKED.value:
        add(item.get("_source_reason") or "source_blocked")
    elif source_state == CoverageSourceState.PARTIAL.value:
        add("source_partial")
    elif source_state == CoverageSourceState.UNKNOWN.value:
        add(item.get("_source_reason") or "no_exact_D")

    close_text, close_decimal, close_state = _normalize_phase16_decimal(
        item.get("_observation_close_value")
    )
    del close_text
    if close_state != "valid" or close_decimal is None or close_decimal <= 0:
        add("close_unusable")

    volume_text, volume_decimal, volume_state = _normalize_phase16_decimal(
        item.get("_observation_volume_value")
    )
    del volume_text
    if volume_state != "valid" or volume_decimal is None:
        add("volume_unusable")
    elif volume_decimal == 0:
        if "volume_unusable" in reasons:
            reasons.remove("volume_unusable")
        add("official_zero_volume_not_public_eligible")

    currency = str(item.get("_observation_currency") or "").strip().upper()
    unit = str(item.get("_observation_unit") or "").strip()
    if currency != "TWD":
        add("foreign_currency_not_supported" if currency else "currency_unit_unproven")
    elif unit != "TWD_per_share":
        add("currency_unit_unproven")

    observed_product_scope = str(
        item.get("_observation_product_scope") or ""
    ).strip()
    if observed_product_scope and observed_product_scope != "supported_stock":
        add("excluded_by_product_scope")
    if item.get("product_scope") == "not_applicable":
        add("excluded_by_product_scope")
    if str(item.get("classification_status") or "missing") != "accepted":
        add("classification_unresolved")
    if str(item.get("public_eligibility_status") or "") == "awaiting_review":
        add("classification_unresolved")

    observed_scope = item.get("_observation_source_scope")
    expected_scope = item.get("_source_scope")
    if observed_scope != expected_scope:
        add("observation_source_scope_mismatch")
    return reasons


def _phase14_freshness_state(item: Mapping[str, Any]) -> str:
    """Map raw observation quality to the Phase 14 freshness enum."""

    source_state = str(item.get("_source_state") or CoverageSourceState.UNKNOWN.value)
    if source_state == CoverageSourceState.BLOCKED.value or item.get("item_state") == "blocked":
        return EodFreshnessState.BLOCKED.value

    raw_quality = str(item.get("_observation_quality_status") or "").strip().lower()
    if raw_quality == EodFreshnessState.BLOCKED.value:
        return EodFreshnessState.BLOCKED.value
    if raw_quality == EodFreshnessState.STALE.value:
        return EodFreshnessState.STALE.value
    if raw_quality == EodFreshnessState.UNKNOWN.value:
        return EodFreshnessState.UNKNOWN.value

    if (
        item.get("observed_trade_date")
        and source_state in {
            CoverageSourceState.USABLE.value,
            CoverageSourceState.PARTIAL.value,
        }
        and item.get("_source_trade_date") == item.get("observed_trade_date")
    ):
        return EodFreshnessState.CURRENT.value
    return EodFreshnessState.UNKNOWN.value


def _public_eod_close(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Expose only the Phase 14 close contract for an exact observed row."""

    if (
        item.get("item_kind") != CoverageItemKind.DENOMINATOR_CANDIDATE.value
        or not item.get("observed_trade_date")
    ):
        return None

    close_value, close_decimal, close_state = _normalize_phase16_decimal(
        item.get("_observation_close_value")
    )
    volume_value, volume_decimal, volume_state = _normalize_phase16_decimal(
        item.get("_observation_volume_value")
    )
    reasons = _phase14_reason_codes(item)
    eligible = (
        item.get("coverage_status") == CoverageStatus.OBSERVED_ELIGIBLE.value
        and item.get("item_state") == "available"
        and item.get("_source_state") == CoverageSourceState.USABLE.value
        and item.get("public_eligibility_status") == "eligible"
        and close_state == "valid"
        and close_decimal is not None
        and close_decimal > 0
        and volume_state == "valid"
        and volume_decimal is not None
        and volume_decimal > 0
        and item.get("_observation_product_scope") == "supported_stock"
        and item.get("_observation_currency") == "TWD"
        and item.get("_observation_unit") == "TWD_per_share"
        and item.get("_observation_source_scope") == item.get("_source_scope")
        and str(item.get("_observation_quality_status") or "").strip().lower() == "fresh"
        and not reasons
    )
    matrix = eod_close_status_matrix_v1(
        reasons,
        freshness_state=_phase14_freshness_state(item),
        current_complete=False,
        evidence_complete=eligible,
    )
    return {
        "contract_version": EOD_CLOSE_CONTEXT_CONTRACT_VERSION,
        "status": matrix["status"],
        "reason_codes": matrix["reason_codes"],
        "selected_trade_date": item.get("observed_trade_date"),
        "close_value": close_value if eligible else None,
        "currency": item.get("_observation_currency") if eligible else None,
        "unit": item.get("_observation_unit") if eligible else None,
        "price_semantics": EOD_CLOSE_CONTEXT_PRICE_SEMANTICS_VERSION if eligible else None,
        "freshness_state": matrix["freshness_state"],
        "public_eligibility_status": item.get("public_eligibility_status"),
    }


def safe_neutral_batch_item_dto(item: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize exactly the safe item surface; private projection keys stop here."""

    identity_epoch = item.get("identity_epoch")
    status = str(item.get("coverage_status") or CoverageStatus.SOURCE_UNKNOWN.value)
    return {
        "item_kind": str(item.get("item_kind") or ""),
        "item_state": item.get("item_state") or "unknown",
        "venue": item.get("venue"),
        "official_code": item.get("official_code"),
        "canonical_symbol": item.get("canonical_symbol"),
        "identity_epoch": identity_epoch,
        "identity_status": item.get("identity_status") or (
            "unresolved" if "unresolved" in str(item.get("identity_state") or "") else "resolved"
        ),
        "denominator_membership": item.get("denominator_membership"),
        "coverage_status": status,
        "reason_codes": normalize_reason_codes(item.get("reason_codes", ())),
        "listing_status": item.get("listing_status") or "unknown",
        "trading_state": item.get("trading_state") or "unknown",
        "d_applicability": item.get("d_applicability") or "unresolved",
        "classification_status": item.get("classification_status") or "missing",
        "public_eligibility_status": item.get("public_eligibility_status"),
        "product_scope": item.get("product_scope") or "needs_human_input",
        "observed_trade_date": item.get("observed_trade_date"),
        "observed_status": item.get("observed_status"),
        "source_record_reference": item.get("observed_source_record_reference"),
        "provenance": {
            "provider": item.get("_provider"),
            "resource_id": item.get("_resource_id"),
            "source_scope": item.get("_source_scope"),
            "source_trade_date": item.get("_source_trade_date"),
            "available_at": item.get("_source_available_at"),
            "ingested_at": item.get("_source_ingested_at"),
        },
        "eod_close": _public_eod_close(item),
    }


def safe_neutral_batch_source_dto(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "state": source.get("source_state") or "unknown",
        "status": source.get("source_status") or "unknown",
        "provider": source.get("provider"),
        "resource_id": source.get("resource_id"),
        "source_scope": source.get("source_scope"),
        "source_trade_date": source.get("source_trade_date"),
        "source_record_reference": source.get("source_record_reference"),
        "available_at": source.get("source_available_at"),
        "ingested_at": source.get("source_ingested_at"),
        "coverage_state": source.get("source_coverage_state"),
        "partial_proof_present": bool(source.get("source_proof_present", False)),
        "source_scope_completeness_proven": False,
        "reason_codes": normalize_reason_codes(source.get("reason_codes", ())),
    }


def safe_neutral_batch_aggregate_dto(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "denominator_candidate_count": int(aggregate.get("denominator_candidate_count", 0)),
        "denominator_expected_count": int(aggregate.get("denominator_expected_count", 0)),
        "denominator_excluded_count": int(aggregate.get("denominator_excluded_count", 0)),
        "denominator_unresolved_count": int(aggregate.get("denominator_unresolved_count", 0)),
        "source_observation_orphan_count": int(aggregate.get("source_observation_orphan_count", 0)),
        "item_status_counts": dict(aggregate.get("item_status_counts", {})),
        "aggregate_completeness_proven": False,
    }


def safe_neutral_batch_response(
    *,
    request: NeutralBatchMarketContextRequest,
    status: Mapping[str, Any],
    per_venue: Iterable[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
    items: Iterable[Mapping[str, Any]],
    next_cursor: str | None,
) -> dict[str, Any]:
    return {
        "contract_version": NEUTRAL_BATCH_MARKET_CONTEXT_CONTRACT_VERSION,
        "mode": NEUTRAL_BATCH_MARKET_CONTEXT_MODE,
        "request": {
            "market_date": request.market_date,
            "knowledge_cutoff_at": request.knowledge_cutoff_at,
            "venue_scope": request.venue_scope,
            "d_k_policy_version": request.d_k_policy_version,
            "order_version": NEUTRAL_BATCH_MARKET_CONTEXT_ORDER_VERSION,
        },
        "status": status["status"],
        "per_venue": {
            venue["venue"]: {
                "source": safe_neutral_batch_source_dto(venue["source"]),
                "assembly_status": venue["assembly_status"],
                "aggregate": safe_neutral_batch_aggregate_dto(venue["aggregate"]),
            }
            for venue in per_venue
        },
        "aggregate": safe_neutral_batch_aggregate_dto(aggregate),
        "items": [safe_neutral_batch_item_dto(item) for item in items],
        "limit": request.limit,
        "next_cursor": next_cursor,
    }


__all__ = [
    "EOD_CLOSE_CONTEXT_CONTRACT_VERSION",
    "EOD_CLOSE_CONTEXT_PRICE_SEMANTICS_VERSION",
    "NEUTRAL_BATCH_MARKET_CONTEXT_ASSEMBLY_VERSION",
    "NEUTRAL_BATCH_MARKET_CONTEXT_CONTRACT_VERSION",
    "NEUTRAL_BATCH_MARKET_CONTEXT_CURSOR_VERSION",
    "NEUTRAL_BATCH_MARKET_CONTEXT_CUTOFF_POLICY",
    "NEUTRAL_BATCH_MARKET_CONTEXT_D_K_VERSION",
    "NEUTRAL_BATCH_MARKET_CONTEXT_MODE",
    "NEUTRAL_BATCH_MARKET_CONTEXT_ORDER_VERSION",
    "NEUTRAL_BATCH_MARKET_CONTEXT_STATUS_VERSION",
    "NeutralBatchCursor",
    "NeutralBatchMarketContextCursor",
    "NeutralBatchMarketContextCursorError",
    "NeutralBatchMarketContextRequest",
    "NeutralBatchRequest",
    "VenueMapping",
    "cursor_for_item",
    "decode_neutral_batch_cursor",
    "item_order_key",
    "neutral_batch_market_context_assembly_v1",
    "neutral_batch_market_context_status_v1",
    "neutral_batch_context_assembly_v1",
    "safe_neutral_batch_aggregate_dto",
    "safe_neutral_batch_item_dto",
    "safe_neutral_batch_response",
    "safe_neutral_batch_source_dto",
    "validate_market_date",
    "validate_market_date_and_cutoff",
    "venue_mapping",
    "venue_order",
]
