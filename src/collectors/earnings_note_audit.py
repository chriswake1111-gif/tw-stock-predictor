"""Offline, narrow-format EPS footnote reconciliation, not a financial import.

Matching a PDF note to inline XBRL establishes a local evidence link only. It
does not authenticate either file, select a restatement, prove a common share
basis across filings, or authorize a fourth-quarter/TTM calculation.
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
import calendar
import hashlib
import io
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from src.collectors.earnings_source_audit import (
    EarningsSourceAuditError, _period,
)
from src.collectors.earnings_revision_audit import (
    REVISION_VERSION, extract_stock_dividend_evidence, explain_basic_revision,
)
from src.domain.valuation import normalize_utc_timestamp


NOTE_VERSION = "earnings-note-reconciliation-v2"
MAX_PDF_BYTES = 4 * 1024 * 1024
MAX_PAGES = 120
MAX_TEXT_CHARS = 1_000_000


@dataclass(frozen=True)
class NoteProfile:
    layout: str
    company: str
    scope: str
    host: str
    path_prefix: str


# These are parser coverage declarations, not trusted-file hashes or approvals.
PROFILES = {
    "3491.TWO": NoteProfile("zh_reconciliation", "昇達科技股份有限公司及子公司",
                           "consolidated", "www.umt-tw.com", "/upload/Finance/"),
    "2330.TW": NoteProfile("en_reconciliation", "Taiwan Semiconductor Manufacturing Company Limited and Subsidiaries",
                          "consolidated", "investor.tsmc.com", "/sites/ir/financial-report/"),
    "3081.TWO": NoteProfile("zh_restated_table", "聯亞光電工業股份有限公司",
                           "separate", "www.lmoc.com.tw", "/index.php"),
}


def _fail(reason):
    raise EarningsSourceAuditError(reason)


def _compact(value):
    return "".join(value.split())


class _Text:
    """Whitespace-insensitive labels with offsets into the original page text."""
    def __init__(self, raw):
        self.raw = raw
        self.offsets = [i for i, char in enumerate(raw) if not char.isspace()]
        self.compact = "".join(raw[i] for i in self.offsets)

    def span(self, start, end):
        return self.offsets[start], self.offsets[end - 1] + 1

    def unique(self, label):
        matches = list(re.finditer(re.escape(_compact(label)), self.compact))
        if len(matches) != 1:
            _fail("note_label_missing_or_ambiguous")
        return matches[0]

    def row(self, label, count, *, decimals=0):
        match = self.unique(label)
        start, pos = self.span(match.start(), match.end())
        number = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
        token = re.compile(r"\s*\$?\s*(\(\s*" + number + r"\s*\)|-?" + number + r")")
        values = []
        for _ in range(count):
            found = token.match(self.raw, pos)
            if not found:
                _fail("note_numeric_cell_missing")
            if found.end() < len(self.raw) and not self.raw[found.end()].isspace():
                _fail("note_numeric_cell_boundary_invalid")
            text = re.sub(r"[\s,]", "", found[1])
            if text.startswith("("):
                text = "-" + text[1:-1]
            if not re.fullmatch(r"-?\d{1,13}" + (rf"\.\d{{{decimals}}}" if decimals else ""), text):
                _fail("note_numeric_precision_unsupported")
            values.append(text)
            pos = found.end()
        # Never truncate an unexpected extra column or a malformed numeric cell.
        remaining = self.raw[pos:].lstrip()
        if token.match(remaining) or (remaining and remaining[0] in "0123456789$.,"):
            _fail("note_extra_numeric_column")
        return values, (start, pos)


def read_pdf_pages(raw):
    """Optional development dependency; no OCR, network, scripts or file writes."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_PDF_BYTES or not raw.startswith(b"%PDF-"):
        _fail("note_pdf_size_or_format_invalid")
    try:
        from pypdf import PdfReader
    except ImportError:
        _fail("note_pdf_dependency_missing_install_development_requirements")
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
        # Public PDFs with an empty user password are readable without supplying
        # credentials. Password-protected documents fail at page access below.
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            _fail("note_page_limit")
        pages, size = [], 0
        for page in reader.pages:
            text = page.extract_text()
            if not isinstance(text, str) or len(text) > 50_000:
                _fail("note_page_text_limit")
            size += len(text)
            if size > MAX_TEXT_CHARS:
                _fail("note_document_text_limit")
            pages.append(text)
        return pages
    except EarningsSourceAuditError:
        raise
    except Exception as exc:
        raise EarningsSourceAuditError("note_pdf_unreadable") from exc


