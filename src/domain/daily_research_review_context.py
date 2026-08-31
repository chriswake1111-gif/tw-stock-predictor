"""Pure Phase 17 Daily Research Review Context contracts and reducers."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


DAILY_RESEARCH_REVIEW_CONTEXT_VERSION = "daily_research_review_context_v1"
DAILY_RESEARCH_REVIEW_CONTEXT_POLICY_VERSION = "daily_research_review_context_policy_v1"
DAILY_RESEARCH_REVIEW_STATUS_VERSION = "daily_research_review_status_v1"
DAILY_RESEARCH_WORKFLOW_TIME_VERSION = "daily_research_workflow_time_v1"
DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION = "daily_research_snapshot_selection_v1"
DAILY_RESEARCH_REASON_REGISTRY_VERSION = "daily_research_review_reason_registry_v1"
DAILY_RESEARCH_D_K_VERSION = "daily_research_review_d_k_v1"
DAILY_RESEARCH_ORDER_VERSION = "daily_research_review_order_v1"
DAILY_RESEARCH_CURSOR_VERSION = "daily_research_review_cursor_v1"
DAILY_RESEARCH_SNAPSHOT_INTEGRATION_VERSION = "daily_research_snapshot_integration_v1"
DAILY_RESEARCH_PREFLIGHT_VERSION = "daily_research_preflight_v1"
DAILY_BASELINE_SELECTION_POLICY_VERSION = "daily_research_baseline_selection_policy_v1"
DAILY_BASELINE_SELECTION_REASON_REGISTRY_VERSION = (
    "daily_research_baseline_selection_reason_registry_v1"
)

DAILY_CONTRACT_VERSIONS = (
    DAILY_RESEARCH_REVIEW_CONTEXT_VERSION,
    DAILY_RESEARCH_REVIEW_CONTEXT_POLICY_VERSION,
    DAILY_RESEARCH_REVIEW_STATUS_VERSION,
    DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
    DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
    DAILY_RESEARCH_REASON_REGISTRY_VERSION,
    DAILY_RESEARCH_D_K_VERSION,
    DAILY_RESEARCH_ORDER_VERSION,
    DAILY_RESEARCH_CURSOR_VERSION,
    DAILY_RESEARCH_SNAPSHOT_INTEGRATION_VERSION,
    DAILY_RESEARCH_PREFLIGHT_VERSION,
    DAILY_BASELINE_SELECTION_POLICY_VERSION,
    DAILY_BASELINE_SELECTION_REASON_REGISTRY_VERSION,
)

DAILY_REASON_CODES = (
    "no_snapshot",
    "snapshot_not_found",
    "baseline_not_set",
    "baseline_not_visible_at_cutoff",
    "stored_snapshot_changed",
    "snapshot_dependency_changed",
    "snapshot_stale",
    "eod_context_changed",
    "coverage_state_changed",
    "source_state_changed",
    "identity_state_changed",
    "identity_unresolved",
    "lifecycle_state_changed",
    "trading_state_changed",
    "forward_eps_revision_changed",
    "valuation_input_changed",
    "profile_revision_changed",
    "anchor_revision_changed",
    "context_provenance_missing",
    "incomparable_contract",
    "snapshot_integrity_error",
    "lineage_unresolved",
    "data_partial",
    "data_unknown",
    "data_blocked",
    "current_context_unavailable",
)

BASELINE_SELECTION_REASON_CODES = (
    "baseline_selection_snapshot_missing",
    "baseline_selection_snapshot_symbol_mismatch",
    "baseline_selection_snapshot_integrity_error",
    "baseline_selection_snapshot_created_after_cutoff",
    "baseline_selection_snapshot_knowledge_cutoff_after_requested_cutoff",
    "baseline_selection_snapshot_contract_unsupported",
)

_DAILY_REASON_ORDER = {code: index for index, code in enumerate(DAILY_REASON_CODES)}
_BASELINE_REASON_ORDER = {
    code: index for index, code in enumerate(BASELINE_SELECTION_REASON_CODES)
}
_STATUS_SEVERITY = {
    "available": 0,
    "insufficient_data": 1,
    "partial": 2,
    "unknown": 3,
    "blocked": 4,
}
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CURSOR_PREFIX = b"daily-research-review-cursor-v1:"
_TAIPEI = ZoneInfo("Asia/Taipei")


class DailyResearchCursorError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_utc_timestamp(value: str | datetime, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        except ValueError as exc:
            raise ValueError(f"{field}_invalid") from exc
    else:
        raise ValueError(f"{field}_required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_timezone_required")
    normalized = parsed.astimezone(timezone.utc)
    if normalized.microsecond:
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_market_date(value: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value.strip()):
        raise ValueError("market_date_invalid")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("market_date_invalid") from exc


def validate_daily_d_k(
    market_date: str,
    knowledge_cutoff_at: str,
    request_received_at: str | datetime,
) -> tuple[str, str, str]:
    target = validate_market_date(market_date)
    cutoff = canonical_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff")
    received = canonical_utc_timestamp(request_received_at, "request_received_at")
    cutoff_dt = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    received_dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
    if cutoff_dt > received_dt:
        raise ValueError("knowledge_cutoff_after_request")
    local_cutoff_date = cutoff_dt.astimezone(_TAIPEI).date().isoformat()
    if target > local_cutoff_date:
        raise ValueError("market_date_after_knowledge_cutoff")
    return target, cutoff, received


def normalize_daily_reasons(codes: Iterable[str]) -> list[str]:
    unique = set(codes)
    unsupported = unique.difference(_DAILY_REASON_ORDER)
    if unsupported:
        raise ValueError("daily_research_reason_not_registered")
    return sorted(unique, key=_DAILY_REASON_ORDER.__getitem__)


def normalize_baseline_selection_reasons(codes: Iterable[str]) -> list[str]:
    unique = set(codes)
    unsupported = unique.difference(_BASELINE_REASON_ORDER)
    if unsupported:
        raise ValueError("baseline_selection_reason_not_registered")
    return sorted(unique, key=_BASELINE_REASON_ORDER.__getitem__)


def reduce_page_status(statuses: Iterable[str]) -> str:
    values = list(statuses)
    if not values:
        return "available"
    if any(value not in _STATUS_SEVERITY for value in values):
        raise ValueError("daily_research_status_invalid")
    if all(value == values[0] for value in values):
        return values[0]
    return "partial"


def reduce_preflight_status(page_status: str, market_context_status: str) -> str:
    if page_status not in _STATUS_SEVERITY or market_context_status not in _STATUS_SEVERITY:
        raise ValueError("daily_research_status_invalid")
    return max((page_status, market_context_status), key=_STATUS_SEVERITY.__getitem__)


def derive_review_flags(
    *,
    review_state: str,
    comparison_status: str,
    comparison_has_deltas: bool | None,
    freshness_status: str,
    item_status: str,
    reason_codes: Iterable[str],
) -> dict[str, bool]:
    reasons = normalize_daily_reasons(reason_codes)
    blocked = (
        item_status == "blocked"
        or review_state in {"blocked", "snapshot_integrity_error", "incomparable_contract"}
        or any(code in reasons for code in (
            "identity_unresolved", "snapshot_integrity_error", "lineage_unresolved",
            "incomparable_contract", "data_blocked",
        ))
    )
    limited = not blocked and (
        review_state in {"no_snapshot", "baseline_not_set", "unknown"}
        or freshness_status == "stale"
        or item_status in {"partial", "insufficient_data", "unknown"}
        or any(code in reasons for code in (
            "data_partial", "data_unknown", "current_context_unavailable",
            "context_provenance_missing",
        ))
    )
    needs_review = bool(
        blocked
        or limited
        or review_state in {"no_snapshot", "baseline_not_set"}
        or freshness_status == "stale"
        or (comparison_status == "comparable" and comparison_has_deltas is True)
        or reasons
    )
    return {
        "review_needed": needs_review,
        "review_blocked": blocked,
        "review_limited": limited,
    }


def baseline_selection_eligibility(
    *,
    item_symbol: str,
    snapshot: Mapping[str, Any] | None,
    knowledge_cutoff_at: str,
    integrity_error: bool = False,
    contract_supported: bool = True,
) -> dict[str, Any]:
    reasons: list[str] = []
    if snapshot is None:
        reasons.append("baseline_selection_snapshot_missing")
    else:
        if str(snapshot.get("symbol") or "") != item_symbol:
            reasons.append("baseline_selection_snapshot_symbol_mismatch")
        if integrity_error:
            reasons.append("baseline_selection_snapshot_integrity_error")
        if str(snapshot.get("created_at") or "") > knowledge_cutoff_at:
            reasons.append("baseline_selection_snapshot_created_after_cutoff")
        if str(snapshot.get("knowledge_cutoff_at") or "") > knowledge_cutoff_at:
            reasons.append(
                "baseline_selection_snapshot_knowledge_cutoff_after_requested_cutoff"
            )
        if not contract_supported:
            reasons.append("baseline_selection_snapshot_contract_unsupported")
    normalized = normalize_baseline_selection_reasons(reasons)
    return {
        "baseline_selection_policy_version": DAILY_BASELINE_SELECTION_POLICY_VERSION,
        "baseline_selection_reason_registry_version": (
            DAILY_BASELINE_SELECTION_REASON_REGISTRY_VERSION
        ),
        "baseline_selection_eligible": not normalized,
        "baseline_selection_blocked": bool(normalized),
        "baseline_selection_reason_codes": normalized,
    }


@dataclass(frozen=True)
class DailyResearchCursor:
    market_date: str
    knowledge_cutoff_at: str
    limit: int
    active_population_checksum: str
    last_symbol: str
    last_watchlist_item_id: str

    def payload(self) -> dict[str, Any]:
        return {
            "cursor_version": DAILY_RESEARCH_CURSOR_VERSION,
            "contract_version": DAILY_RESEARCH_REVIEW_CONTEXT_VERSION,
            "policy_version": DAILY_RESEARCH_REVIEW_CONTEXT_POLICY_VERSION,
            "workflow_time_policy_version": DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
            "snapshot_selection_policy_version": DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
            "market_date": self.market_date,
            "knowledge_cutoff_at": self.knowledge_cutoff_at,
            "population": "active_research_queue",
            "population_evaluated_at": "workflow_evaluated_at",
            "active_population_checksum": self.active_population_checksum,
            "order_version": DAILY_RESEARCH_ORDER_VERSION,
            "limit": self.limit,
            "last_symbol": self.last_symbol,
            "last_watchlist_item_id": self.last_watchlist_item_id,
        }

    def encode(self) -> str:
        body = canonical_json(self.payload()).encode("utf-8")
        envelope = canonical_json({
            "payload": self.payload(),
            "checksum": hashlib.sha256(_CURSOR_PREFIX + body).hexdigest(),
        }).encode("utf-8")
        return base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")


def decode_daily_cursor(
    token: str,
    *,
    market_date: str,
    knowledge_cutoff_at: str,
    limit: int,
) -> DailyResearchCursor:
    if not isinstance(token, str) or not token or not _TOKEN_RE.fullmatch(token):
        raise DailyResearchCursorError("daily_cursor_malformed")
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        envelope = json.loads(raw.decode("utf-8"))
        payload = envelope["payload"]
        checksum = envelope["checksum"]
        expected = hashlib.sha256(
            _CURSOR_PREFIX + canonical_json(payload).encode("utf-8")
        ).hexdigest()
        if checksum != expected:
            raise DailyResearchCursorError("daily_cursor_checksum_invalid")
        canonical = base64.urlsafe_b64encode(
            canonical_json(envelope).encode("utf-8")
        ).decode("ascii").rstrip("=")
        if canonical != token:
            raise DailyResearchCursorError("daily_cursor_noncanonical")
    except DailyResearchCursorError:
        raise
    except Exception as exc:
        raise DailyResearchCursorError("daily_cursor_malformed") from exc
    expected_versions = {
        "cursor_version": DAILY_RESEARCH_CURSOR_VERSION,
        "contract_version": DAILY_RESEARCH_REVIEW_CONTEXT_VERSION,
        "policy_version": DAILY_RESEARCH_REVIEW_CONTEXT_POLICY_VERSION,
        "workflow_time_policy_version": DAILY_RESEARCH_WORKFLOW_TIME_VERSION,
        "snapshot_selection_policy_version": DAILY_RESEARCH_SNAPSHOT_SELECTION_VERSION,
        "population": "active_research_queue",
        "population_evaluated_at": "workflow_evaluated_at",
        "order_version": DAILY_RESEARCH_ORDER_VERSION,
    }
    if any(payload.get(key) != value for key, value in expected_versions.items()):
        raise DailyResearchCursorError("daily_cursor_context_mismatch")
    if (
        payload.get("market_date") != market_date
        or payload.get("knowledge_cutoff_at") != knowledge_cutoff_at
        or payload.get("limit") != limit
    ):
        raise DailyResearchCursorError("daily_cursor_context_mismatch")
    required = (
        "active_population_checksum", "last_symbol", "last_watchlist_item_id"
    )
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
        raise DailyResearchCursorError("daily_cursor_malformed")
    return DailyResearchCursor(
        market_date=market_date,
        knowledge_cutoff_at=knowledge_cutoff_at,
        limit=limit,
        active_population_checksum=payload["active_population_checksum"],
        last_symbol=payload["last_symbol"],
        last_watchlist_item_id=payload["last_watchlist_item_id"],
    )


def active_population_checksum(items: Iterable[Mapping[str, Any]]) -> str:
    keys = [
        [str(item["symbol"]), str(item["watchlist_item_id"])]
        for item in sorted(
            items,
            key=lambda value: (
                str(value["symbol"]), str(value["watchlist_item_id"])
            ),
        )
    ]
    return hashlib.sha256(canonical_json(keys).encode("utf-8")).hexdigest()


__all__ = [name for name in globals() if name.startswith("DAILY_")] + [
    "BASELINE_SELECTION_REASON_CODES",
    "DailyResearchCursor",
    "DailyResearchCursorError",
    "active_population_checksum",
    "baseline_selection_eligibility",
    "canonical_json",
    "canonical_utc_timestamp",
    "decode_daily_cursor",
    "derive_review_flags",
    "normalize_baseline_selection_reasons",
    "normalize_daily_reasons",
    "reduce_page_status",
    "reduce_preflight_status",
    "validate_daily_d_k",
    "validate_market_date",
]
