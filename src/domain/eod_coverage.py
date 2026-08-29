"""Phase 15 historical EOD coverage visibility contracts.

The coverage surface is deliberately diagnostic.  It reports what the
cutoff-bounded evidence can show for a caller supplied trade date; it does not
assert source completeness, missingness, quality, or an investment decision.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.domain.eod_close import parse_iso_date
from src.domain.universe import validate_knowledge_cutoff_at


EOD_COVERAGE_VISIBILITY_CONTRACT_VERSION = "eod_coverage_visibility_v1"
EOD_COVERAGE_DENOMINATOR_CONTRACT_VERSION = "eod_coverage_denominator_v1"
EOD_COVERAGE_STATUS_POLICY_VERSION = "eod_coverage_visibility_status_v1"
EOD_COVERAGE_MODE = "as_of"
EOD_COVERAGE_ORDER_VERSION = (
    "venue_asc_official_code_asc_identity_epoch_asc_item_kind_asc_stable_id_asc_v1"
)
EOD_COVERAGE_CUTOFF_POLICY = {
    "type": "aware_timestamp",
    "timezone": "UTC",
    "no_end_of_day_expansion": True,
    "target_date_timezone": "Asia/Taipei",
}

TWSE_EOD_RESOURCE_ID = "twse.eod.stock_day_all"
TPEX_EOD_RESOURCE_ID = "tpex.eod.daily_close_quotes"
TWSE_EOD_SOURCE_SCOPE = "twse_whole_market_daily_close"
TPEX_EOD_SOURCE_SCOPE = "tpex_mainboard_daily_close_quotes_without_fixed_price"

_TAIPEI = ZoneInfo("Asia/Taipei")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CURSOR_CHECKSUM_PREFIX = b"eod-coverage-cursor-v1:"


class CoverageItemKind(str, Enum):
    DENOMINATOR_CANDIDATE = "denominator_candidate"
    SOURCE_OBSERVATION_ORPHAN = "source_observation_orphan"


class DenominatorMembership(str, Enum):
    EXPECTED = "expected"
    EXCLUDED = "excluded"
    UNRESOLVED = "unresolved"


class CoverageStatus(str, Enum):
    OBSERVED_ELIGIBLE = "observed_eligible"
    OBSERVED_INELIGIBLE = "observed_ineligible"
    NOT_OBSERVED_UNPROVEN = "not_observed_unproven"
    SOURCE_PARTIAL = "source_partial"
    SOURCE_UNKNOWN = "source_unknown"
    SOURCE_BLOCKED = "source_blocked"
    EXCLUDED_BY_LIFECYCLE = "excluded_by_lifecycle"
    EXCLUDED_BY_OPERATIONAL_STATE = "excluded_by_operational_state"
    EXCLUDED_BY_PRODUCT_SCOPE = "excluded_by_product_scope"
    EXCLUDED_BY_SOURCE_SCOPE = "excluded_by_source_scope"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    CLASSIFICATION_UNRESOLVED = "classification_unresolved"
    SOURCE_OBSERVATION_UNMAPPED = "source_observation_unmapped"


class CoverageSourceState(str, Enum):
    USABLE = "usable"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class CoverageAggregateAssertionState(str, Enum):
    NOT_PROVEN = "not_proven"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class CoverageTopLevelStatus(str, Enum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class CoverageCursorError(ValueError):
    """A malformed, corrupt, mismatched, or impossible opaque cursor."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def resource_for_venue(venue: str) -> str:
    normalized = str(venue).strip().upper()
    if normalized == "TWSE":
        return TWSE_EOD_RESOURCE_ID
    if normalized == "TPEX":
        return TPEX_EOD_RESOURCE_ID
    raise ValueError("venue must be TWSE or TPEX")


def source_scope_for_venue(venue: str) -> str:
    normalized = str(venue).strip().upper()
    if normalized == "TWSE":
        return TWSE_EOD_SOURCE_SCOPE
    if normalized == "TPEX":
        return TPEX_EOD_SOURCE_SCOPE
    raise ValueError("venue must be TWSE or TPEX")