def _header_periods(header, layout):
    text = _compact(header)
    if layout == "en_reconciliation":
        # Deliberately limited to the observed interim English table format.
        match = re.fullmatch(r"ThreeMonthsEndedJune30SixMonthsEndedJune30(20\d\d)(20\d\d)(20\d\d)(20\d\d)", text)
        if not match:
            _fail("note_english_period_layout_unsupported")
        return [(f"{y}-{month}-01", f"{y}-06-30") for y, month in zip(match.groups(), ["04", "04", "01", "01"])]
    pattern = (r"(\d{3})年(\d{1,2})月1日至(\d{1,2})月(\d{1,2})日"
               if layout == "zh_reconciliation" else r"(\d{3})年(\d{1,2})月至(\d{1,2})月")
    if layout == "zh_reconciliation" and re.fullmatch(r"(?:\d{3}年度){2}", text):
        return [(f"{int(y) + 1911}-01-01", f"{int(y) + 1911}-12-31") for y in re.findall(r"(\d{3})年度", text)]
    matches = list(re.finditer(pattern, text))
    if "".join(m[0] for m in matches) != text or len(matches) not in (2, 4):
        _fail("note_chinese_period_layout_unsupported")
    periods = []
    for match in matches:
        year, start_month, end_month = (int(match[i]) for i in (1, 2, 3))
        year += 1911
        try:
            end_day = int(match[4]) if layout == "zh_reconciliation" else calendar.monthrange(year, end_month)[1]
            periods.append((date(year, start_month, 1).isoformat(), date(year, end_month, end_day).isoformat()))
        except (ValueError, IndexError):
            _fail("note_period_invalid")
    return periods


def _ratio_consistency(numerator, shares, eps):
    """Overlap of displayed rounding intervals, not an independently computed EPS."""
    with localcontext() as context:
        context.prec = 50
        n, d, e = map(Decimal, (numerator, shares, eps))
        if d <= Decimal("0.5"):
            _fail("note_nonpositive_weighted_shares")
        quotients = [(n + a) / (d + b) for a in [Decimal("-.5"), Decimal(".5")]
                     for b in [Decimal("-.5"), Decimal(".5")]]
        return max(quotients) >= e - Decimal(".005") and min(quotients) <= e + Decimal(".005")


