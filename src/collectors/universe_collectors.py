"""Fixture-oriented Universe source contracts; no live provider is called in tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from src.domain.universe import UniverseVenue, validate_official_code


APPROVED_RESOURCE_KEYS = frozenset({
    "twse.t187ap03_L", "twse.company.newlisting", "twse.company.suspendListingCsvAndHtml",
    "tpex.mopsfin_t187ap03_O", "tpex.company.deListed", "tpex.spendi.cmode", "tpex.company.current",
})
EXCLUDED_RESOURCE_KEYS = frozenset({"tpex_mainboard_quotes", "price_context_out_of_scope"})
APPROVED_PROVIDER_HOSTS = frozenset({"twse.com.tw", "tpex.org.tw"})
PRICE_FIELD_NAMES = frozenset({
    "price", "close", "open", "high", "low", "volume", "turnover",
    "成交價", "收盤價", "開盤價", "最高價", "最低價", "成交量", "成交金額",
})


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


def assert_approved_resource(resource_key: str) -> str:
    key = str(resource_key).strip()
    if key in EXCLUDED_RESOURCE_KEYS or "quote" in key.lower() or "price" in key.lower():
        raise UniverseSourceRejected("quote_source_excluded")
    if key not in APPROVED_RESOURCE_KEYS:
        raise UniverseSourceRejected("universe_source_not_approved")
    return key


def parse_universe_payload(resource_key: str, payload: Any) -> list[dict[str, Any]]:
    key = assert_approved_resource(resource_key)
    if not isinstance(payload, (dict, list)):
        raise UniverseSourceRejected("source_schema_changed")
    rows: Any = payload.get("data") if isinstance(payload, dict) else payload
    if rows is None and isinstance(payload, dict):
        rows = payload.get("tables")
    if not isinstance(rows, list):
        raise UniverseSourceRejected("source_schema_changed")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise UniverseSourceRejected("source_schema_changed")
        if any(str(field).strip().lower() in PRICE_FIELD_NAMES for row in rows for field in row):
            raise UniverseSourceRejected("price_field_excluded")
        if key in {"twse.t187ap03_L", "tpex.mopsfin_t187ap03_O", "tpex.company.current"}:
            code = row.get("code") or row.get("公司代號") or row.get("SecuritiesCompanyCode")
            if code is None:
                raise UniverseSourceRejected("source_schema_changed")
            parsed.append({**row, "official_code": validate_official_code(str(code))})
        else:
            parsed.append(dict(row))
    return parsed


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
            rows = parse_universe_payload(key, raw)
            # The observation timestamp is assigned only after transport, JSON,
            # schema and parser acceptance; it is independent of coverage.
            first = kwargs.get("first_observed_at") or kwargs.get("received_at") or kwargs.get("fetched_at")
            return UniverseFetchResult(key, "accepted", tuple(rows), first, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), kwargs.get("payload_sha256"), kwargs.get("schema_fingerprint"))
        except UniverseSourceRejected as exc:
            return UniverseFetchResult(key, "schema_changed", (), None, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), None, None, str(exc))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return UniverseFetchResult(key, "schema_changed", (), None, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), None, None, str(exc))


__all__ = ["APPROVED_PROVIDER_HOSTS", "APPROVED_RESOURCE_KEYS", "EXCLUDED_RESOURCE_KEYS", "UniverseCollector", "UniverseFetchResult", "UniverseSourceRejected", "assert_approved_resource", "parse_universe_payload"]
