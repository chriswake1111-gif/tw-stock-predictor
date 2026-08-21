"""Fixture-oriented Universe source contracts; no live provider is called in tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from src.domain.universe import payload_fingerprint, validate_official_code


APPROVED_RESOURCE_KEYS = frozenset({
    "twse.t187ap03_L", "twse.company.newlisting", "twse.company.suspendListingCsvAndHtml",
    "tpex.mopsfin_t187ap03_O", "tpex.company.deListed",
    "tpex.spendi.history", "tpex.spendi.today", "tpex.cmode", "tpex.company.current",
    "tpex_spendi_history", "tpex_spendi_today", "tpex_cmode",
})
# The seven-row Phase 13 migration still contains this logical identity for
# already-imported history.  It is intentionally not a new-ingestion contract.
LEGACY_RESOURCE_KEYS = frozenset({"tpex.spendi.cmode"})
EXCLUDED_RESOURCE_KEYS = frozenset({"tpex_mainboard_quotes", "price_context_out_of_scope"})
APPROVED_PROVIDER_HOSTS = frozenset({"twse.com.tw", "tpex.org.tw"})
PRICE_FIELD_NAMES = frozenset({
    "price", "close", "open", "high", "low", "volume", "turnover",
    "成交價", "收盤價", "開盤價", "最高價", "最低價", "成交量", "成交金額",
})

_RESOURCE_ALIASES = {
    "tpex_spendi_history": "tpex.spendi.history",
    "tpex_spendi_today": "tpex.spendi.today",
    "tpex_cmode": "tpex.cmode",
}
_RESOURCE_CONTRACTS = {
    # Each contract fixes the response envelope and the required official
    # field groups.  Additional fields are retained in the normalized row and
    # included in the observed schema evidence rather than silently ignored.
    "twse.t187ap03_L": {
        "kind": "master", "envelope": "data",
        "required": {"code": ("code", "official_code", "公司代號", "SecuritiesCompanyCode", "證券代號"),
                      "name": ("name", "公司名稱", "SecuritiesCompanyName")},
    },
    "twse.company.newlisting": {
        "kind": "listing", "envelope": "data",
        "required": {"code": ("code", "official_code", "公司代號", "SecuritiesCompanyCode", "證券代號"),
                      "effective_date": ("effective_date", "source_effective_date", "上市日期", "Date", "date")},
    },
    "twse.company.suspendListingCsvAndHtml": {
        "kind": "termination", "envelope": "data",
        "required": {"code": ("code", "official_code", "公司代號", "SecuritiesCompanyCode", "證券代號"),
                      "date": ("effective_date", "source_effective_date", "上市日期", "終止日期", "Date", "date"),
                      "reason": ("reason", "termination_reason", "下市原因", "終止上市原因")},
    },
    "tpex.mopsfin_t187ap03_O": {
        "kind": "master", "envelope": "data",
        "required": {"code": ("code", "official_code", "公司代號", "SecuritiesCompanyCode", "證券代號"),
                      "name": ("name", "公司名稱", "SecuritiesCompanyName")},
    },
    "tpex.company.deListed": {
        "kind": "termination", "envelope": "tables",
        "required": {"code": ("code", "official_code", "公司代號", "SecuritiesCompanyCode", "證券代號"),
                      "date": ("effective_date", "source_effective_date", "下市日期", "Date", "date", "year", "年度"),
                      "reason": ("reason", "termination_reason", "下市原因", "終止上市原因")},
    },
    "tpex.spendi.history": {
        "kind": "operational", "envelope": "data",
        "required": {"date": ("date", "Date", "交易日期"), "state": ("status", "state", "trading_state", "交易狀態", "處置狀態", "session_status")},
    },
    "tpex.spendi.today": {
        "kind": "operational", "envelope": "data",
        "required": {"date": ("date", "Date", "交易日期"), "state": ("status", "state", "trading_state", "交易狀態", "處置狀態", "session_status")},
    },
    "tpex.cmode": {
        "kind": "operational", "envelope": "data",
        "required": {"date": ("date", "Date", "交易日期"), "state": ("status", "state", "trading_state", "交易狀態", "處置狀態", "session_status")},
    },
    "tpex.company.current": {
        "kind": "corroborating", "envelope": "data",
        "required": {"code": ("code", "official_code", "公司代號", "SecuritiesCompanyCode", "證券代號"),
                      "name": ("name", "公司名稱", "SecuritiesCompanyName")},
    },
}
_CODE_FIELDS = ("code", "official_code", "公司代號", "SecuritiesCompanyCode", "證券代號")
_DATE_FIELDS = ("date", "Date", "effective_date", "source_effective_date", "上市日期", "交易日期", "year", "年度")
_STATE_FIELDS = ("status", "state", "trading_state", "交易狀態", "處置狀態", "session_status")
_REASON_FIELDS = ("reason", "termination_reason", "下市原因", "終止上市原因")


class UniverseSourceRejected(ValueError):
    pass


@dataclass(frozen=True)
class UniverseFetchResult:
    resource_key: str
    status: str
    rows: tuple[dict[str, Any], ...]
    first_observed_at: str | None
    received_at: str
    fetched_at: str
    payload_sha256: str | None
    schema_fingerprint: str | None
    reason: str | None = None
    raw_payload_sha256: str | None = None
    normalized_payload_sha256: str | None = None
    parser_version: str | None = None
    query_dimensions: dict[str, Any] | None = None
    source_record_reference: str | None = None


def assert_approved_resource(resource_key: str) -> str:
    key = str(resource_key).strip()
    if key in EXCLUDED_RESOURCE_KEYS or "quote" in key.lower() or "price" in key.lower():
        raise UniverseSourceRejected("quote_source_excluded")
    if key in LEGACY_RESOURCE_KEYS:
        raise UniverseSourceRejected("legacy_source_contract_not_for_ingestion")
    if key not in APPROVED_RESOURCE_KEYS:
        raise UniverseSourceRejected("universe_source_not_approved")
    return _RESOURCE_ALIASES.get(key, key)


def _extract_rows(resource_key: str, payload: Any) -> tuple[list[dict[str, Any]], str]:
    contract = _RESOURCE_CONTRACTS[resource_key]
    if not isinstance(payload, dict):
        raise UniverseSourceRejected("source_schema_changed")
    envelope = contract["envelope"]
    if envelope == "data" and isinstance(payload.get("data"), list):
        rows = payload["data"]
    elif envelope == "tables" and isinstance(payload.get("tables"), list):
        rows = []
        for table in payload["tables"]:
            if isinstance(table, dict) and isinstance(table.get("data"), list):
                rows.extend(table["data"])
            else:
                raise UniverseSourceRejected("source_schema_changed")
    else:
        raise UniverseSourceRejected("source_schema_changed")
    if not all(isinstance(row, dict) for row in rows):
        raise UniverseSourceRejected("source_schema_changed")
    return [dict(row) for row in rows], envelope


def _has_value(row: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return any(field in row and row[field] not in (None, "") for field in fields)


def _validate_rows(key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contract = _RESOURCE_CONTRACTS[key]
    kind = contract["kind"]
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if any(str(field).strip().lower() in PRICE_FIELD_NAMES for field in row):
            raise UniverseSourceRejected("price_field_excluded")
        value = dict(row)
        for fields in contract["required"].values():
            if not _has_value(row, fields):
                raise UniverseSourceRejected("source_schema_changed")
        if kind in {"master", "listing", "termination", "corroborating"}:
            code = next((row.get(field) for field in _CODE_FIELDS if row.get(field) not in (None, "")), None)
            value["official_code"] = validate_official_code(str(code))
        parsed.append(value)
    return parsed


def _schema_evidence(key: str, envelope: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    contract = _RESOURCE_CONTRACTS[key]
    observed_fields = sorted({str(field) for row in rows for field in row})
    return {
        "logical_resource_key": key,
        "schema_version": "phase13",
        "envelope": envelope,
        "required_field_groups": {
            name: list(fields) for name, fields in contract["required"].items()
        },
        "observed_row_fields": observed_fields,
    }


def _parse_with_evidence(resource_key: str, payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = assert_approved_resource(resource_key)
    rows, envelope = _extract_rows(key, payload)
    parsed = _validate_rows(key, rows)
    return parsed, _schema_evidence(key, envelope, rows)


def parse_universe_payload(resource_key: str, payload: Any) -> list[dict[str, Any]]:
    return _parse_with_evidence(resource_key, payload)[0]


class UniverseCollector:
    """A collector with injected transport; it cannot fetch excluded sources."""

    def __init__(self, fetcher: Callable[..., Any] | None = None):
        self.fetcher = fetcher

    def fetch_official(self, resource_key: str, *, url: str, **kwargs: Any) -> UniverseFetchResult:
        key = assert_approved_resource(resource_key)
        parsed_url = urlparse(str(url))
        hostname = (parsed_url.hostname or "").lower()
        if parsed_url.scheme.lower() != "https":
            raise UniverseSourceRejected("official_source_requires_https")
        if not any(hostname == host or hostname.endswith(f".{host}") for host in APPROVED_PROVIDER_HOSTS):
            raise UniverseSourceRejected("third_party_source_rejected")
        if self.fetcher is None:
            raise UniverseSourceRejected("live_provider_fetch_not_configured")
        try:
            response = self.fetcher(url, **kwargs)
        except Exception as exc:  # transport failure is a typed partial/provider state
            return UniverseFetchResult(key, "provider_error", (), None, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), None, None, f"transport_error:{type(exc).__name__}")
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            return UniverseFetchResult(key, "provider_error", (), None, "", "", None, None, f"http_{status_code}")
        try:
            raw = response.json() if hasattr(response, "json") else response
            rows, schema_evidence = _parse_with_evidence(key, raw)
            # The observation timestamp is assigned only after transport, JSON,
            # schema and parser acceptance; it is independent of coverage.
            first = kwargs.get("first_observed_at") or kwargs.get("received_at") or kwargs.get("fetched_at")
            raw_hash = payload_fingerprint(raw)
            normalized_hash = payload_fingerprint(rows)
            schema_hash = payload_fingerprint(schema_evidence)
            supplied_schema = kwargs.get("schema_fingerprint")
            if supplied_schema and str(supplied_schema).lower() != schema_hash:
                raise UniverseSourceRejected("schema_evidence_mismatch")
            query_dimensions = dict(kwargs.get("query_dimensions") or {})
            query_dimensions.setdefault("source_contract_key", key)
            query_dimensions["source_envelope"] = schema_evidence["envelope"]
            query_dimensions["required_field_groups"] = schema_evidence["required_field_groups"]
            query_dimensions["observed_row_fields"] = schema_evidence["observed_row_fields"]
            return UniverseFetchResult(
                key, "accepted", tuple(rows), first, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""),
                normalized_hash, schema_hash, None, raw_hash, normalized_hash, "1", query_dimensions,
                kwargs.get("source_record_reference") or f"{key}:{normalized_hash[:16]}",
            )
        except UniverseSourceRejected as exc:
            return UniverseFetchResult(key, "schema_changed", (), None, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), None, None, str(exc))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return UniverseFetchResult(key, "schema_changed", (), None, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), None, None, str(exc))


__all__ = [
    "APPROVED_PROVIDER_HOSTS", "APPROVED_RESOURCE_KEYS", "EXCLUDED_RESOURCE_KEYS",
    "LEGACY_RESOURCE_KEYS", "UniverseCollector", "UniverseFetchResult",
    "UniverseSourceRejected", "assert_approved_resource", "parse_universe_payload",
]