def parse_note_pages(pages, *, profile, year, quarter):
    """Internal text parser. Its output is never accepted as an import receipt."""
    if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES
            or any(not isinstance(p, str) or len(p) > 50_000 for p in pages)
            or sum(map(len, pages)) > MAX_TEXT_CHARS):
        _fail("note_text_limit")
    cover = _compact(pages[0])
    if _compact(profile.company) not in cover:
        _fail("note_company_mismatch")
    if profile.scope == "consolidated":
        if "合併財務報告" not in cover and "ConsolidatedFinancialStatements" not in cover:
            _fail("note_statement_scope_mismatch")
    elif "合併" in cover or "Consolidated" in cover or "財務報告暨會計師" not in cover:
        _fail("note_statement_scope_mismatch")
    raw = "\n".join(pages)
    starts, offset = [], 0
    for page in pages:
        starts.append(offset)
        offset += len(page) + 1
    document = _Text(raw)
    pattern = {"zh_reconciliation": r"[一二三四五六七八九十]{1,5}、每股盈餘",
               "zh_restated_table": r"\([一二三四五六七八九十]{1,5}\)每股盈餘",
               "en_reconciliation": r"\d{1,2}\.EARNINGSPERSHARE"}[profile.layout]
    matches = list(re.finditer(pattern, document.compact))
    if len(matches) != 1:
        _fail("note_section_missing_or_ambiguous")
    note_start = document.span(matches[0].start(), matches[0].end())[1]
    first_page = bisect_right(starts, note_start) - 1
    # At most three consecutive pages; never borrow a row from another note.
    end = starts[first_page + 3] if first_page + 3 < len(starts) else len(raw)
    section_raw = raw[note_start:end]
    section_view = _Text(section_raw)
    stop_pattern = {"zh_reconciliation": r"[一二三四五六七八九十]{1,5}、",
                    "zh_restated_table": r"\([一二三四五六七八九十]{1,5}\)",
                    "en_reconciliation": r"\d{1,2}\.[A-Z]{3}"}[profile.layout]
    stop = re.search(stop_pattern, section_view.compact)
    if stop:
        section_raw = section_raw[:section_view.offsets[stop.start()]]
    view = _Text(section_raw)
    if any(word in view.compact.lower() for word in ["停業", "discontinued", "特別股", "preferred"]):
        _fail("note_profit_or_share_class_unsupported")
    if any(word in view.compact.lower() for word in ["美元", "美金", "人民幣", "usd", "cny", "us$"]):
        _fail("note_currency_override_unsupported")

    def locator(span):
        start, end = (note_start + x for x in span)
        return dict(page=bisect_right(starts, start), end_page=bisect_right(starts, end - 1),
                    text_start=start - starts[bisect_right(starts, start) - 1],
                    text_end=end - starts[bisect_right(starts, end - 1) - 1])

    # Units must appear in the report's notes heading, not an unrelated table.
    unit_label = {"zh_reconciliation": "（除另註明外，金額以新台幣仟元為單位）",
                  "zh_restated_table": "(除另有註明者外，所有金額均以新台幣千元為單位)",
                  "en_reconciliation": "(Amounts in Thousands of New Taiwan Dollars, Unless Specified Otherwise)"}[profile.layout]
    unit_matches = []
    for match in re.finditer(re.escape(_compact(unit_label)), document.compact):
        page = bisect_right(starts, document.offsets[match.start()])
        if page <= min(first_page + 1, 20) and any(title in _compact(pages[page - 1]) for title in
                ["合併財務報告附註", "財務報告附註", "NOTESTOCONSOLIDATEDFINANCIALSTATEMENTS"]):
            unit_matches.append(match)
    if len(unit_matches) != 1:
        _fail("note_currency_unit_missing_or_ambiguous")
    unit_match = unit_matches[0]
    unit_span = document.span(unit_match.start(), unit_match.end())
    unit_page = bisect_right(starts, unit_span[0])
    if not 1 <= unit_page <= min(first_page + 1, 20):
        _fail("note_currency_unit_location_unsupported")

    if profile.layout == "zh_reconciliation":
        header = re.search(r"單位：每股元(.+?)基本每股盈餘", view.compact)
    elif profile.layout == "zh_restated_table":
        header = re.search(r"股數單位：千股(.+?)基本每股盈餘：", view.compact)
    else:
        header = re.search(r"^(.+?)BasicEPS", view.compact)
    if not header:
        _fail("note_period_header_missing")
    periods = _header_periods(header[1], profile.layout)
    month = quarter * 3
    report_end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    expected = ([(f"{y}-01-01", f"{y}-12-31") for y in (year, year - 1)] if quarter == 4 else
                [(f"{y}-{(quarter - 1) * 3 + 1:02}-01", f"{y}-{month:02}-{calendar.monthrange(y, month)[1]}") for y in (year, year - 1)])
    if quarter in (2, 3):
        expected += [(f"{y}-01-01", f"{y}-{month:02}-{calendar.monthrange(y, month)[1]}") for y in (year, year - 1)]
    if periods != expected:
        _fail("note_report_period_or_column_order_mismatch")
    count = len(periods)
    header_locator = locator(view.span(header.start(1), header.end(1)))
    rows = []
    for kind, chinese, english in [("basic", "基本", "Basic"), ("diluted", "稀釋", "Diluted")]:
        row_view, base = view, 0
        kind_periods, kind_header_locator = periods, header_locator
        if profile.layout == "zh_reconciliation":
            eps_label = f"{chinese}每股盈餘"
            numerator_label = f"用以計算{chinese}每股盈餘之" + ("淨利" if f"用以計算{chinese}每股盈餘之淨利" in view.compact else "盈餘")
            # The standalone EPS row must not match the numerator/denominator labels.
            eps_end = view.compact.index("用以計算每股盈餘之盈餘及普通股加權平均股數如下")
            eps_view = _Text(section_raw[:view.offsets[eps_end]])
            eps, eps_span = eps_view.row(eps_label, count, decimals=2)
            numerator, n_span = view.row(numerator_label, count)
            denominator, d_span = view.row(f"用以計算{chinese}每股盈餘之普通股加權平均股數", count)
            if "歸屬於本公司業主之淨利" not in view.compact or "單位：仟股" not in view.compact:
                _fail("note_numerator_or_share_unit_missing")
            # Repeated column headings must agree, including on a following page.
            if view.compact.count(header[1]) != 3:
                _fail("note_repeated_period_headers_mismatch")
            restated = "not_stated_in_eps_note"
        else:
            # Basic and diluted sections may repeat labels; isolate first.
            if profile.layout == "zh_restated_table":
                start_label, end_label = f"{chinese}每股盈餘：", "稀釋每股盈餘：" if kind == "basic" else None
            else:
                # First Basic/Diluted rows are a summary, not the reconciliation.
                computation = view.compact.index("EPSiscomputedasfollows:") if "EPSiscomputedasfollows:" in view.compact else -1
                if computation < 0:
                    _fail("note_reconciliation_heading_missing")
                start_label, end_label = f"{english}EPS", "DilutedEPS" if kind == "basic" else None
                if view.compact.count(header[1]) != 2:
                    _fail("note_repeated_period_headers_mismatch")
            search_start = computation if profile.layout == "en_reconciliation" else 0
            start = view.compact.find(start_label, search_start)
            if start < 0:
                _fail("note_kind_section_missing")
            base = view.span(start, start + len(start_label))[1]
            end = view.compact.find(end_label, start + len(start_label)) if end_label else -1
            block_end = view.offsets[end] if end >= 0 else len(section_raw)
            if profile.layout == "zh_restated_table" and end >= 0:
                next_header = re.search(r"((?:\d{3}年\d{1,2}月至\d{1,2}月){2,4})$", view.compact[:end])
                if next_header:
                    block_end = view.offsets[next_header.start(1)]
            row_view = _Text(section_raw[base:block_end])
            if profile.layout == "zh_restated_table":
                # A loss-year comparative may be omitted from the diluted table.
                # Accept only the periods explicitly repeated over that table.
                repeated = re.search(r"((?:\d{3}年\d{1,2}月至\d{1,2}月){2,4})$", view.compact[:start])
                if repeated:
                    kind_periods = _header_periods(repeated[1], profile.layout)
                    if (len(set(kind_periods)) != len(kind_periods)
                            or [p for p in periods if p in kind_periods] != kind_periods):
                        _fail("note_kind_periods_mismatch")
                    kind_header_locator = locator(view.span(repeated.start(1), repeated.end(1)))
                elif locator((base, base + 1))["page"] != header_locator["end_page"]:
                    _fail("note_kind_period_header_missing")
                numerator_label = "歸屬於本公司普通股權益持有人之淨利"
                if kind == "diluted":
                    numerator_label += "(調整稀釋性潛在普通股影響數後)"
                elif numerator_label + "(損)" in row_view.compact:
                    numerator_label += "(損)"
                restated = "explicitly_restated" if "普通股加權平均流通在外股數(追溯調整)" in row_view.compact else "not_stated_in_eps_note"
                denominator_label = (("普通股加權平均流通在外股數" + ("(追溯調整)" if restated == "explicitly_restated" else "")) if kind == "basic" else "計算稀釋每股盈餘之加權平均流通在外股數")
                eps_label = f"{chinese}每股盈餘(單位：新台幣元)"
            else:
                numerator_label = "Net income available to common shareholders of the parent"
                denominator_label = ("Weighted average number of common shares outstanding used in the computation of basic EPS (in thousands)" if kind == "basic" else "Weighted average number of common shares used in the computation of diluted EPS (in thousands)")
                eps_label = f"{english} EPS (in dollars)"
                restated = "not_stated_in_eps_note"
            eps, eps_span = row_view.row(eps_label, len(kind_periods), decimals=2)
            numerator, n_span = row_view.row(numerator_label, len(kind_periods))
            denominator, d_span = row_view.row(denominator_label, len(kind_periods))
            if profile.layout == "en_reconciliation":
                summary = _Text(section_raw[:view.offsets[computation]])
                summary_eps, _ = summary.row(f"{english}EPS", count, decimals=2)
                if list(map(Decimal, summary_eps)) != list(map(Decimal, eps)):
                    _fail("note_internal_eps_conflict")
        for column, ((start, end), n, d, e) in enumerate(zip(kind_periods, numerator, denominator, eps), 1):
            rows.append(dict(period_start=start, period_end=end, period_type=_period(start, end),
                eps_kind=kind, profit_scope="total", statement_scope=profile.scope,
                reported_eps=e, unit="TWD_per_share", numerator=n, numerator_unit="TWD_thousands",
                weighted_average_shares=d, shares_unit="thousand_common_shares", restatement_disclosure=restated,
                arithmetic_check="compatible_with_display_rounding" if _ratio_consistency(n, d, e) else "inconsistent",
                locators=dict(column=column, header=kind_header_locator, currency_unit_page=unit_page,
                    eps=locator(tuple(x + base for x in eps_span)),
                    numerator=locator(tuple(x + base for x in n_span)),
                    weighted_average_shares=locator(tuple(x + base for x in d_span)))))
    return dict(rows=rows, report_period_end=report_end)


