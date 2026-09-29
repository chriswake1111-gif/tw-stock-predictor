"""Read-only source feasibility audit; never qualifies EPS for calculation.

Reads local MOPS inline-XBRL documents without resolving schemas, URLs or
entities. The downloaded main statements do not by themselves establish a
common share basis. Parsed facts remain outside the financial database.
"""
from __future__ import annotations

import calendar
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import normalize_utc_timestamp

PARSER_VERSION = "mops-earnings-source-audit-v1"
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
IX = "http://www.xbrl.org/2013/inlineXBRL"
XBRL = "http://www.xbrl.org/2003/instance"
ISO4217 = "http://www.xbrl.org/2003/iso4217"
CONCEPTS = {
    "BasicEarningsLossPerShare": ("basic", "total"),
    "DilutedEarningsLossPerShare": ("diluted", "total"),
    "BasicEarningsLossPerShareFromContinuingOperations": ("basic", "continuing_operations"),
    "DilutedEarningsLossPerShareFromContinuingOperations": ("diluted", "continuing_operations"),
}


class EarningsSourceAuditError(ValueError):
    """Bounded, non-sensitive reason suitable for a command-line report."""


def _fail(reason):
    raise EarningsSourceAuditError(reason)


def _source_identity(url, code, year, quarter):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "mopsov.twse.com.tw"
            or parsed.path != "/server-java/FileDownLoad" or parsed.fragment):
        _fail("unsupported_source_locator")
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {"functionName": ["t164sb01"], "step": ["9"], "co_id": [code],
                "year": [str(year)], "season": [str(quarter)]}
    if (set(query) != set(expected) | {"report_id"}
            or any(query.get(k) != v for k, v in expected.items())
            or query["report_id"] not in (["A"], ["C"])):
        _fail("source_locator_identity_mismatch")
    return "consolidated" if query["report_id"] == ["C"] else "separate"


def _period(start, end):
    if not all(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in (start, end)):
        _fail("invalid_period")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        _fail("invalid_period")
    if first > last:
        _fail("invalid_period")
    if last.month not in (3, 6, 9, 12) or last.day != calendar.monthrange(last.year, last.month)[1]:
        return "unsupported_period"
    if first == date(last.year, last.month - 2, 1):
        return "single_quarter"
    if first == date(last.year, 1, 1):
        return "annual" if last.month == 12 else "year_to_date"
    return "unsupported_period"


def _qname(value, namespaces):
    if not value or value.count(":") != 1:
        _fail("invalid_qname")
    prefix, local = value.split(":")
    if prefix not in namespaces:
        _fail("unknown_namespace")
    return namespaces[prefix], local


def _numeric(element, namespaces):
    if element.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") in {"true", "1"}:
        return None
    if list(element) or element.attrib.get("continuedAt"):
        _fail("unsupported_numeric_content")
    uri, transform = _qname(element.attrib.get("format"), namespaces)
    if (uri != "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"
            or transform != "numdotdecimal"):
        _fail("unsupported_numeric_transform")
    value = (element.text or "").strip()
    if len(value) > 32 or not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        _fail("invalid_numeric_value")
    scale = element.attrib.get("scale", "0")
    decimals = element.attrib.get("decimals", "")
    sign = element.attrib.get("sign", "")
    if not re.fullmatch(r"-?[0-6]", scale) or not re.fullmatch(r"[0-6]", decimals) or sign not in {"", "-"}:
        _fail("unsupported_numeric_attributes")
    try:
        number = Decimal(value.replace(",", "")) * Decimal(10) ** int(scale)
        if sign == "-":
            number = -number
    except InvalidOperation:
        _fail("invalid_numeric_value")
    if not number.is_finite() or abs(number) > Decimal("1000000"):
        _fail("invalid_numeric_value")
    return format(number, "f")


