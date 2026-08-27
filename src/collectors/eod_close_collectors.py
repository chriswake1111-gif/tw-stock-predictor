"""Pure parsers for the two approved official EOD close resources.

The collector boundary accepts an injected payload/transport.  Nothing in
this module calls a provider at import time, and the parsers never write the
legacy ``daily_ohlcv`` table.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from src.domain.data_foundation import canonical_json, schema_fingerprint, sha256_text
from src.domain.eod_close import normalize_decimal_text, parse_twse_roc_date


TWSE_EOD_STOCK_DAY_ALL_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
)
TPEX_EOD_DAILY_CLOSE_QUOTES_URL = (
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
)

TWSE_EOD_RESOURCE_ID = "twse.eod.stock_day_all"
TPEX_EOD_RESOURCE_ID = "tpex.eod.daily_close_quotes"
TWSE_EOD_SOURCE_SCOPE = "twse_whole_market_daily_close"
TPEX_EOD_SOURCE_SCOPE = "tpex_mainboard_daily_close_quotes_without_fixed_price"

TWSE_EOD_FIELDS = (
    "Date", "Code", "Name", "TradeVolume", "TradeValue", "OpeningPrice",
    "HighestPrice", "LowestPrice", "ClosingPrice", "Change", "Transaction",
)
TPEX_EOD_FIELDS = (
    "Date", "SecuritiesCompanyCode", "CompanyName", "Close", "Change", "Open",
    "High", "Low", "Average", "TradingShares", "TransactionAmount",
    "TransactionNumber", "LatestBidPrice", "LatesAskPrice", "Capitals",
    "NextReferencePrice", "NextLimitUp", "NextLimitDown",
)


class EodSourceContractError(ValueError):
    """A source payload failed a code-owned envelope/schema/date gate."""

    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


@dataclass(frozen=True)
class ParsedEodRow:
    venue: str
    official_code: str
    display_name: str
    trade_date: str
    raw_close_text: str
    close_value: str | None
    close_parse_status: str
    raw_volume_text: str
    volume_value: str | None
    volume_parse_status: str
    raw_trade_indication_text: str
    trade_indication_value: str | None
    trade_indication_parse_status: str
    raw_row: dict[str, str]
    row_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "official_code": self.official_code,
            "display_name": self.display_name,
            "trade_date": self.trade_date,
            "raw_close_text": self.raw_close_text,
            "close_value": self.close_value,
            "close_parse_status": self.close_parse_status,
            "raw_volume_text": self.raw_volume_text,
            "volume_value": self.volume_value,
            "volume_parse_status": self.volume_parse_status,
            "raw_trade_indication_text": self.raw_trade_indication_text,
            "trade_indication_value": self.trade_indication_value,
            "trade_indication_parse_status": self.trade_indication_parse_status,
            "raw_row": dict(self.raw_row),
            "row_fingerprint": self.row_fingerprint,
        }


@dataclass(frozen=True)
class ParsedEodSnapshot:
    venue: str
    resource_id: str
    source_url: str
    source_scope: str
    rows: tuple[ParsedEodRow, ...]
    source_trade_date: str | None
    source_trade_date_status: str
    status: str
    coverage_state: str
    reason: str | None
    schema_fingerprint: str
    normalized_payload_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "resource_id": self.resource_id,
            "source_url": self.source_url,
            "source_scope": self.source_scope,
            "rows": [row.to_dict() for row in self.rows],
            "source_trade_date": self.source_trade_date,
            "source_trade_date_status": self.source_trade_date_status,
            "status": self.status,
            "coverage_state": self.coverage_state,
            "reason": self.reason,
            "schema_fingerprint": self.schema_fingerprint,
            "normalized_payload_sha256": self.normalized_payload_sha256,
        }


def _require_source_array(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        raise EodSourceContractError("schema_changed", "source envelope must be a top-level array")
    rows: list[dict[str, str]] = []
    for row in payload:
        if not isinstance(row, dict):
            raise EodSourceContractError("schema_changed", "source row must be an object")
        rows.append(row)
    return rows


def _require_exact_string_fields(row: dict[str, Any], fields: tuple[str, ...]) -> None:
    if set(row) != set(fields):
        raise EodSourceContractError("schema_changed", "source row fields changed")
    if any(type(row[field]) is not str for field in fields):
        raise EodSourceContractError("schema_changed", "source fields must remain strings")


def _parse_row(
    row: dict[str, str],
    *,
    venue: str,
    fields: tuple[str, ...],
    code_field: str,
    name_field: str,
    close_field: str,
    volume_field: str,
    trade_indication_field: str,
) -> ParsedEodRow:
    _require_exact_string_fields(row, fields)
    official_code = row[code_field].strip()
    display_name = row[name_field].strip()
    if not official_code or not display_name:
        raise EodSourceContractError("schema_changed", "source code and name are required")
    try:
        trade_date = parse_twse_roc_date(row["Date"])
    except ValueError as exc:
        raise EodSourceContractError(
            "source_date_in_future_or_invalid", "source Date is not a valid ROC date"
        ) from exc
    close_value, _, close_state = normalize_decimal_text(row[close_field])
    volume_value, _, volume_state = normalize_decimal_text(row[volume_field])
    indication_value, _, indication_state = normalize_decimal_text(
        row[trade_indication_field]
    )
    raw_copy = {field: row[field] for field in fields}
    return ParsedEodRow(
        venue=venue,
        official_code=official_code,
        display_name=display_name,
        trade_date=trade_date,
        raw_close_text=row[close_field],
        close_value=close_value,
        close_parse_status=close_state,
        raw_volume_text=row[volume_field],
        volume_value=volume_value,
        volume_parse_status=volume_state,
        raw_trade_indication_text=row[trade_indication_field],
        trade_indication_value=indication_value,
        trade_indication_parse_status=indication_state,
        raw_row=raw_copy,
        row_fingerprint=sha256_text(canonical_json(raw_copy)),
    )


def _parse_snapshot(
    payload: Any,
    *,
    venue: str,
    resource_id: str,
    source_url: str,
    source_scope: str,
    fields: tuple[str, ...],
    code_field: str,
    name_field: str,
    close_field: str,
    volume_field: str,
    trade_indication_field: str,
) -> ParsedEodSnapshot:
    raw_rows = _require_source_array(payload)
    expected_schema = schema_fingerprint(fields)
    if not raw_rows:
        normalized_hash = sha256_text(canonical_json([]))
        return ParsedEodSnapshot(
            venue=venue,
            resource_id=resource_id,
            source_url=source_url,
            source_scope=source_scope,
            rows=(),
            source_trade_date=None,
            source_trade_date_status="missing",
            status="unknown",
            coverage_state="unknown",
            reason="source_empty",
            schema_fingerprint=expected_schema,
            normalized_payload_sha256=normalized_hash,
        )
    rows = tuple(
        _parse_row(
            row,
            venue=venue,
            fields=fields,
            code_field=code_field,
            name_field=name_field,
            close_field=close_field,
            volume_field=volume_field,
            trade_indication_field=trade_indication_field,
        )
        for row in raw_rows
    )
    dates = {row.trade_date for row in rows}
    if len(dates) != 1:
        raise EodSourceContractError(
            "schema_changed", "whole-market source rows must share one trade date"
        )
    normalized_hash = sha256_text(
        canonical_json([row.to_dict() for row in rows])
    )
    return ParsedEodSnapshot(
        venue=venue,
        resource_id=resource_id,
        source_url=source_url,
        source_scope=source_scope,
        rows=rows,
        source_trade_date=next(iter(dates)),
        source_trade_date_status="valid",
        status="available",
        coverage_state="complete",
        reason=None,
        schema_fingerprint=expected_schema,
        normalized_payload_sha256=normalized_hash,
    )


def parse_twse_stock_day_all(payload: Any) -> list[dict[str, Any]]:
    """Parse TWSE ``STOCK_DAY_ALL`` and return normalized evidence rows."""

    snapshot = parse_twse_snapshot(payload)
    return [row.to_dict() for row in snapshot.rows]


def parse_tpex_daily_close_quotes(payload: Any) -> list[dict[str, Any]]:
    """Parse TPEx daily close quotes without fixed-price substitution."""

    snapshot = parse_tpex_snapshot(payload)
    return [row.to_dict() for row in snapshot.rows]


def parse_twse_snapshot(payload: Any) -> ParsedEodSnapshot:
    return _parse_snapshot(
        payload,
        venue="TWSE",
        resource_id=TWSE_EOD_RESOURCE_ID,
        source_url=TWSE_EOD_STOCK_DAY_ALL_URL,
        source_scope=TWSE_EOD_SOURCE_SCOPE,
        fields=TWSE_EOD_FIELDS,
        code_field="Code",
        name_field="Name",
        close_field="ClosingPrice",
        volume_field="TradeVolume",
        trade_indication_field="TradeValue",
    )


def parse_tpex_snapshot(payload: Any) -> ParsedEodSnapshot:
    return _parse_snapshot(
        payload,
        venue="TPEX",
        resource_id=TPEX_EOD_RESOURCE_ID,
        source_url=TPEX_EOD_DAILY_CLOSE_QUOTES_URL,
        source_scope=TPEX_EOD_SOURCE_SCOPE,
        fields=TPEX_EOD_FIELDS,
        code_field="SecuritiesCompanyCode",
        name_field="CompanyName",
        close_field="Close",
        volume_field="TradingShares",
        trade_indication_field="TransactionAmount",
    )


def parse_approved_partial_fixture(
    payload: dict[str, Any], *, venue: str
) -> ParsedEodSnapshot:
    """Parse only a separately constructed positive coverage-proof fixture.

    The live TWSE/TPEx parsers intentionally reject wrapper metadata because
    their approved v1 envelope is a top-level array.  Tests may use this
    helper to exercise the domain partial branch without inventing a live
    provider marker.
    """

    if payload.get("coverage_state") != "partial":
        raise EodSourceContractError("schema_changed", "partial fixture proof is required")
    if not payload.get("coverage_proof_type") or not payload.get("coverage_proof_reference"):
        raise EodSourceContractError("schema_changed", "partial fixture proof is required")
    rows = payload.get("rows", [])
    snapshot = parse_twse_snapshot(rows) if venue.upper() == "TWSE" else parse_tpex_snapshot(rows)
    return ParsedEodSnapshot(
        **{
            **snapshot.__dict__,
            "status": "partial",
            "coverage_state": "partial",
            "reason": "partial_venue_payload",
        }
    )


class OfficialEodCollector:
    """Allowlisted GET transport for the two approved whole-market resources."""

    def __init__(
        self,
        fetcher: Callable[..., Any] | None = None,
        *,
        timeout: int = 20,
    ) -> None:
        self.fetcher = fetcher or requests.get
        self.timeout = timeout

    def fetch(self, venue: str, *, received_at: str | None = None) -> dict[str, Any]:
        normalized = str(venue).strip().upper()
        if normalized == "TWSE":
            url = TWSE_EOD_STOCK_DAY_ALL_URL
            parser = parse_twse_snapshot
        elif normalized == "TPEX":
            url = TPEX_EOD_DAILY_CLOSE_QUOTES_URL
            parser = parse_tpex_snapshot
        else:
            raise ValueError("venue must be TWSE or TPEX")
        response = self.fetcher(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        raw_body = getattr(response, "content", None)
        raw_payload_sha256 = (
            hashlib.sha256(raw_body).hexdigest()
            if isinstance(raw_body, bytes)
            else sha256_text(canonical_json(payload))
        )
        parsed = parser(payload)
        observed = received_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "payload": payload,
            "parsed": parsed,
            "source_url": url,
            "received_at": observed,
            "raw_payload_sha256": raw_payload_sha256,
        }


__all__ = [
    "EodSourceContractError",
    "OfficialEodCollector",
    "ParsedEodRow",
    "ParsedEodSnapshot",
    "TPEX_EOD_DAILY_CLOSE_QUOTES_URL",
    "TPEX_EOD_FIELDS",
    "TPEX_EOD_RESOURCE_ID",
    "TPEX_EOD_SOURCE_SCOPE",
    "TWSE_EOD_FIELDS",
    "TWSE_EOD_RESOURCE_ID",
    "TWSE_EOD_SOURCE_SCOPE",
    "TWSE_EOD_STOCK_DAY_ALL_URL",
    "parse_approved_partial_fixture",
    "parse_tpex_daily_close_quotes",
    "parse_tpex_snapshot",
    "parse_twse_snapshot",
    "parse_twse_stock_day_all",
]
