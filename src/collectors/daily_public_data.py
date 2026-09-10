"""Pure parsers for candidate daily FinMind research data.

These parsers deliberately do not perform network, database, or approval work.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import parse_aware_timestamp


DATASETS = {
    "TaiwanStockPrice",
    "TaiwanStockPER",
    "TaiwanStockFinancialStatements",
}


class DailyPublicDataError(ValueError):
    pass


def _number(value: Any, field: str, *, allow_none: bool = True) -> float | None:
    if isinstance(value, bool):
        raise DailyPublicDataError(f"invalid_{field}")
    if value is None or value == "":
        if allow_none:
            return None
        raise DailyPublicDataError(f"missing_{field}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DailyPublicDataError(f"invalid_{field}") from exc
    if not math.isfinite(result):
        raise DailyPublicDataError(f"non_finite_{field}")
    return result


def _iso_date(value: Any, observed_date: date) -> str:
    text = str(value).strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise DailyPublicDataError("invalid_date")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise DailyPublicDataError("invalid_date") from exc
    if parsed > observed_date:
        raise DailyPublicDataError("future_date")
    return parsed.isoformat()


def _observed_date(observed_at: str) -> date:
    try:
        return parse_aware_timestamp(observed_at, "observed_at").astimezone(ZoneInfo("Asia/Taipei")).date()
    except ValueError as exc:
        raise DailyPublicDataError("invalid_observed_at") from exc


def _canonical_code(symbol: str) -> str:
    return parse_canonical_symbol(symbol)[1]


def _base(dataset: str, observed_at: str) -> dict[str, Any]:
    return {
        "source": "FinMind",
        "official_exchange_source": False,
        "observed_at": observed_at,
        "dataset": dataset,
    }


def _validate_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != 200:
        raise DailyPublicDataError("payload_status_must_be_200")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise DailyPublicDataError("payload_data_must_be_list")
    if any(not isinstance(row, dict) for row in rows):
        raise DailyPublicDataError("payload_row_must_be_object")
    return rows


def _check_code(row: dict[str, Any], code: str) -> None:
    if str(row.get("stock_id", "")).strip() != code:
        raise DailyPublicDataError("stock_id_mismatch")


def _parse_price(rows: list[dict[str, Any]], code: str, dataset: str, observed_at: str) -> dict[str, Any]:
    observed_date = _observed_date(observed_at)
    parsed: list[dict[str, Any]] = []
    seen: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        _check_code(row, code)
        day = _iso_date(row.get("date"), observed_date)
        values = tuple(_number(row.get(key), key, allow_none=False) for key in (
            "open", "max", "min", "close", "Trading_Volume", "Trading_money", "spread"
        ))
        opening, high, low, closing, volume, value, change = values
        if volume < 0:
            raise DailyPublicDataError("negative_volume")
        if volume != int(volume) or value < 0 or low <= 0:
            raise DailyPublicDataError("invalid_price_or_quantity")
        if not (low <= opening <= high and low <= closing <= high):
            raise DailyPublicDataError("invalid_ohlc")
        normalized = {
            "date": day, "open": opening, "high": high, "low": low,
            "close": closing, "volume": volume, "value": value, "change": change,
            "zero_volume": volume == 0,
        }
        fingerprint = tuple(normalized[key] for key in ("open", "high", "low", "close", "volume", "value", "change"))
        if day in seen and seen[day] != fingerprint:
            raise DailyPublicDataError("duplicate_date_conflict")
        seen[day] = fingerprint
        if not any(item["date"] == day for item in parsed):
            parsed.append(normalized)
    parsed.sort(key=lambda item: item["date"])
    result = _base(dataset, observed_at)
    result.update({
        "status": "available" if parsed else "insufficient_data",
        "quality_status": "quality_warning",
        "quality_warning": "FinMind prices are not official, corporate-action-adjusted, or trading-calendar-audited",
        "symbol": code,
        "rows": parsed,
    })
    return result


def _parse_per(rows: list[dict[str, Any]], code: str, dataset: str, observed_at: str) -> dict[str, Any]:
    observed_date = _observed_date(observed_at)
    parsed = []
    seen: set[str] = set()
    for row in rows:
        _check_code(row, code)
        day = _iso_date(row.get("date"), observed_date)
        if day in seen:
            raise DailyPublicDataError("duplicate_date")
        seen.add(day)
        pe = _number(row.get("PER"), "PER")
        pb = _number(row.get("PBR"), "PBR")
        dividend = _number(row.get("dividend_yield"), "dividend_yield")
        if dividend is not None and dividend < 0:
            raise DailyPublicDataError("negative_dividend_yield")
        parsed.append({
            "date": day,
            "pe": pe if pe is not None and pe > 0 else None,
            "pb": pb if pb is not None and pb > 0 else None,
            "yield_ratio": dividend / 100 if dividend is not None else None,
        })
    parsed.sort(key=lambda item: item["date"])
    result = _base(dataset, observed_at)
    result.update({"status": "available" if parsed else "insufficient_data", "symbol": code, "rows": parsed})
    return result


def _parse_eps(rows: list[dict[str, Any]], code: str, dataset: str, observed_at: str) -> dict[str, Any]:
    observed_date = _observed_date(observed_at)
    parsed = []
    seen: set[str] = set()
    for row in rows:
        _check_code(row, code)
        if row.get("type") != "EPS":
            continue
        period_end = _iso_date(row.get("date"), observed_date)
        month = int(period_end[5:7])
        if period_end[5:] not in ("03-31", "06-30", "09-30", "12-31"):
            raise DailyPublicDataError("invalid_quarter_end")
        if period_end in seen:
            raise DailyPublicDataError("duplicate_period")
        seen.add(period_end)
        parsed.append({
            "period_end": period_end,
            "quarter": month // 3,
            "quarterly_eps": _number(row.get("value"), "EPS", allow_none=False),
            "available_at": observed_at,
        })
    parsed.sort(key=lambda item: item["period_end"])
    result = _base(dataset, observed_at)
    result.update({
        "status": "insufficient_data",
        "value": None,
        "reason": "share_basis_not_verified",
        "symbol": code,
        "rows": parsed,
    })
    return result


def parse_daily_public_dataset(dataset: str, payload: Any, symbol: str, observed_at: str) -> dict[str, Any]:
    """Parse one FinMind dataset without network, persistence, or model activation."""
    if dataset not in DATASETS:
        raise DailyPublicDataError("unsupported_dataset")
    rows = _validate_payload(payload)
    code = _canonical_code(symbol)
    if dataset == "TaiwanStockPrice":
        return _parse_price(rows, code, dataset, observed_at)
    if dataset == "TaiwanStockPER":
        return _parse_per(rows, code, dataset, observed_at)
    return _parse_eps(rows, code, dataset, observed_at)