def audit_earnings_note(raw, *, filing, source_url, observed_at):
    profile = PROFILES.get(filing["symbol"])
    if profile is None:
        _fail("note_company_profile_not_supported")
    url = urlsplit(source_url)
    if (url.scheme != "https" or url.netloc != profile.host or not url.path.startswith(profile.path_prefix)
            or url.fragment or url.username or url.password):
        _fail("note_source_url_not_supported")
    if filing["statement_scope"] != profile.scope:
        _fail("note_filing_scope_mismatch")
    observed_at = normalize_utc_timestamp(observed_at, "observed_at")
    # No report publication time is inferred from a URL, signature or quarter end.
    if datetime.fromisoformat(observed_at).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat() < filing["report_period_end"]:
        _fail("note_observed_before_period_end")
    pages = read_pdf_pages(raw)
    if profile.layout != "en_reconciliation" and f"股票代碼：{filing['symbol'].split('.')[0]}" not in _compact(pages[0]):
        _fail("note_stock_code_mismatch")
    parsed = parse_note_pages(pages, profile=profile,
                              year=filing["report_year"], quarter=filing["report_quarter"])
    available_at = max(observed_at, filing["observed_at"])
    note_sha256 = hashlib.sha256(raw).hexdigest()
    share_events = (extract_stock_dividend_evidence(pages) if profile.layout == "zh_restated_table"
                   else dict(contract=REVISION_VERSION, events=[], coverage="not_established",
                             reason="share_event_note_format_not_supported"))
    for event in share_events["events"]:
        event.update(symbol=filing["symbol"], statement_scope=profile.scope,
                     note_sha256=note_sha256, filing_sha256=filing["raw_sha256"],
                     source_url=source_url, available_at=available_at)
    key = lambda row: (row["period_start"], row["period_end"], row["eps_kind"], row["profit_scope"])
    facts = {key(row): row for row in filing["rows"]}
    reasons = set()
    parsed_keys = {key(row) for row in parsed["rows"]}
    omitted = [dict(period_start=row["period_start"], period_end=row["period_end"], eps_kind=row["eps_kind"],
                    reason="no_supported_note_row") for row in filing["rows"]
               if row["profit_scope"] == "total" and key(row) not in parsed_keys]
    for row in parsed["rows"]:
        fact = facts.get(key(row))
        state = "matched"
        if fact is None or fact["value"] is None:
            state = "structured_fact_missing"
        elif Decimal(fact["value"]) != Decimal(row["reported_eps"]):
            state = "structured_value_mismatch"
        elif row["arithmetic_check"] != "compatible_with_display_rounding":
            state = "note_arithmetic_mismatch"
        row.update(link_status=state, structured_locators=fact["locators"] if fact else [],
                   structured_value=fact["value"] if fact else None, available_at=available_at)
        if state != "matched":
            reasons.add(state)
    return dict(contract=NOTE_VERSION, symbol=filing["symbol"], report_year=filing["report_year"],
        report_quarter=filing["report_quarter"], statement_scope=profile.scope,
        source_url=source_url, raw_sha256=note_sha256, raw_bytes=len(raw),
        filing_sha256=filing["raw_sha256"], filing_source_url=filing["source_url"],
        observed_at=observed_at, available_at=available_at, source_published_at=None,
        provenance_status="local_file_and_caller_receipt_not_authenticated",
        status="linked" if not reasons else "quality_warning", reasons=sorted(reasons),
        structured_rows_without_notes=omitted, share_event_evidence=share_events,
        calculation_eligible=False, historical_eligibility="not_asserted", value=None, **parsed)