def _local_date_from_cutoff(cutoff: str) -> date:
    return datetime.fromisoformat(cutoff.replace("Z", "+00:00")).astimezone(_TAIPEI).date()


def validate_target_trade_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()):
        raise ValueError("source_trade_date must be YYYY-MM-DD")
    try:
        return parse_iso_date(value.strip(), "source_trade_date")
    except ValueError as exc:
        raise ValueError("source_trade_date must be YYYY-MM-DD") from exc


def validate_d_k(*, source_trade_date: str, knowledge_cutoff_at: str) -> tuple[str, str]:
    target_date = validate_target_trade_date(source_trade_date)
    cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
    if target_date > _local_date_from_cutoff(cutoff).isoformat():
        raise ValueError("target_trade_date_after_cutoff")
    return target_date, cutoff


@dataclass(frozen=True)
class CoverageRequest:
    """Validated caller request; no implicit latest-date behavior exists."""

    venue: str
    source_trade_date: str
    knowledge_cutoff_at: str
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        venue = str(self.venue).strip().upper()
        if venue not in {"TWSE", "TPEX"}:
            raise ValueError("venue must be TWSE or TPEX")
        target_date, cutoff = validate_d_k(
            source_trade_date=self.source_trade_date,
            knowledge_cutoff_at=self.knowledge_cutoff_at,
        )
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.cursor is not None and (not isinstance(self.cursor, str) or not self.cursor.strip()):
            raise ValueError("cursor must be a non-empty opaque token")
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "source_trade_date", target_date)
        object.__setattr__(self, "knowledge_cutoff_at", cutoff)
        object.__setattr__(self, "cursor", self.cursor.strip() if self.cursor else None)

    @property
    def resource_id(self) -> str:
        return resource_for_venue(self.venue)

    @property
    def source_scope(self) -> str:
        return source_scope_for_venue(self.venue)

    def cursor_context(self) -> dict[str, Any]:
        return {
            "contract_version": EOD_COVERAGE_VISIBILITY_CONTRACT_VERSION,
            "mode": EOD_COVERAGE_MODE,
            "venue": self.venue,
            "resource_id": self.resource_id,
            "source_scope": self.source_scope,
            "source_trade_date": self.source_trade_date,
            "knowledge_cutoff_at": self.knowledge_cutoff_at,
            "filters": {},
            "order_version": EOD_COVERAGE_ORDER_VERSION,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class CoverageCursor:
    """A public deterministic continuation position, not an auth token.

    The checksum catches accidental corruption and the exact context/tuple
    checks reject malformed or stale continuations.  It is intentionally not
    cryptographic authentication, anti-forgery, or authorization: a client
    that knows this public format can recompute its checksum.
    """

    context: Mapping[str, Any]
    last_key: tuple[str, str, int | None, str, str]

    def payload(self) -> dict[str, Any]:
        venue, official_code, identity_epoch, item_kind, stable_id = self.last_key
        return {
            "context": dict(self.context),
            "last_key": {
                "venue": venue,
                "official_code": official_code,
                "identity_epoch": identity_epoch,
                "identity_epoch_null_rank": 1 if identity_epoch is None else 0,
                "item_kind": item_kind,
                "stable_id": stable_id,
            },
        }

    def encode(self) -> str:
        payload_text = _canonical_json(self.payload())
        checksum = hashlib.sha256(_CURSOR_CHECKSUM_PREFIX + payload_text.encode("utf-8")).hexdigest()
        envelope = _canonical_json({"checksum": checksum, "payload": self.payload()}).encode("utf-8")
        return base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _cursor_error(code: str) -> CoverageCursorError:
    return CoverageCursorError(code)


def _validate_last_key(value: Any, *, request: CoverageRequest) -> tuple[str, str, int | None, str, str]:
    if not isinstance(value, dict):
        raise _cursor_error("cursor_impossible_tuple")
    expected = {
        "venue", "official_code", "identity_epoch", "identity_epoch_null_rank",
        "item_kind", "stable_id",
    }
    if set(value) != expected:
        raise _cursor_error("cursor_impossible_tuple")
    venue = value.get("venue")
    code = value.get("official_code")
    epoch = value.get("identity_epoch")
    null_rank = value.get("identity_epoch_null_rank")
    item_kind = value.get("item_kind")
    stable_id = value.get("stable_id")
    if venue != request.venue:
        raise _cursor_error("cursor_context_mismatch")
    if not isinstance(code, str) or not code.strip() or code != code.strip() or len(code) > 64:
        raise _cursor_error("cursor_impossible_tuple")
    if epoch is not None and (isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1):
        raise _cursor_error("cursor_impossible_tuple")
    if null_rank != (1 if epoch is None else 0):
        raise _cursor_error("cursor_impossible_tuple")
    if item_kind not in {item.value for item in CoverageItemKind}:
        raise _cursor_error("cursor_impossible_tuple")
    if item_kind == CoverageItemKind.DENOMINATOR_CANDIDATE.value and epoch is None:
        raise _cursor_error("cursor_impossible_tuple")
    if not isinstance(stable_id, str) or not stable_id.strip():
        raise _cursor_error("cursor_impossible_tuple")
    return venue, code, epoch, item_kind, stable_id


def decode_cursor(token: str, *, request: CoverageRequest) -> CoverageCursor:
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
        canonical_envelope = _canonical_json(envelope).encode("utf-8")
        canonical_token = base64.urlsafe_b64encode(canonical_envelope).decode("ascii").rstrip("=")
    except (TypeError, ValueError):
        raise _cursor_error("cursor_malformed") from None
    if canonical_token != token:
        raise _cursor_error("cursor_malformed")
    checksum = envelope.get("checksum")
    payload = envelope.get("payload")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum) or not isinstance(payload, dict):
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
    return CoverageCursor(context=request.cursor_context(), last_key=last_key)


