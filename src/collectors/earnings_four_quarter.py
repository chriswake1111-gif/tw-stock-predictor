"""Source-bounded four-quarter research calculation (FIN-01), never Forward EPS.

The production entry point accepts only the content-pinned source catalog. Page
parsers also check the disclosed capital roll-forward and overlapping quarters;
neither a caller's eligibility flag nor a local research note is an input.
"""
from datetime import date
from decimal import Decimal
import hashlib
import io
import re

from src.collectors.earnings_note_audit import _Text
from src.collectors.earnings_quarter_audit import (
    PROFILES, _between, _cells, _compact, _period, _previous,
    parse_quarter_release_pages,
)
from src.collectors.earnings_source_audit import EarningsSourceAuditError
from src.collectors.earnings_sources_v2 import CATALOG_VERSION, CATALOG_REVIEW_DATE, sources_for
from src.domain.valuation import normalize_utc_timestamp

CONTRACT = "earnings-four-quarter-v1"
DATASET = "VerifiedQuarterlyEarnings"
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
METHOD = "sum_of_four_published_basic_quarterly_ratios"
LIMITATIONS = [
    "四季已公布的基本每股盈餘相加，不等同公司以全年加權平均股數計算的年度值。",
    "僅涵蓋已核對版本及報表期間；來源修訂或新增季度需要重新核對。",
    "不是全年獲利預估，不用於估值倍數選擇、目標價或歷史回測。",
    "季報發布資料不全等於經查核財報；各文件性質及頁碼分別保留。",
]


def fail(reason):
    raise EarningsSourceAuditError(reason)


