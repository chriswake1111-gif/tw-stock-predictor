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
    "tpex.spendi.history", "tpex.spendi.today", "tpex.cmode",
    "tpex_spendi_history", "tpex_spendi_today", "tpex_cmode",
})
# TPEx company.html / company/otcSearch is a manual corroborating source
# until its machine response contract has been independently verified.  The
# migration keeps its resource/policy row for historical compatibility, but
# it is deliberately not a collector/parser contract.
MANUAL_ONLY_RESOURCE_KEYS = frozenset({"tpex.company.current"})
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


# These are source-backed contracts, rather than caller supplied schema
# declarations.  The field sets intentionally include only the fields
# observed in the approved first-party resources (plus documented aliases for
# the TPEx delisted/table response).  An unlisted field is schema drift.
_TWSE_MASTER_FIELDS = frozenset({
    "出表日期", "公司代號", "公司名稱", "公司簡稱", "外國企業註冊地國", "產業別",
    "住址", "營利事業統一編號", "董事長", "總經理", "發言人", "發言人職稱",
    "代理發言人", "總機電話", "成立日期", "上市日期", "普通股每股面額",
    "實收資本額", "私募股數", "特別股", "編制財務報表類型", "股票過戶機構",
    "過戶電話", "過戶地址", "簽證會計師事務所", "簽證會計師1", "簽證會計師2",
    "英文簡稱", "英文通訊地址", "傳真機號碼", "電子郵件信箱", "網址",
    "已發行普通股數或TDR原股發行股數",
})
_TWSE_NEWLISTING_FIELDS = frozenset({
    "Code", "Company", "ApplicationDate", "Chairman", "AmountofCapital ",
    "CommitteeDate", "ApprovedDate", "AgreementDate", "ListingDate",
    "ApprovedListingDate", "Underwriter", "UnderwritingPrice", "Note",
})
_TWSE_TERMINATION_FIELDS = frozenset({"DelistingDate", "Company", "Code"})
_TPEX_MASTER_FIELDS = frozenset({
    "Date", "SecuritiesCompanyCode", "CompanyName", "CompanyAbbreviation",
    "Registration", "SecuritiesIndustryCode", "Address", "UnifiedBusinessNo.",
    "Chairman", "GeneralManager", "Spokesman", "TitleOfSpokesman",
    "DeputySpokesperson", "Telephone", "DateOfIncorporation", "DateOfListing",
    "ParValueOfCommonStock", "Paidin.Capital.NTDollars", "PrivateStock.shares",
    "PreferredStock.shares", "PreparationOfFinancialReportType", "StockTransferAgent",
    "StockTransferAgentTelephone", "StockTransferAgentAddress", "AccountingFirm",
    "CPA.CharteredPublicAccountant.First", "CPA.CharteredPublicAccountant.Second",
    "Symbol", "Fax", "EmailAddress", "WebAddress", "IssueShares",
})
_TPEX_DELISTED_FIELDS = frozenset({
    "股票代號", "公司名稱", "終止上櫃日期", "終止上櫃原因", "公司資料網址",
    # Logical aliases are accepted only for the explicit manual/table
    # contract; they are not generic fallbacks for other sources.
    "code", "name", "year", "reason", "url",
})
_TPEX_SPENDI_HISTORY_FIELDS = frozenset({
    "Date", "Serial", "SecuritiesCompanyCode", "CompanyName",
    "DateOfSuspendedTrading", "TimeOfSuspendedTrading",
    "DateOfResumedTrading", "TimeOfResumedTrading",
})
_TPEX_SPENDI_TODAY_FIELDS = frozenset({
    "Date", "Serial", "SecuritiesCompanyCode", "CompanyName",
    "DateOfSuspendedTrading", "TimeOfSuspendedTrading",
    "DateOfResumedTrading", "TimeOfResumedTrading", "暫停交易", "恢復交易",
})
_TPEX_CMODE_FIELDS = frozenset({
    "Date", "SecuritiesCompanyCode", "CompanyName", "AlteredTrading",
    "PeriodicTrading", "ManagedStock", "MatchingFrequency", "SuspensionOfTrading",
    " FinancialAnnouncements", "FinancialAnnouncements",
})