def compare_earnings_notes(audits):
    """Classify source differences and Q4 gaps without choosing a winning value."""
    groups = {}
    for audit in audits:
        groups.setdefault((audit["symbol"], audit["statement_scope"]), []).append(audit)
    results = []
    for (symbol, scope), documents in sorted(groups.items()):
        periods, q4 = {}, []
        for document in documents:
            for row in document["rows"]:
                if row["eps_kind"] != "basic" or row["link_status"] != "matched":
                    continue
                entry = dict(row, note_sha256=document["raw_sha256"], filing_sha256=document["filing_sha256"],
                             report_period_end=document.get("report_period_end"))
                periods.setdefault((row["period_start"], row["period_end"]), []).append(entry)
        revisions = []
        for (start, end), entries in sorted(periods.items()):
            variants = {(Decimal(e["reported_eps"]), Decimal(e["numerator"]), Decimal(e["weighted_average_shares"])) for e in entries}
            if len(variants) > 1:
                links = _revision_links(entries, documents)
                reason = ("disclosed_share_event_matches_some_versions_selection_unresolved"
                          if any(link["status"] == "consistent" for link in links)
                          else "reported_restatement_requires_revision_link"
                          if any(e["restatement_disclosure"] == "explicitly_restated" for e in entries)
                          else "source_versions_differ")
                revisions.append(dict(period_start=start, period_end=end, selected_version=None, revision_links=links,
                    reason=reason, versions=entries))
            if start.endswith("-01-01") and end.endswith("-12-31"):
                year = start[:4]
                previous = periods.get((start, f"{year}-09-30"), [])
                direct = periods.get((f"{year}-10-01", end), [])
                if direct:
                    reason = "direct_quarter_present_cross_filing_basis_still_required"
                elif not previous:
                    reason = "nine_month_note_missing"
                elif any(Decimal(a["weighted_average_shares"]) != Decimal(b["weighted_average_shares"]) for a in entries for b in previous):
                    reason = "annual_and_ytd_weighted_shares_differ"
                else:
                    reason = "equal_rounded_denominators_do_not_prove_quarter_basis"
                q4.append(dict(year=int(year), status="not_derived", value=None, reason=reason,
                               annual_versions=entries, nine_month_versions=previous, direct_versions=direct))
        results.append(dict(symbol=symbol, statement_scope=scope, revisions=revisions, fourth_quarter_checks=q4,
                            value=None, calculation_eligible=False,
                            remaining_gate="cross_filing_share_basis_and_revision_chain_not_verified"))
    return results