def audit_earnings_document(raw: bytes, *, symbol: str, year: int, quarter: int,
                            source_url: str, observed_at: str) -> dict:
    """Extract typed observations, with a permanently closed qualification gate.

    ``source_url`` and ``observed_at`` describe the caller's acquisition receipt;
    they do not authenticate a file or prove a historical publication date.
    """
    _, code = parse_canonical_symbol(symbol)
    if type(year) is not int or not 2013 <= year <= 2200 or type(quarter) is not int or quarter not in (1, 2, 3, 4):
        _fail("invalid_report_period")
    observed_at = normalize_utc_timestamp(observed_at, "observed_at")
    report_end = date(year, quarter * 3, calendar.monthrange(year, quarter * 3)[1])
    observed_day = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Taipei")).date()
    if report_end > observed_day:
        _fail("report_after_observation")
    scope = _source_identity(source_url, code, year, quarter)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_DOCUMENT_BYTES:
        _fail("document_size_invalid")
    # Require UTF-8 before scanning declarations, including UTF-16 NUL evasions.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        _fail("unsupported_document_encoding")
    if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        _fail("xml_declarations_forbidden")
    namespaces = {}
    try:
        for _, (prefix, uri) in ET.iterparse(io.StringIO(text), events=("start-ns",)):
            if prefix in namespaces and namespaces[prefix] != uri:
                _fail("namespace_rebinding")
            namespaces[prefix] = uri
        root = ET.fromstring(text)
    except ET.ParseError:
        _fail("invalid_xml")
    elements = list(root.iter())
    if len(elements) > 60000:
        _fail("document_element_limit")
    if root.tag != "{http://www.w3.org/1999/xhtml}html":
        _fail("unsupported_document_root")
    contexts, units = {}, {}
    for element in elements:
        if element.tag == f"{{{XBRL}}}context":
            key = element.attrib.get("id")
            if not key or key in contexts:
                _fail("duplicate_or_missing_context_id")
            contexts[key] = element
        elif element.tag == f"{{{XBRL}}}unit":
            key = element.attrib.get("id")
            if not key or key in units:
                _fail("duplicate_or_missing_unit_id")
            units[key] = element
    observations, skipped, facts = {}, [], []
    earnings_count = 0
    for index, element in enumerate(elements):
        if element.tag != f"{{{IX}}}nonFraction":
            continue
        uri, name = _qname(element.attrib.get("name"), namespaces)
        facts.append(name)
        if name not in CONCEPTS:
            continue
        earnings_count += 1
        if earnings_count > 2048:
            _fail("earnings_fact_limit")
        if not re.fullmatch(r"https?://xbrl\.ifrs\.org/taxonomy/\d{4}-\d{2}-\d{2}/ifrs-full", uri):
            _fail("unsupported_earnings_namespace")
        context_id = element.attrib.get("contextRef")
        context = contexts.get(context_id)
        if context is None:
            _fail("missing_context")
        identifiers = context.findall(f"{{{XBRL}}}entity/{{{XBRL}}}identifier")
        identifier = identifiers[0] if len(identifiers) == 1 else None
        if identifier is None or identifier.text != code or identifier.attrib.get("scheme") != "http://www.twse.com.tw":
            _fail("entity_mismatch")
        start = context.findtext(f"{{{XBRL}}}period/{{{XBRL}}}startDate")
        end = context.findtext(f"{{{XBRL}}}period/{{{XBRL}}}endDate")
        period_type = _period(start, end)
        if end > report_end.isoformat():
            _fail("fact_after_report_period")
        if context.find(f"{{{XBRL}}}scenario") is not None or context.find(f"{{{XBRL}}}entity/{{{XBRL}}}segment") is not None:
            skipped.append({"context_id": context_id, "reason": "dimensional_context_not_supported"})
            continue
        unit = units.get(element.attrib.get("unitRef"))
        if unit is None:
            _fail("missing_unit")
        numerator = unit.find(f"{{{XBRL}}}divide/{{{XBRL}}}unitNumerator/{{{XBRL}}}measure")
        denominator = unit.find(f"{{{XBRL}}}divide/{{{XBRL}}}unitDenominator/{{{XBRL}}}measure")
        if (numerator is None or denominator is None or len(list(unit.iter())) != 6
                or _qname(numerator.text, namespaces) != (ISO4217, "TWD")
                or _qname(denominator.text, namespaces) != (XBRL, "shares")):
            _fail("unsupported_earnings_unit")
        value = _numeric(element, namespaces)
        kind, profit_scope = CONCEPTS[name]
        key = (start, end, kind, profit_scope)
        if key in observations:
            previous = observations[key]["value"]
            if ((previous is None) != (value is None)
                    or (value is not None and Decimal(previous) != Decimal(value))):
                _fail("duplicate_fact_conflict")
            observations[key]["locators"].append(f"element:{index};context:{context_id}")
            continue
        observations[key] = dict(period_start=start, period_end=end, period_type=period_type,
            eps_kind=kind, profit_scope=profit_scope, statement_scope=scope, value=value,
            unit="TWD_per_share", source_concept=element.attrib["name"],
            taxonomy_uri=uri, context_id=context_id, decimals=element.attrib.get("decimals"),
            locators=[f"element:{index};context:{context_id}"],
            source_published_at=None, observed_at=observed_at,
            value_status="reported" if value is not None else "source_nil")
    rows = [observations[key] for key in sorted(observations)]
    if not rows:
        _fail("no_supported_earnings_facts")
    if not any(row["period_end"] == report_end.isoformat() for row in rows):
        _fail("report_period_not_found")
    return dict(contract=PARSER_VERSION, symbol=symbol, report_year=year,
        report_quarter=quarter, report_period_end=report_end.isoformat(),
        statement_scope=scope, source_url=source_url, observed_at=observed_at,
        raw_sha256=hashlib.sha256(raw).hexdigest(), raw_bytes=len(raw),
        provenance_status="local_file_and_caller_receipt_not_authenticated",
        status="insufficient_data", value=None, calculation_eligible=False,
        historical_eligibility="not_asserted", rows=rows, skipped_contexts=skipped,
        weighted_average_tag_names=sorted({name for name in facts if "WeightedAverage" in name}),
        reasons=["share_basis_not_verified", "publication_and_revision_history_not_verified"],
        scope="source_feasibility_only_no_database_or_model_activation")


