"""Phase 14 official end-of-day close context contracts.

This module is deliberately independent from the legacy adjusted-price and
analysis paths.  It owns the small, fail-closed policy surface shared by the
EOD repository, read services, API and frontend DTO.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable

from src.domain.universe import validate_knowledge_cutoff_at


class EodCloseStatus(str, Enum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    NEEDS_HUMAN_INPUT = "needs_human_input"
    NOT_APPLICABLE = "not_applicable"


class EodFreshnessState(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class EodProductScope(str, Enum):
    SUPPORTED_STOCK = "supported_stock"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_HUMAN_INPUT = "needs_human_input"


class EodClassificationState(str, Enum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    SCHEMA_CHANGED = "schema_changed"
    AMBIGUOUS = "ambiguous"
    REVOKED = "revoked"


EOD_STATUS_POLICY_VERSION = "eod_close_status_matrix_v1"
EOD_DTO_CONTRACT_VERSION = "eod_close_context_v1"
EOD_PRICE_SEMANTICS_VERSION = "official_reported_close_v1"
EOD_PRODUCT_POLICY_VERSION = "eod_product_scope_v1"
EOD_CURRENCY_POLICY_VERSION = "eod_currency_unit_scope_v1"
EOD_ELIGIBILITY_POLICY_VERSION = "eod_public_close_eligibility_v1"
EOD_CURRENTNESS_POLICY_VERSION = "eod_currentness_policy_v1"
EOD_ASOF_SELECTION_SCOPE = "latest_observed_trade_date_as_of_cutoff"

# These are exact first-party classification values only.  The classifier must
# not infer product scope from a name, code shape, or a broad text fragment.
_RECOGNIZED_NON_STOCK_TYPES = frozenset({
    "etf",
    "etn",
    "特別股",
    "優先股",
    "權證",
    "權利證書",
    "認股權憑證",
    "認購權證",
    "認售權證",
    "存託憑證",
    "臺灣存託憑證",
    "tdr",
    "depositary receipt",
    "受益證券",
    "指數股票型基金",
})
_PREFERRED_CFI_VALUES = frozenset({"EPNRAR"})
_PREFERRED_REMARK_VALUES = frozenset({
    "特別股",
    "優先股",
    "Preferred Stock",
    "Preferred Stocks",
    "Preferred Share",
    "Preferred Shares",
})


EOD_REASON_CODES = (
    "source_date_older_than_evaluated_date",
    "no_same_day_snapshot",
    "current_session_unproven",
    "source_date_in_future_or_invalid",
    "provider_error",
    "schema_changed",
    "latest_revision_blocked",
    "source_empty",
    "partial_venue_payload",
    "row_missing",
    "close_missing",
    "close_missing_or_volume_unusable",
    "volume_unusable",
    "official_zero_volume_not_public_eligible",
    "instrument_suspended",
    "unsupported_security_type",
    "foreign_currency_not_supported",
    "currency_unit_unproven",
    "classification_evidence_missing",
    "unrecognized_product_class",
    "identity_epoch_ambiguous",
    "identity_mapping_unverified",
    "cross_venue_mismatch",
    "no_cutoff_visible_observation",
    "correction_without_accepted_replacement",
    "source_revoke_without_replacement",
    "publication_instant_unproven",
    "current_latest_blocked",
    "source_usage_not_approved",
)

_REASON_SET = frozenset(EOD_REASON_CODES)
_BLOCKED_REASONS = frozenset(
    {
        "source_date_in_future_or_invalid",
        "provider_error",
        "schema_changed",
        "latest_revision_blocked",
        "correction_without_accepted_replacement",
        "source_revoke_without_replacement",
        "current_latest_blocked",
        "source_usage_not_approved",
    }
)
_HUMAN_REASONS = frozenset(
    {
        "foreign_currency_not_supported",
        "currency_unit_unproven",
        "classification_evidence_missing",
        "unrecognized_product_class",
        "identity_epoch_ambiguous",
        "identity_mapping_unverified",
        "cross_venue_mismatch",
    }
)
_PARTIAL_REASONS = frozenset({"partial_venue_payload"})
_UNKNOWN_REASONS = frozenset(
    {
        "source_date_older_than_evaluated_date",
        "no_same_day_snapshot",
        "current_session_unproven",
        "source_empty",
        "no_cutoff_visible_observation",
    }
)
_INSUFFICIENT_REASONS = frozenset(
    {
        "row_missing",
        "close_missing",
        "close_missing_or_volume_unusable",
        "volume_unusable",
        "official_zero_volume_not_public_eligible",
    }
)
_NOT_APPLICABLE_REASONS = frozenset(
    {"instrument_suspended", "unsupported_security_type"}
)

_REASON_ORDER = {reason: index for index, reason in enumerate(EOD_REASON_CODES)}
_ROC_DATE_RE = re.compile(r"^(?P<year>[0-9]{3})(?:/(?P<month>[0-9]{2})/(?P<day>[0-9]{2})|(?P<compact>[0-9]{4}))$")
_MISSING_MARKERS = frozenset({"", "-", "--", "---", "N/A", "NA", "NONE", "NAN"})


def _dedupe_reasons(reasons: Iterable[str]) -> list[str]:
    values = {str(reason).strip() for reason in reasons if str(reason).strip()}
    # An unknown reason is itself fail-closed.  Retain it for diagnosis while
    # the status matrix maps it to blocked rather than accidentally treating it
    # as an informational annotation.
    return sorted(values, key=lambda value: (_REASON_ORDER.get(value, len(EOD_REASON_CODES)), value))


def _status_for_reason(reason: str) -> EodCloseStatus:
    if reason in _BLOCKED_REASONS or reason not in _REASON_SET:
        return EodCloseStatus.BLOCKED
    if reason in _HUMAN_REASONS:
        return EodCloseStatus.NEEDS_HUMAN_INPUT
    if reason in _PARTIAL_REASONS:
        return EodCloseStatus.PARTIAL
    if reason in _UNKNOWN_REASONS:
        return EodCloseStatus.UNKNOWN
    if reason in _INSUFFICIENT_REASONS:
        return EodCloseStatus.INSUFFICIENT_DATA
    if reason in _NOT_APPLICABLE_REASONS:
        return EodCloseStatus.NOT_APPLICABLE
    return EodCloseStatus.BLOCKED


def eod_close_status_matrix_v1(
    reasons: Iterable[str] = (),
    *,
    freshness_state: str = EodFreshnessState.UNKNOWN.value,
    current_complete: bool = False,
    evidence_complete: bool | None = None,
) -> dict[str, Any]:
    """Compose the exact Phase 14 status precedence.

    ``evidence_complete`` is separate from ``current_complete`` because an
    as-of lookup can have a complete historical row while the DTO contract
    intentionally fixes ``current_complete`` to ``false``.
    """

    normalized_reasons = _dedupe_reasons(reasons)
    candidates = [_status_for_reason(reason) for reason in normalized_reasons]
    if evidence_complete is None:
        evidence_complete = bool(current_complete)
    if candidates:
        status = max(
            candidates,
            key=lambda candidate: (
                {
                    EodCloseStatus.BLOCKED: 6,
                    EodCloseStatus.NEEDS_HUMAN_INPUT: 5,
                    EodCloseStatus.PARTIAL: 4,
                    EodCloseStatus.UNKNOWN: 3,
                    EodCloseStatus.INSUFFICIENT_DATA: 2,
                    EodCloseStatus.NOT_APPLICABLE: 1,
                    EodCloseStatus.AVAILABLE: 0,
                }[candidate],
                candidate.value,
            ),
        )
    elif evidence_complete:
        status = EodCloseStatus.AVAILABLE
    else:
        status = EodCloseStatus.UNKNOWN

    try:
        freshness = EodFreshnessState(str(freshness_state)).value
    except ValueError:
        freshness = EodFreshnessState.UNKNOWN.value
    if status is EodCloseStatus.BLOCKED:
        freshness = EodFreshnessState.BLOCKED.value
    return {
        "status": status.value,
        "status_policy_version": EOD_STATUS_POLICY_VERSION,
        "reason_codes": normalized_reasons,
        "freshness_state": freshness,
        "current_complete": bool(current_complete),
    }


def normalize_decimal_text(value: Any) -> tuple[str | None, Decimal | None, str]:
    """Parse a provider numeric string without converting through float.

    The third return value is one of ``valid``, ``missing`` or ``invalid`` and
    lets the service distinguish a missing marker from malformed numeric text.
    """

    if not isinstance(value, str):
        return None, None, "invalid"
    text = value.strip()
    if text.upper() in _MISSING_MARKERS:
        return None, None, "missing"
    try:
        parsed = Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None, None, "invalid"
    if not parsed.is_finite():
        return None, None, "invalid"
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".") or "0"
    return normalized, parsed, "valid"


def positive_decimal_text(value: Any) -> str | None:
    normalized, parsed, state = normalize_decimal_text(value)
    if state != "valid" or parsed is None or parsed <= 0:
        return None
    return normalized


def parse_twse_roc_date(value: Any) -> str:
    """Convert the fixed ROC ``YYYMMDD``/``YYY/MM/DD`` source date to ISO."""

    if not isinstance(value, str):
        raise ValueError("source_date_invalid")
    match = _ROC_DATE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("source_date_invalid")
    year = int(match.group("year")) + 1911
    if match.group("compact"):
        month = int(match.group("compact")[:2])
        day = int(match.group("compact")[2:])
    else:
        month = int(match.group("month"))
        day = int(match.group("day"))
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise ValueError("source_date_invalid") from exc


def parse_iso_date(value: Any, field_name: str = "trade_date") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name}_invalid")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name}_invalid") from exc


def normalize_aware_timestamp(value: str, field_name: str) -> str:
    return validate_knowledge_cutoff_at(value) if field_name == "knowledge_cutoff_at" else _normalize_timestamp(value, field_name)


def _normalize_timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str) or "T" not in value and "t" not in value:
        raise ValueError(f"{field_name} must include a timestamp and timezone")
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def classify_product(
    *,
    official_code: str | None,
    requested_code: str,
    market: str | None,
    expected_market: str,
    security_type: str | None,
    listing_date: str | None = None,
    trade_date: str | None = None,
    currency: str | None = None,
    cfi: str | None = None,
    remarks: str | None = None,
) -> dict[str, Any]:
    """Apply the exact v1 product decision table without inference."""

    code = str(official_code or "").strip()
    requested = str(requested_code).strip()
    observed_market = str(market or "").strip()
    observed_type = str(security_type or "").strip()
    observed_cfi = str(cfi or "").strip()
    observed_remarks = str(remarks or "").strip()
    result: dict[str, Any] = {
        "product_scope": EodProductScope.NEEDS_HUMAN_INPUT.value,
        "decision": "needs_human_input",
        "reason_codes": [],
        "security_type": observed_type or None,
        "cfi": observed_cfi or None,
        "remarks": observed_remarks or None,
    }
    if not code or not observed_market or not observed_type:
        result["reason_codes"] = ["classification_evidence_missing"]
        return result
    if code != requested:
        result["reason_codes"] = ["identity_mapping_unverified"]
        return result
    if observed_market != expected_market:
        result["reason_codes"] = ["cross_venue_mismatch"]
        return result

    normalized_type = observed_type.casefold()
    normalized_cfi = observed_cfi.upper()
    normalized_remarks = observed_remarks.casefold()
    preferred_evidence = (
        normalized_cfi in {item.upper() for item in _PREFERRED_CFI_VALUES}
        or normalized_remarks in {item.casefold() for item in _PREFERRED_REMARK_VALUES}
    )
    if (
        normalized_type in {item.casefold() for item in _RECOGNIZED_NON_STOCK_TYPES}
        or preferred_evidence
    ):
        result.update(
            product_scope=EodProductScope.NOT_APPLICABLE.value,
            decision="not_applicable",
            reason_codes=["unsupported_security_type"],
        )
        return result
    if normalized_type != "股票":
        result["reason_codes"] = ["unrecognized_product_class"]
        return result
    if currency and currency.strip().upper() not in {"TWD", "新台幣", "台幣"}:
        result["reason_codes"] = ["foreign_currency_not_supported"]
        return result
    if listing_date and trade_date:
        try:
            if parse_iso_date(listing_date, "listing_date") > parse_iso_date(trade_date, "trade_date"):
                result["reason_codes"] = ["unrecognized_product_class"]
                return result
        except ValueError:
            result["reason_codes"] = ["classification_evidence_missing"]
            return result
    result.update(
        product_scope=EodProductScope.SUPPORTED_STOCK.value,
        decision="supported_stock",
    )
    return result


@dataclass(frozen=True)
class EodCloseContext:
    status: str
    reason_codes: tuple[str, ...]
    canonical_symbol: str | None
    instrument_id: str | None
    official_code: str | None
    venue: str | None
    security_type: str | None
    product_scope: str
    knowledge_cutoff_at: str | None
    evaluated_at: str
    selection_scope: str | None
    selected_trade_date: str | None
    close_value: str | None
    currency: str | None
    unit: str | None
    price_semantics: str | None
    freshness_state: str
    current_complete: bool
    provider: str | None
    resource_key: str | None
    source_url: str | None
    source_trade_date: str | None
    observed_at: str | None
    source_scope: str | None
    quality_flags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Return only the stable public DTO fields."""

        status = str(self.status)
        public_close = self.close_value if status == EodCloseStatus.AVAILABLE.value else None
        return {
            "contract_version": EOD_DTO_CONTRACT_VERSION,
            "status": status,
            "reason_codes": list(self.reason_codes),
            "canonical_symbol": self.canonical_symbol,
            "instrument_id": self.instrument_id,
            "official_code": self.official_code,
            "venue": self.venue,
            "security_type": self.security_type,
            "product_scope": self.product_scope,
            "knowledge_cutoff_at": self.knowledge_cutoff_at,
            "evaluated_at": self.evaluated_at,
            "selection_scope": self.selection_scope,
            "selected_trade_date": self.selected_trade_date,
            "close_value": public_close,
            "currency": self.currency if status == EodCloseStatus.AVAILABLE.value else None,
            "unit": self.unit if status == EodCloseStatus.AVAILABLE.value else None,
            "price_semantics": self.price_semantics if status == EodCloseStatus.AVAILABLE.value else None,
            "freshness_state": self.freshness_state,
            "current_complete": bool(self.current_complete),
            "provider": self.provider,
            "resource_key": self.resource_key,
            "source_url": self.source_url,
            "source_trade_date": self.source_trade_date,
            "observed_at": self.observed_at,
            "source_scope": self.source_scope,
            "quality_flags": list(self.quality_flags),
        }