def _revision_links(entries, documents):
    """Explain individual comparisons, never choose a report or certify a chain."""
    links = []
    for new in entries:
        sources = [doc for doc in documents if doc["raw_sha256"] == new["note_sha256"]
                   and doc["filing_sha256"] == new["filing_sha256"]]
        if len(sources) != 1:
            continue
        document = sources[0]
        events = document.get("share_event_evidence", {}).get("events", [])
        # This version supports exactly one disclosed event, not a guessed
        # product of multiple actions or automatic choice among conflicting ones.
        if len(events) != 1:
            continue
        event = events[0]
        reference = {key: document[key] for key in ("symbol", "statement_scope", "filing_sha256")}
        reference["note_sha256"] = document["raw_sha256"]
        if any(event.get(key) != value for key, value in reference.items()):
            continue
        report_end = document.get("report_period_end")
        if (not report_end or not event["resolution_date"] <= event["authorization_date"] <= report_end < event["stated_effective_date"]
                or not document.get("available_at") or event["available_at"] != document["available_at"]):
            continue
        for old in entries:
            if (old["note_sha256"] == new["note_sha256"] or not old.get("report_period_end")
                    or not old["report_period_end"] < report_end
                    or not old["period_end"] <= old["report_period_end"] < event["resolution_date"]):
                continue
            reason = explain_basic_revision(old, new, event)
            links.append(dict(contract=REVISION_VERSION, reason=reason,
                status="consistent" if reason == "consistent_with_disclosed_stock_dividend" else "unresolved",
                inference="disclosed_event_and_numeric_consistency_not_explicit_version_replacement",
                old_note_sha256=old["note_sha256"], old_filing_sha256=old["filing_sha256"],
                new_note_sha256=new["note_sha256"], new_filing_sha256=new["filing_sha256"],
                event=event, available_at=max(old["available_at"], new["available_at"], event["available_at"]),
                selected_version=None, cross_quarter_basis_verified=False))
    return sorted(links, key=lambda link: (link["old_note_sha256"], link["new_note_sha256"]))