def normalize_reason_codes(reasons: Iterable[str]) -> list[str]:
    values = {str(reason).strip() for reason in reasons if str(reason).strip()}
    return sorted(values)


def eod_coverage_visibility_status_v1(
    *,
    source_state: str,
    denominator_candidate_count: int,
    denominator_expected_count: int,
    denominator_excluded_count: int,
    denominator_unresolved_count: int,
    denominator_blocked: bool = False,
) -> dict[str, str | bool]:
    """Apply the locked top-level status precedence.

    The two completeness-proof fields are intentionally fixed false.  A
    usable response is descriptive evidence visibility, never a completeness
    assertion.
    """

    try:
        source = CoverageSourceState(str(source_state))
    except ValueError:
        source = CoverageSourceState.BLOCKED
    if source is CoverageSourceState.BLOCKED or denominator_blocked:
        status = CoverageTopLevelStatus.BLOCKED
        assertion = CoverageAggregateAssertionState.BLOCKED
    elif (
        denominator_candidate_count == 0
        or denominator_unresolved_count == denominator_candidate_count
    ):
        status = CoverageTopLevelStatus.INSUFFICIENT_DATA
        assertion = CoverageAggregateAssertionState.UNKNOWN
    elif source is CoverageSourceState.PARTIAL:
        status = CoverageTopLevelStatus.PARTIAL
        assertion = CoverageAggregateAssertionState.PARTIAL
    elif source is CoverageSourceState.UNKNOWN:
        status = CoverageTopLevelStatus.UNKNOWN
        assertion = CoverageAggregateAssertionState.UNKNOWN
    else:
        status = CoverageTopLevelStatus.AVAILABLE
        assertion = CoverageAggregateAssertionState.NOT_PROVEN
    return {
        "status": status.value,
        "aggregate_assertion_state": assertion.value,
        "aggregate_completeness_proven": False,
    }


_PUBLIC_ITEM_FIELDS = (
    "item_kind",
    "venue",
    "official_code",
    "canonical_symbol",
    "identity_epoch",
    "denominator_membership",
    "coverage_status",
    "reason_codes",
    "listing_status",
    "trading_state",
    "classification_status",
    "product_scope",
    "observed_trade_date",
    "observed_status",
    "source_record_reference",
)


