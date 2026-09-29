"""Offline issuer-release reconciliation; no source selection or TTM calculation.

The tested format pairs a quarterly report with its financial tables. Matching
overlapping disclosures is evidence of consistency, not a corporate-action audit.
"""
import calendar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
import hashlib
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from src.collectors.earnings_note_audit import read_pdf_pages, _Text, MAX_PAGES, MAX_TEXT_CHARS
from src.collectors.earnings_source_audit import EarningsSourceAuditError
from src.domain.valuation import normalize_utc_timestamp

QUARTER_VERSION = "earnings-quarter-release-reconciliation-v1"


@dataclass(frozen=True)
class QuarterProfile:
    company: str
    abbreviation: str
    host: str
    path_prefix: str
    ticker: str


# Parser coverage only; this is not a list of authenticated financial inputs.
PROFILES = {"2303.TW": QuarterProfile("United Microelectronics Corporation", "UMC", "www.umc.com",
    "/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/", "2303")}
_ORDINALS = ("First", "Second", "Third", "Fourth")
_MONTHS = ("March", "June", "September", "December")
_INTEGER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"


def _fail(reason):
    raise EarningsSourceAuditError("quarter_" + reason)


def _compact(text):
    return "".join(text.split())


def _period(year, quarter):
    month = quarter * 3
    return (date(year, month - 2, 1).isoformat(), date(year, month, calendar.monthrange(year, month)[1]).isoformat())


def _previous(year, quarter):
    return (year, quarter - 1) if quarter > 1 else (year - 1, 4)


def _date_label(year, quarter):
    return f"{_MONTHS[quarter - 1]}{_period(year, quarter)[1][-2:]},{year}"


def _page(pages, label):
    matches = [(index + 1, text) for index, text in enumerate(pages) if _compact(label) in _compact(text)]
    if len(matches) != 1:
        _fail("page_missing_or_ambiguous")
    return matches[0]


def _between(text, label, end_label):
    view = _Text(text)
    try:
        start = view.unique(label)
        end = view.unique(end_label)
    except EarningsSourceAuditError:
        _fail("label_missing_or_ambiguous")
    if start.end() >= end.start():
        _fail("row_order_invalid")
    left = view.span(start.start(), start.end())[1]
    right = view.span(end.start(), end.end())[0]
    return text[left:right], dict(start=left, end=right)


def _cells(raw, specs):
    """Fixed cells: decimal precision, integer, or explicitly ignored percentage.

    A missing amount is never zero. Percentages can contain a dash but cannot
    absorb extra cells, currency text, formulas or scientific notation.
    """
    tokens = raw.split()
    if len(tokens) != len(specs):
        _fail("numeric_column_count")
    values = []
    for token, spec in zip(tokens, specs):
        if spec in ("percent", "change"):
            number = r"\d{1,5}(?:\.\d{1,2})?" + ("%" if spec == "percent" else "")
            if token != "-" and not re.fullmatch(rf"(?:-?{number}|\({number}\))", token):
                _fail("percentage_cell_invalid")
            values.append(None)
            continue
        pattern = _INTEGER + (rf"\.\d{{{spec}}}" if spec else "")
        if not re.fullmatch(rf"(?:-?{pattern}|\({pattern}\))", token):
            _fail("numeric_cell_invalid")
        token = token.replace(",", "")
        if len(token) > 20:
            _fail("numeric_precision_unsupported")
        values.append("-" + token[1:-1] if token.startswith("(") else token)
    return values


def _ratio_consistent(numerator_millions, shares, eps):
    with localcontext() as context:
        context.prec = 50
        n, d, e = map(Decimal, (numerator_millions, shares, eps))
        if d <= Decimal(".5"):
            return False
        possible = [(n + dn) * 1_000_000 / (d + dd)
                    for dn in (Decimal("-.5"), Decimal(".5")) for dd in (Decimal("-.5"), Decimal(".5"))]
        return max(possible) >= e - Decimal(".005") and min(possible) <= e + Decimal(".005")