def compare_earnings_sources(audits: list[dict]) -> list[dict]:
    """Show gaps and conflicting source versions; never select or sum values."""
    groups = {}
    for audit in audits:
        key = (audit["symbol"], audit["statement_scope"])
        groups.setdefault(key, []).append(audit)
    results = []
    for (symbol, scope), documents in sorted(groups.items()):
        latest_end = max(item["report_period_end"] for item in documents)
        latest = date.fromisoformat(latest_end)
        ordinal = latest.year * 4 + (latest.month // 3) - 1
        target_periods = []
        for number in range(ordinal - 3, ordinal + 1):
            year, quarter_zero = divmod(number, 4)
            month = (quarter_zero + 1) * 3
            target_periods.append(date(year, month, calendar.monthrange(year, month)[1]).isoformat())
        single_periods, values = set(), {}
        for document in documents:
            for row in document["rows"]:
                key = (row["period_start"], row["period_end"], row["eps_kind"], row["profit_scope"])
                values.setdefault(key, []).append(dict(value=row["value"], raw_sha256=document["raw_sha256"],
                                                       source_url=document["source_url"], context_id=row["context_id"]))
                if row["period_type"] == "single_quarter" and row["eps_kind"] == "basic" and row["profit_scope"] == "total" and row["value"] is not None:
                    single_periods.add(row["period_end"])
        conflicts = []
        for key, entries in sorted(values.items()):
            variants = {None if entry["value"] is None else Decimal(entry["value"]) for entry in entries}
            if len(variants) > 1:
                conflicts.append(dict(period_start=key[0], period_end=key[1], eps_kind=key[2],
                                      profit_scope=key[3], reason="source_versions_differ_no_automatic_selection",
                                      versions=entries))
        results.append(dict(symbol=symbol, statement_scope=scope, latest_report_in_input=latest_end,
            target_periods=target_periods, missing_single_quarters=[p for p in target_periods if p not in single_periods],
            conflicts=conflicts, calculation_eligible=False, value=None,
            reason="share_basis_not_verified_even_if_four_quarters_exist"))
    return results