def compose_context(
    *,
    reasons: Iterable[str],
    freshness_state: str,
    current_complete: bool,
    evidence_complete: bool,
    close_value: str | None,
    product_scope: str = EodProductScope.NEEDS_HUMAN_INPUT.value,
    **fields: Any,
) -> dict[str, Any]:
    matrix = eod_close_status_matrix_v1(
        reasons,
        freshness_state=freshness_state,
        current_complete=current_complete,
        evidence_complete=evidence_complete,
    )
    context = EodCloseContext(
        status=matrix["status"],
        reason_codes=tuple(matrix["reason_codes"]),
        product_scope=product_scope,
        close_value=close_value,
        freshness_state=matrix["freshness_state"],
        current_complete=matrix["current_complete"],
        **fields,
    )
    return context.to_dict()


__all__ = [
    "EOD_ASOF_SELECTION_SCOPE",
    "EOD_CURRENCY_POLICY_VERSION",
    "EOD_CURRENTNESS_POLICY_VERSION",
    "EOD_DTO_CONTRACT_VERSION",
    "EOD_ELIGIBILITY_POLICY_VERSION",
    "EOD_PRICE_SEMANTICS_VERSION",
    "EOD_PRODUCT_POLICY_VERSION",
    "EOD_REASON_CODES",
    "EOD_STATUS_POLICY_VERSION",
    "EodClassificationState",
    "EodCloseContext",
    "EodCloseStatus",
    "EodFreshnessState",
    "EodProductScope",
    "classify_product",
    "compose_context",
    "eod_close_status_matrix_v1",
    "normalize_decimal_text",
    "normalize_aware_timestamp",
    "parse_iso_date",
    "parse_twse_roc_date",
    "positive_decimal_text",
]