def parse_quarter_release_pages(report_pages, statement_pages, *, profile, year, quarter):
    if type(year) is not int or not 2013 <= year <= 2200 or type(quarter) is not int or quarter not in (1, 2, 3, 4):
        _fail("period_invalid")
    for pages in (report_pages, statement_pages):
        if (not 1 <= len(pages) <= MAX_PAGES or any(not isinstance(t, str) or len(t) > 50_000 for t in pages)
                or sum(map(len, pages)) > MAX_TEXT_CHARS):
            _fail("text_limit")
    cover = _compact(report_pages[0])
    if (_compact(profile.company) not in cover or f"TWSE:{profile.ticker}" not in cover
            or f"{profile.abbreviation}Reports{_ORDINALS[quarter - 1]}Quarter{year}Results" not in cover):
        _fail("report_identity_or_period_mismatch")
    report_page, report = _page(report_pages, "Summary of Operating Results")
    table_page, table = _page(statement_pages, "Year over Year Comparison Quarter over Quarter Comparison")
    compact_table = _compact(table)
    for label in (profile.company.upper() + " AND SUBSIDIARIES",
                  "Consolidated Condensed Statements of Comprehensive Income",
                  "Figures in Millions of New Taiwan Dollars (NT$) and U.S. Dollars (US$)",
                  "Except Per Share and Per ADS Data", "(2) 1 ADS equals 5 common shares."):
        if _compact(label) not in compact_table:
            _fail("table_identity_scope_or_units_mismatch")
    if any(word in table.lower() for word in ("preferred", "discontinued", "restated", "adjusted earnings")):
        _fail("special_basis_requires_separate_review")
    prior = _previous(year, quarter)
    periods = [(year, quarter), prior, (year - 1, quarter)]
    expected_head = f"{_date_label(year - 1, quarter)}Chg.{_date_label(*prior)}Chg.US$NT$NT$%US$NT$NT$%"
    expected_dates = "Three-MonthPeriodEndedThree-MonthPeriodEnded" + _date_label(year, quarter) * 2
    if not compact_table.startswith(expected_head) or expected_dates not in compact_table:
        _fail("table_period_or_currency_column_order")

    # Exact ordinary basic-share counts are stated separately from diluted and
    # end-of-period shares. The latter are never a fallback denominator.
    shares_text, shares_span = _between(report, "basic weighted average number of shares outstanding in", "The diluted weighted")
    shares_match = re.fullmatch(r"([1-4]Q\d{2})was(" + _INTEGER + r"),comparedwith(" + _INTEGER
        + r")sharesin([1-4]Q\d{2})and(" + _INTEGER + r")sharesin([1-4]Q\d{2})\.", _compact(shares_text))
    if not shares_match:
        _fail("basic_share_disclosure_missing_or_unsupported")
    if [shares_match[i] for i in (1, 4, 6)] != [f"{q}Q{y % 100:02d}" for y, q in periods]:
        _fail("share_period_order_mismatch")
    exact_shares = [shares_match[i].replace(",", "") for i in (2, 3, 5)]
    if any(len(value) > 16 or Decimal(value) <= 0 for value in exact_shares):
        _fail("share_value_invalid")
    eps_raw, eps_span = _between(table, "Earnings per share-basic", "Earnings per ADS (2)")
    eps_cells = _cells(eps_raw, [3, 2, 2, 3, 2, 2])
    income_block, income_block_span = _between(table, "Net income attributable to:", "Comprehensive income (loss) attributable to:")
    income_raw, income_span = _between(income_block, "Shareholders of the parent", "Non-controlling interests")
    income_cells = _cells(income_raw, [0, 0, 0, "percent", 0, 0, 0, "percent"])
    rounded_raw, rounded_span = _between(table, "Weighted average number of shares outstanding (in millions)", "Notes:")
    rounded = _cells(rounded_raw, [0, 0, 0, 0])
    if (eps_cells[:2] != eps_cells[3:5] or income_cells[:2] != income_cells[4:6] or rounded[0] != rounded[2]):
        _fail("repeated_current_column_conflict")
    eps_values = [eps_cells[i] for i in (1, 5, 2)]
    income_values = [income_cells[i] for i in (1, 6, 2)]
    rounded_values = [rounded[i] for i in (0, 3, 1)]
    report_eps_raw, report_eps_span = _between(report, "EPS (NT$ per share)", "EPS (US$ per ADS)")
    report_eps = _cells(report_eps_raw, [2, 2, 2])
    report_income_raw, report_income_span = _between(report, "Net Income Attributable to Shareholders of the Parent", "EPS (NT$ per share)")
    report_income = _cells(report_income_raw, [0, 0, "change", 0, "change"])
    header = f"OperatingResults(Amount:NT$million){quarter}Q{year % 100:02d}{prior[1]}Q{prior[0] % 100:02d}QoQ%change{quarter}Q{(year - 1) % 100:02d}YoY%change"
    if header not in _compact(report):
        _fail("report_table_period_or_units_mismatch")
    rows = []
    for index, ((y, q), eps, numerator, shares, displayed) in enumerate(zip(periods, eps_values, income_values, exact_shares, rounded_values)):
        reasons = []
        if Decimal(eps) != Decimal(report_eps[index]) or Decimal(numerator) != Decimal(report_income[(0, 1, 3)[index]]):
            reasons.append("report_and_statement_value_conflict")
        if abs(Decimal(shares) - Decimal(displayed) * 1_000_000) > 500_000:
            reasons.append("rounded_share_count_conflict")
        if not _ratio_consistent(numerator, shares, eps):
            reasons.append("earnings_ratio_inconsistent")
        start, end = _period(y, q)
        rows.append(dict(period_start=start, period_end=end, period_type="quarter", eps_kind="basic",
            profit_scope="parent_common_shareholders", statement_scope="consolidated",
            reported_eps=eps, unit="TWD_per_share", numerator=numerator, numerator_unit="TWD_millions",
            weighted_average_shares=shares, shares_unit="common_shares", rounded_shares_millions=displayed,
            link_status="reconciled" if not reasons else "quality_warning", reasons=reasons,
            calculation_eligible=False, locators=dict(report_page=report_page, statement_page=table_page,
                report_column=index + 1, statement_eps_cell=(2, 6, 3)[index],
                statement_income_cell=(2, 7, 3)[index], statement_shares_cell=(1, 4, 2)[index], basic_shares=shares_span,
                report_eps=report_eps_span, report_income=report_income_span, statement_eps=eps_span,
                statement_income={key: value + income_block_span["start"] for key, value in income_span.items()},
                statement_rounded_shares=rounded_span)))
    return rows