def read_reviewed_pdf(raw):
    """Called after content verification. No OCR, scripts, network or disk writes."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DOCUMENT_BYTES or not raw.startswith(b"%PDF-"):
        fail("earnings_document_size_or_format")
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if not 1 <= len(reader.pages) <= 120:
            fail("earnings_document_page_limit")
        pages = []
        size = 0
        for page in reader.pages:
            text = page.extract_text()
            if not isinstance(text, str) or len(text) > 50_000:
                fail("earnings_document_text_limit")
            size += len(text)
            if size > 1_000_000:
                fail("earnings_document_text_limit")
            pages.append(text)
        return pages
    except EarningsSourceAuditError:
        raise
    except Exception as exc:
        raise EarningsSourceAuditError("earnings_document_unreadable") from exc


def parse_parade_quarter(pages, year, quarter):
    if len(pages) != 1 or len(pages[0]) > 50_000:
        fail("quarter_page_format")
    text = pages[0]
    compact = _compact(text)
    if not compact.startswith("ParadeTechnologies,Ltd.andSubsidiaries."):
        fail("quarter_company_mismatch")
    for marker in ("CONSOLIDATED INCOME STATEMENTS", "USD in Thousands NTD in Thousands",
                   "Sequential Quarter Three Months ended", "NT$ version shall prevail"):
        if _compact(marker) not in compact:
            fail("quarter_scope_or_currency_mismatch")
    prior = _previous(year, quarter)
    periods = [(year, quarter), prior, (year, quarter), (year - 1, quarter)]
    if quarter != 1:
        periods += [(year, quarter), (year - 1, quarter)]
    months = ("Mar 31,", "Jun 30,", "Sep 30,", "Dec 31,")
    header = "".join(_compact(months[q - 1]) for _, q in periods * 2)
    header += "".join(str(y) for y, _ in periods * 2)
    if not compact.startswith("ParadeTechnologies,Ltd.andSubsidiaries." + header + "Revenue"):
        fail("quarter_column_period_mismatch")
    count = len(periods) * 2
    values = {}
    for key, label, end_label, decimals in (
        ("reported_eps", "EPS - Basic (In Dollar)", "Shares used in computing EPS-Basic (In thousands)", 2),
        ("weighted_average_shares", "Shares used in computing EPS-Basic (In thousands)", "EPS - Diluted (In Dollar)", 0),
        ("numerator", "Net income", "EPS - Basic (In Dollar)", 0),
    ):
        raw, _ = _between(text, label, end_label)
        values[key] = _cells(raw.replace("$", " "), [decimals] * count)[count // 2:]
        if values[key][0] != values[key][2]:
            fail("quarter_repeated_column_conflict")
    rows = []
    for index in (0, 1, 3):
        y, q = periods[index]
        n, d, e = [Decimal(values[key][index]) for key in ("numerator", "weighted_average_shares", "reported_eps")]
        if d <= Decimal(".5"):
            fail("quarter_share_count_invalid")
        ratios = [(n + dn) / (d + dd) for dn in (Decimal("-.5"), Decimal(".5"))
                  for dd in (Decimal("-.5"), Decimal(".5"))]
        if max(ratios) < e - Decimal(".005") or min(ratios) > e + Decimal(".005"):
            fail("quarter_ratio_inconsistent")
        start, end = _period(y, q)
        rows.append(dict(period_start=start, period_end=end, period_type="quarter", eps_kind="basic",
            statement_scope="consolidated", profit_scope="parent_common_shareholders", unit="TWD_per_share",
            numerator_unit="TWD_thousands", shares_unit="common_shares_thousands", link_status="reconciled",
            **{key: values[key][index] for key in values}, locators={"statement_page": 1, "ntd_column": index + 1}))
    return rows


_CELL = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\)\()?|\(\d[\d,]*\)|-"


def _ledger(text, labels, columns):
    """Consume the complete disclosed block, including zero capital movements."""
    text = " ".join(text.split()).replace("$", "")
    rows = []
    for label in labels:
        pattern = re.escape(" ".join(label.split())) + r"\s*" + r"\s+".join(["(" + _CELL + ")"] * columns)
        match = re.match(pattern, text)
        if not match:
            fail("capital_schedule_format_or_unknown_movement")
        cells = []
        for value in match.groups():
            negative = "(" in value or ")" in value
            digits = value.replace(",", "").replace("(", "").replace(")", "")
            if len(digits) > 16:
                fail("capital_schedule_number_limit")
            cells.append(Decimal(0) if digits == "-" else Decimal(digits) * (-1 if negative else 1))
        rows.append(cells)
        text = text[match.end():].strip()
    if text:
        fail("capital_schedule_unconsumed_content")
    return rows


def _parade_ledger(pages, year, half_year):
    matches = [(i + 1, t) for i, t in enumerate(pages)
               if "Movements in the number of the Company’s ordinary shares outstanding" in t]
    if len(matches) != 1:
        fail("capital_schedule_missing_or_ambiguous")
    page, text = matches[0]
    if "excluding treasury shares" not in text:
        fail("capital_schedule_scope")
    end = "June 30" if half_year else "December 31"
    # In this reviewed PDF layout the heading is extracted after its own table.
    heading = f"For the {'six months' if half_year else 'year'} ended {end}, {year}"
    view = _Text(text)
    limit = view.unique(heading)
    block = text[:view.span(limit.start(), limit.end())[0]]
    block = block[block.index("At January 1"):]
    columns = 3 if half_year else 4
    labels = ["At January 1"]
    if not half_year:
        labels += ["Vesting of restricted stocks", "Cancellation of restricted stocks ordinary shares"]
    labels += ["Purchase of treasury shares", "Treasury stock reissued to employees",
               "Cancellation of treasury stock", f"At {end}"]
    rows = _ledger(block, labels, columns)
    for row in rows:
        if sum(row[:-1]) != row[-1]:
            fail("capital_schedule_row_does_not_reconcile")
    if any(sum(r[i] for r in rows[:-1]) != rows[-1][i] for i in range(columns)):
        fail("capital_schedule_does_not_reconcile")
    return dict(page=page, period_start=f"{year}-01-01", period_end=f"{year}-06-30" if half_year else f"{year}-12-31",
                unit="common_shares_thousands", opening=str(rows[0][-1]), closing=str(rows[-1][-1]),
                movements=[dict(label=label, cells=list(map(str, row))) for label, row in zip(labels, rows)])


def _umc_ledger(pages, year, half_year):
    matches = [(i + 1, t) for i, t in enumerate(pages) if "CONSOLIDATED STATEMENTS OF CHANGES IN EQUITY" in t]
    if len(matches) != 1:
        fail("capital_schedule_missing_or_ambiguous")
    page, text = matches[0]
    if "UNITED MICROELECTRONICS CORPORATION AND SUBSIDIARIES" not in text:
        fail("capital_schedule_company")
    # Strip only the known note-reference column, retaining all financial cells.
    text = re.sub(r"(?:4,\s*)?6\(\d+\)(?:,\s*6\(\d+\))*", "", text)
    start = f"Balance as of January 1, {year}" if half_year else f"Balance as of December 31, {year - 1}"
    end = f"Balance as of {'June 30' if half_year else 'December 31'}, {year}"
    block = text[text.index(start):]
    closing = block.index(end)
    tail = block[closing:]
    closing_line = tail.splitlines()[0]
    block = block[:closing] + closing_line
    # This row is a section heading with no amounts.
    block = block.replace(f"Appropriation and distribution of {year - 1} retained earnings", "")
    period = f"in the first half of {year}" if half_year else f"for the year ended December 31, {year}"
    labels = [start, "Legal reserve", "Cash dividends", f"Net income (loss) {period}",
              f"Other comprehensive income (loss) {period}", "Total comprehensive income (loss)", "Share-based payment transaction"]
    if half_year:
        labels += ["Treasury stock acquired"]
    labels += ["Share of changes in net assets of associates and joint ventures accounted for using equity method",
               "Changes in subsidiaries’ ownership"]
    if half_year:
        labels += ["Disposal of equity instruments investments measured at fair value through other comprehensive income"]
    labels += ["Non-Controlling Interests", "Others", end]
    rows = _ledger(block, labels, 11)
    for label, row in zip(labels[1:-1], rows[1:-1]):
        if label != "Share-based payment transaction" and row[0] != 0:
            fail("unsupported_capital_change")
    if sum(row[0] for row in rows[:-1]) != rows[-1][0]:
        fail("capital_schedule_does_not_reconcile")
    full_text = _compact("\n".join(pages))
    for marker in ("each at a par value of NT$10", "unvested restricted stocks issued for employees", "Earnings per share-basic (NTD)"):
        if _compact(marker) not in full_text:
            fail("capital_note_support_missing")
    return dict(page=page, period_start=f"{year}-01-01", period_end=f"{year}-06-30" if half_year else f"{year}-12-31",
                unit="common_stock_TWD_thousands", opening=str(rows[0][0]), closing=str(rows[-1][0]),
                movements=[dict(label=label, common_stock=str(row[0])) for label, row in zip(labels, rows)])


def verify_basis_pages(symbol, annual_pages, interim_pages):
    parser = _umc_ledger if symbol == "2303.TW" else _parade_ledger if symbol == "4966.TWO" else None
    if parser is None:
        fail("source_format_not_supported")
    ledgers = [parser(annual_pages, 2025, False), parser(interim_pages, 2026, True)]
    if ledgers[0]["closing"] != ledgers[1]["opening"]:
        fail("capital_schedule_continuity_conflict")
    return dict(status="reconciled", coverage_start="2025-01-01", coverage_end="2026-06-30", ledgers=ledgers,
        interpretation="Reviewed disclosures contain employee-share and treasury movements; no proportional restatement in this covered version.",
        scope="content_pinned_disclosures_only_not_exhaustive_corporate_event_monitoring")


def sum_quarters(observations, *, window_end, basis):
    """Pure arithmetic/contract gate; external callers cannot grant source trust."""
    empty = dict(status="insufficient_data", value=None, rows=[], reason=None)
    if basis.get("status") != "reconciled":
        return dict(empty, reason="share_basis_not_verified")
    try:
        end = date.fromisoformat(window_end)
        quarter = (end.month - 1) // 3 + 1
        if window_end != _period(end.year, quarter)[1]:
            return dict(empty, reason="quarter_period_invalid")
        periods = [(end.year, quarter)]
        for _ in range(3):
            periods.append(_previous(*periods[-1]))
        if basis["coverage_start"] > _period(*periods[-1])[0] or basis["coverage_end"] < window_end:
            return dict(empty, reason="share_basis_coverage_incomplete")
        rows = []
        for y, q in reversed(periods):
            start, finish = _period(y, q)
            versions = [r for r in observations if r.get("period_end") == finish]
            if not versions:
                return dict(empty, rows=rows, reason="quarter_missing")
            for row in versions:
                if (row.get("period_start") != start or row.get("period_type") != "quarter"
                    or row.get("eps_kind") != "basic" or row.get("unit") != "TWD_per_share"
                    or row.get("statement_scope") != "consolidated" or row.get("profit_scope") != "parent_common_shareholders"
                    or row.get("link_status") != "reconciled"):
                    return dict(empty, rows=rows, reason="quarter_basis_mismatch")
                if (row.get("numerator_unit"), row.get("shares_unit")) not in {
                    ("TWD_thousands", "common_shares_thousands"), ("TWD_millions", "common_shares")
                }:
                    return dict(empty, rows=rows, reason="quarter_basis_mismatch")
                if not row.get("source_keys") or len(set(row["source_keys"])) != len(row["source_keys"]):
                    return dict(empty, rows=rows, reason="quarter_provenance_missing")
                for key in ("reported_eps", "numerator", "weighted_average_shares"):
                    value = row[key]
                    if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,16}(?:\.\d{1,6})?", value):
                        return dict(empty, rows=rows, reason="quarter_value_invalid")
                if Decimal(row["weighted_average_shares"]) <= 0:
                    return dict(empty, rows=rows, reason="quarter_value_invalid")
            fields = ("reported_eps", "numerator", "weighted_average_shares", "numerator_unit", "shares_unit")
            variants = {tuple(Decimal(r[f]) if f in fields[:3] else r[f] for f in fields) for r in versions}
            keys = [tuple(r["source_keys"]) for r in versions]
            if len(set(keys)) != len(keys):
                return dict(empty, rows=rows, reason="quarter_duplicate_input")
            if len(variants) != 1:
                return dict(empty, rows=rows, reason="quarter_revision_conflict")
            rows.append(dict(period_start=start, period_end=finish, value=versions[0]["reported_eps"], versions=versions))
        return dict(status="available", reason=None, rows=rows, value=str(sum(Decimal(r["value"]) for r in rows)))
    except (KeyError, TypeError, ValueError):
        return dict(empty, reason="quarter_contract_invalid")


def normalize_bundle(symbol, documents, observed_at):
    """No I/O. Documents must have been fetched by the governed collector."""
    catalog = sources_for(symbol)
    if not catalog:
        fail("source_format_not_supported")
    observed_at = normalize_utc_timestamp(observed_at, "observed_at")
    if observed_at[:10] < CATALOG_REVIEW_DATE:
        fail("earnings_observed_before_source_window")
    if set(documents) != {source["key"] for source in catalog}:
        fail("source_bundle_incomplete")
    if sum(len(raw) for raw in documents.values()) > MAX_BUNDLE_BYTES:
        fail("source_bundle_size_limit")
    pages = {}
    sources = []
    for source in catalog:
        raw = documents[source["key"]]
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            fail("source_revision_requires_review")
        pages[source["key"]] = read_reviewed_pdf(raw)
        sources.append(dict(source, bytes=len(raw), observed_at=observed_at, source_published_at=None,
            document_kind="consolidated_financial_report" if source["role"] == "basis" else "issuer_quarterly_release",
            audit_opinion_status="not_asserted"))
    prefix = "umc" if symbol == "2303.TW" else "parade"
    observations = []
    for y, q in ((2025, 3), (2025, 4), (2026, 1), (2026, 2)):
        key = f"{prefix}_{y}q{q}_statements"
        source_keys = [key]
        if symbol == "2303.TW":
            report_key = f"{prefix}_{y}q{q}_report"
            rows = parse_quarter_release_pages(pages[report_key], pages[key], profile=PROFILES[symbol], year=y, quarter=q)
            source_keys.append(report_key)
        else:
            rows = parse_parade_quarter(pages[key], y, q)
        for row in rows:
            row.pop("calculation_eligible", None)
            observations.append(dict(row, source_keys=source_keys, available_at=observed_at))
    basis_keys = [f"{prefix}_2025q4_basis", f"{prefix}_2026q2_basis"]
    basis = verify_basis_pages(symbol, *(pages[key] for key in basis_keys))
    basis["source_keys"] = basis_keys
    result = sum_quarters(observations, window_end="2026-06-30", basis=basis)
    return dict(result, contract_version=CONTRACT, dataset=DATASET, symbol=symbol,
        source="公司原始財報及季度發布資料", official_exchange_source=False,
        catalog_version=CATALOG_VERSION, observed_at=observed_at, available_at=observed_at,
        data_date="2026-06-30", period_start="2025-07-01", period_end="2026-06-30", unit="TWD_per_share",
        method=METHOD, rule_id="FIN-01", rule_version="1.0.0", evidence_level="C", project_operationalization=True,
        official_affiliation=False, historical_eligibility=False, forward_eps_eligible=False,
        basis=basis, sources=sources, limitations=LIMITATIONS)