def _contract(
    *,
    kind: str,
    envelope: str,
    required: dict[str, tuple[str, ...]],
    allowed_fields: frozenset[str],
    value_required: frozenset[str] = frozenset(),
    table_fields: frozenset[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "envelope": envelope,
        "required": required,
        "allowed_fields": allowed_fields,
        "value_required": value_required,
        "table_fields": table_fields,
        "field_types": {field: str for field in allowed_fields},
    }


_RESOURCE_CONTRACTS = {
    "twse.t187ap03_L": _contract(
        kind="master", envelope="array",
        required={"code": ("公司代號",), "name": ("公司名稱",)},
        allowed_fields=_TWSE_MASTER_FIELDS, value_required=frozenset({"code", "name"}),
    ),
    "twse.company.newlisting": _contract(
        kind="listing", envelope="array",
        required={
            "code": ("Code",), "company": ("Company",),
            "application_date": ("ApplicationDate",),
            "approved_date": ("ApprovedDate",),
            "agreement_date": ("AgreementDate",),
            "listing_date": ("ListingDate",),
            "approved_listing_date": ("ApprovedListingDate",),
        },
        allowed_fields=_TWSE_NEWLISTING_FIELDS,
        value_required=frozenset({"code", "company", "application_date", "approved_date", "agreement_date", "approved_listing_date"}),
    ),
    "twse.company.suspendListingCsvAndHtml": _contract(
        kind="termination", envelope="array",
        required={"date": ("DelistingDate",), "company": ("Company",), "code": ("Code",)},
        allowed_fields=_TWSE_TERMINATION_FIELDS,
        value_required=frozenset({"date", "company", "code"}),
    ),
    "tpex.mopsfin_t187ap03_O": _contract(
        kind="master", envelope="array",
        required={"code": ("SecuritiesCompanyCode",), "name": ("CompanyName",)},
        allowed_fields=_TPEX_MASTER_FIELDS, value_required=frozenset({"code", "name"}),
    ),
    "tpex.company.deListed": _contract(
        kind="termination", envelope="tables",
        required={
            "code": ("股票代號", "code"),
            "date": ("終止上櫃日期", "year"),
            "reason": ("終止上櫃原因", "reason"),
        },
        allowed_fields=_TPEX_DELISTED_FIELDS,
        value_required=frozenset({"code", "date", "reason"}),
        table_fields=_TPEX_DELISTED_FIELDS,
    ),
    "tpex.spendi.history": _contract(
        kind="operational", envelope="array",
        required={
            "date": ("Date",), "code": ("SecuritiesCompanyCode",),
            "company": ("CompanyName",),
            "suspended_date": ("DateOfSuspendedTrading",),
            "suspended_time": ("TimeOfSuspendedTrading",),
            "resumed_date": ("DateOfResumedTrading",),
            "resumed_time": ("TimeOfResumedTrading",),
        },
        allowed_fields=_TPEX_SPENDI_HISTORY_FIELDS,
        value_required=frozenset({"date"}),
    ),
    "tpex.spendi.today": _contract(
        kind="operational", envelope="array",
        required={
            "date": ("Date",), "code": ("SecuritiesCompanyCode",),
            "company": ("CompanyName",),
            "suspended_date": ("DateOfSuspendedTrading", "暫停交易"),
            "resumed_date": ("DateOfResumedTrading", "恢復交易"),
        },
        allowed_fields=_TPEX_SPENDI_TODAY_FIELDS,
        value_required=frozenset({"date"}),
    ),
    "tpex.cmode": _contract(
        kind="operational", envelope="array",
        required={
            "date": ("Date",), "code": ("SecuritiesCompanyCode",),
            "company": ("CompanyName",), "altered": ("AlteredTrading",),
            "periodic": ("PeriodicTrading",), "managed": ("ManagedStock",),
            "matching_frequency": ("MatchingFrequency",),
            "suspension": ("SuspensionOfTrading",),
            "financial_announcements": (" FinancialAnnouncements", "FinancialAnnouncements"),
        },
        allowed_fields=_TPEX_CMODE_FIELDS,
        value_required=frozenset({"date"}),
    ),
}
_CODE_FIELDS = ("code", "official_code", "公司代號", "Code", "SecuritiesCompanyCode", "證券代號", "股票代號")
_DATE_FIELDS = ("date", "Date", "effective_date", "source_effective_date", "上市日期", "交易日期", "year", "年度", "DelistingDate", "終止上櫃日期")
_STATE_FIELDS = ("status", "state", "trading_state", "交易狀態", "處置狀態", "session_status")
_REASON_FIELDS = ("reason", "termination_reason", "下市原因", "終止上市原因", "終止上櫃原因")


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
    if key in MANUAL_ONLY_RESOURCE_KEYS:
        raise UniverseSourceRejected("manual_source_contract_not_for_ingestion")
    if key not in APPROVED_RESOURCE_KEYS:
        raise UniverseSourceRejected("universe_source_not_approved")
    return _RESOURCE_ALIASES.get(key, key)


def _extract_rows(resource_key: str, payload: Any) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    contract = _RESOURCE_CONTRACTS[resource_key]
    envelope = contract["envelope"]
    if envelope == "array":
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise UniverseSourceRejected("source_schema_changed")
        return [dict(row) for row in payload], "array", {"type": "top_level_array"}

    if envelope != "tables" or not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
        raise UniverseSourceRejected("source_schema_changed")

    rows: list[dict[str, Any]] = []
    observed_table_fields: set[str] = set()
    expected_table_fields = contract.get("table_fields") or contract["allowed_fields"]
    for table in payload["tables"]:
        if not isinstance(table, dict) or not isinstance(table.get("fields"), list) or not isinstance(table.get("data"), list):
            raise UniverseSourceRejected("source_schema_changed")
        fields = table["fields"]
        if not all(isinstance(field, str) for field in fields):
            raise UniverseSourceRejected("source_schema_changed")
        field_set = set(fields)
        if not field_set.issubset(expected_table_fields):
            raise UniverseSourceRejected("source_schema_changed")
        observed_table_fields.update(field_set)
        for raw_row in table["data"]:
            if not isinstance(raw_row, list) or len(raw_row) != len(fields):
                raise UniverseSourceRejected("source_schema_changed")
            rows.append({field: value for field, value in zip(fields, raw_row)})
    if observed_table_fields and not observed_table_fields.issubset(expected_table_fields):
        raise UniverseSourceRejected("source_schema_changed")
    for group_fields in contract["required"].values():
        if not any(field in observed_table_fields for field in group_fields):
            raise UniverseSourceRejected("source_schema_changed")
    return rows, "tables", {"type": "tables", "table_fields": sorted(observed_table_fields)}


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
        if set(row) - contract["allowed_fields"]:
            raise UniverseSourceRejected("source_schema_changed")
        for group_name, fields in contract["required"].items():
            if not any(field in row for field in fields):
                raise UniverseSourceRejected("source_schema_changed")
            if group_name in contract["value_required"] and not _has_value(row, fields):
                raise UniverseSourceRejected("source_schema_changed")
        for field, expected_type in contract["field_types"].items():
            if field in row and row[field] not in (None, "") and not isinstance(row[field], expected_type):
                raise UniverseSourceRejected("source_schema_changed")
        if kind in {"master", "listing", "termination", "corroborating"}:
            code = next((row.get(field) for field in _CODE_FIELDS if row.get(field) not in (None, "")), None)
            value["official_code"] = validate_official_code(str(code))
        if kind == "master":
            if key == "twse.t187ap03_L":
                value["short_name"] = str(row.get("公司簡稱") or "").strip()
            elif key == "tpex.mopsfin_t187ap03_O":
                value["short_name"] = str(row.get("CompanyAbbreviation") or "").strip()
            else:
                value["short_name"] = str(row.get("公司簡稱") or row.get("CompanyAbbreviation") or "").strip()
        parsed.append(value)
    return parsed


def _expected_schema_descriptor(key: str) -> dict[str, Any]:
    contract = _RESOURCE_CONTRACTS[key]
    return {
        "logical_resource_key": key,
        "schema_version": "phase13",
        "envelope": contract["envelope"],
        "allowed_fields": sorted(contract["allowed_fields"]),
        "required_field_groups": {
            name: list(fields) for name, fields in contract["required"].items()
        },
        "value_required_groups": sorted(contract["value_required"]),
    }


def _schema_evidence(key: str, envelope: str, envelope_detail: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    contract = _RESOURCE_CONTRACTS[key]
    observed_fields = sorted({str(field) for row in rows for field in row})
    expected = _expected_schema_descriptor(key)
    return {
        "logical_resource_key": key,
        "schema_version": "phase13",
        "envelope": envelope,
        "envelope_detail": envelope_detail,
        "expected_schema_fingerprint": payload_fingerprint(expected),
        "allowed_fields": sorted(contract["allowed_fields"]),
        "required_field_groups": expected["required_field_groups"],
        "value_required_groups": expected["value_required_groups"],
        "observed_row_fields": observed_fields,
    }


def _observed_schema_fingerprint(schema_evidence: dict[str, Any]) -> str:
    """Return the shared persisted schema identity used by UniverseRepository."""
    return payload_fingerprint({
        "logical_resource_key": schema_evidence["logical_resource_key"],
        "schema_version": schema_evidence["schema_version"],
        "envelope": schema_evidence["envelope"],
        "required_field_groups": schema_evidence["required_field_groups"],
        "observed_row_fields": schema_evidence["observed_row_fields"],
    })


def _parse_with_evidence(resource_key: str, payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = assert_approved_resource(resource_key)
    rows, envelope, envelope_detail = _extract_rows(key, payload)
    parsed = _validate_rows(key, rows)
    return parsed, _schema_evidence(key, envelope, envelope_detail, rows)


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
            # The observed fingerprint is persisted as provenance and must use
            # the same canonical identity that UniverseRepository verifies.
            # The expected contract fingerprint remains separate and is the
            # fail-closed gate before this point.
            schema_hash = _observed_schema_fingerprint(schema_evidence)
            supplied_schema = kwargs.get("schema_fingerprint")
            if supplied_schema and str(supplied_schema).lower() != schema_hash:
                raise UniverseSourceRejected("schema_evidence_mismatch")
            query_dimensions = dict(kwargs.get("query_dimensions") or {})
            query_dimensions.setdefault("source_contract_key", key)
            query_dimensions["source_envelope"] = schema_evidence["envelope"]
            query_dimensions["source_envelope_detail"] = schema_evidence["envelope_detail"]
            query_dimensions["expected_schema_fingerprint"] = schema_evidence["expected_schema_fingerprint"]
            query_dimensions["allowed_fields"] = schema_evidence["allowed_fields"]
            query_dimensions["required_field_groups"] = schema_evidence["required_field_groups"]
            query_dimensions["value_required_groups"] = schema_evidence["value_required_groups"]
            query_dimensions["observed_row_fields"] = schema_evidence["observed_row_fields"]
            parser_version = "2.0.0" if key in ("twse.t187ap03_L", "tpex.mopsfin_t187ap03_O") else "1"
            return UniverseFetchResult(
                key, "accepted", tuple(rows), first, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""),
                normalized_hash, schema_hash, None, raw_hash, normalized_hash, parser_version, query_dimensions,
                kwargs.get("source_record_reference") or f"{key}:{normalized_hash[:16]}",
            )
        except UniverseSourceRejected as exc:
            return UniverseFetchResult(key, "schema_changed", (), None, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), None, None, str(exc))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return UniverseFetchResult(key, "schema_changed", (), None, kwargs.get("received_at", ""), kwargs.get("fetched_at", ""), None, None, str(exc))


__all__ = [
    "APPROVED_PROVIDER_HOSTS", "APPROVED_RESOURCE_KEYS", "EXCLUDED_RESOURCE_KEYS",
    "MANUAL_ONLY_RESOURCE_KEYS",
    "LEGACY_RESOURCE_KEYS", "UniverseCollector", "UniverseFetchResult",
    "UniverseSourceRejected", "assert_approved_resource", "parse_universe_payload",
]