def safe_item_dto(item: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize only the Phase 15 item allowlist."""

    result = {key: item.get(key) for key in _PUBLIC_ITEM_FIELDS}
    result["reason_codes"] = normalize_reason_codes(item.get("reason_codes", ()))
    result["item_kind"] = str(result["item_kind"])
    if result["item_kind"] == CoverageItemKind.SOURCE_OBSERVATION_ORPHAN.value:
        result["denominator_membership"] = None
    return result


def safe_source_dto(source: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize safe provenance without internal IDs or financial values."""

    return {
        "provider": source.get("provider"),
        "resource_id": source.get("resource_id"),
        "source_scope": source.get("source_scope"),
        "source_record_reference": source.get("source_record_reference"),
        "source_trade_date": source.get("source_trade_date"),
        "available_at": source.get("available_at"),
        "ingested_at": source.get("ingested_at"),
        "source_status": source.get("source_status"),
        "coverage_state": source.get("coverage_state"),
        "partial_proof_present": bool(source.get("partial_proof_present", False)),
        "source_scope_completeness_proven": False,
        "reason_codes": normalize_reason_codes(source.get("reason_codes", ())),
    }


def safe_coverage_response(
    *,
    request: CoverageRequest,
    status: Mapping[str, Any],
    source: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    items: Iterable[Mapping[str, Any]],
    next_cursor: str | None,
) -> dict[str, Any]:
    """Build the stable public DTO and reject accidental internal fields."""

    item_values = [safe_item_dto(item) for item in items]
    return {
        "contract_version": EOD_COVERAGE_VISIBILITY_CONTRACT_VERSION,
        "status": status["status"],
        "aggregate_assertion_state": status["aggregate_assertion_state"],
        "aggregate_completeness_proven": False,
        "denominator_policy_version": EOD_COVERAGE_DENOMINATOR_CONTRACT_VERSION,
        "status_policy_version": EOD_COVERAGE_STATUS_POLICY_VERSION,
        "request": {
            "venue": request.venue,
            "resource_id": request.resource_id,
            "source_scope": request.source_scope,
            "source_trade_date": request.source_trade_date,
            "knowledge_cutoff_at": request.knowledge_cutoff_at,
            "mode": EOD_COVERAGE_MODE,
            "cutoff_policy": dict(EOD_COVERAGE_CUTOFF_POLICY),
        },
        "source": safe_source_dto(source),
        "aggregate": {
            "denominator_expected_count": int(aggregate.get("denominator_expected_count", 0)),
            "denominator_excluded_count": int(aggregate.get("denominator_excluded_count", 0)),
            "denominator_unresolved_count": int(aggregate.get("denominator_unresolved_count", 0)),
            "item_status_counts": dict(aggregate.get("item_status_counts", {})),
            "source_observation_orphan_count": int(aggregate.get("source_observation_orphan_count", 0)),
        },
        "items": item_values,
        "next_cursor": next_cursor,
        "limit": request.limit,
    }


__all__ = [
    "CoverageAggregateAssertionState",
    "CoverageCursor",
    "CoverageCursorError",
    "CoverageItemKind",
    "CoverageRequest",
    "CoverageSourceState",
    "CoverageStatus",
    "DenominatorMembership",
    "EOD_COVERAGE_CUTOFF_POLICY",
    "EOD_COVERAGE_DENOMINATOR_CONTRACT_VERSION",
    "EOD_COVERAGE_MODE",
    "EOD_COVERAGE_ORDER_VERSION",
    "EOD_COVERAGE_STATUS_POLICY_VERSION",
    "EOD_COVERAGE_VISIBILITY_CONTRACT_VERSION",
    "TPEX_EOD_RESOURCE_ID",
    "TPEX_EOD_SOURCE_SCOPE",
    "TWSE_EOD_RESOURCE_ID",
    "TWSE_EOD_SOURCE_SCOPE",
    "decode_cursor",
    "eod_coverage_visibility_status_v1",
    "normalize_reason_codes",
    "resource_for_venue",
    "safe_coverage_response",
    "safe_item_dto",
    "safe_source_dto",
    "source_scope_for_venue",
    "validate_d_k",
    "validate_target_trade_date",
]