def audit_quarter_release(report_raw, statement_raw, *, symbol, year, quarter, report_source, statement_source):
    profile = PROFILES.get(symbol)
    if profile is None:
        _fail("company_profile_not_supported")
    if type(year) is not int or type(quarter) is not int or not 2013 <= year <= 2200 or quarter not in (1, 2, 3, 4):
        _fail("period_invalid")
    end = _period(year, quarter)[1]
    sources = []
    for raw, receipt, suffix in [(report_raw, report_source, "report"), (statement_raw, statement_source, "financial_statements-E")]:
        url = urlsplit(receipt["url"])
        path = f"{profile.path_prefix}{year}/Q{quarter}_{year}/{profile.abbreviation}{year % 100:02d}Q{quarter}_{suffix}.pdf"
        if url.scheme != "https" or url.netloc != profile.host or url.path != path or url.query or url.fragment:
            _fail("source_url_not_supported")
        observed = normalize_utc_timestamp(receipt["observed_at"], "observed_at")
        if datetime.fromisoformat(observed).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat() < end:
            _fail("observed_before_period_end")
        sources.append(dict(url=receipt["url"], observed_at=observed, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
    if sources[0]["sha256"] == sources[1]["sha256"]:
        _fail("report_and_statements_must_be_distinct")
    rows = parse_quarter_release_pages(read_pdf_pages(report_raw), read_pdf_pages(statement_raw),
                                       profile=profile, year=year, quarter=quarter)
    available = max(source["observed_at"] for source in sources)
    return dict(contract=QUARTER_VERSION, symbol=symbol, report_year=year, report_quarter=quarter, report_period_end=end,
        sources=dict(report=sources[0], statements=sources[1]), rows=rows, available_at=available,
        source_published_at=None, provenance_status="local_files_and_caller_receipts_not_authenticated",
        source_kind="issuer_quarterly_earnings_release", audit_opinion_status="not_asserted",
        status="reconciled" if all(row["link_status"] == "reconciled" for row in rows) else "quality_warning",
        calculation_eligible=False, historical_eligibility="not_asserted", value=None)


def compare_quarter_releases(audits):
    results = []
    for symbol in sorted({audit["symbol"] for audit in audits}):
        documents = [audit for audit in audits if audit["symbol"] == symbol]
        latest = max((doc["report_year"], doc["report_quarter"]) for doc in documents)
        windows = [latest]
        for _ in range(3):
            windows.append(_previous(*windows[-1]))
        quarters = []
        for y, q in reversed(windows):
            start, end = _period(y, q)
            versions = [dict(row, report_sha256=doc["sources"]["report"]["sha256"],
                             statements_sha256=doc["sources"]["statements"]["sha256"], available_at=doc["available_at"])
                        for doc in documents for row in doc["rows"] if (row["period_start"], row["period_end"]) == (start, end)]
            variants = {(Decimal(row["reported_eps"]), Decimal(row["numerator"]), Decimal(row["weighted_average_shares"]), row["eps_kind"],
                         row["statement_scope"], row["unit"], row["shares_unit"], row["numerator_unit"], row["profit_scope"])
                        for row in versions}
            status = ("missing" if not versions else "quality_warning" if any(row["link_status"] != "reconciled" for row in versions)
                      else "conflicting_versions" if len(variants) > 1 else "observed_consistent")
            quarters.append(dict(period_start=start, period_end=end, status=status,
                                 versions=sorted(versions, key=lambda row: (row["report_sha256"], row["statements_sha256"])), selected_version=None))
        observed_count = sum(q["status"] == "observed_consistent" for q in quarters)
        results.append(dict(symbol=symbol, quarters=quarters, observed_consistent_quarter_count=observed_count,
            four_quarter_coverage_complete=observed_count == 4,
            window_end=_period(*latest)[1], value=None, calculation_eligible=False, cross_period_basis_verified=False,
            remaining_gate="corporate_action_and_revision_coverage_not_verified"))
    return results
