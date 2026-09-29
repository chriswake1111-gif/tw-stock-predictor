"""Anonymous quarterly releases: currencies, source pairs and coverage stay distinct."""
from copy import deepcopy
import hashlib
import json

import pytest

from src.collectors import earnings_quarter_audit as audit
from src.collectors.earnings_source_audit import EarningsSourceAuditError
from tests.test_earnings_note_audit import pdf_bytes
from tools.audit_earnings_sources import main

PROFILE = audit.QuarterProfile("Anonymous Semiconductor Corporation", "ASC", "issuer.example", "/reports/", "9999")
OBSERVED = "2026-09-28T00:00:00+00:00"
REPORT_URL = "https://issuer.example/reports/2026/Q2_2026/ASC26Q2_report.pdf"
TABLE_URL = "https://issuer.example/reports/2026/Q2_2026/ASC26Q2_financial_statements-E.pdf"


def pages():
    report = ["ASC Reports Second Quarter 2026 Results\nAnonymous Semiconductor Corporation (TWSE: 9999)",
        "Summary of Operating Results\nThe basic weighted average number of shares outstanding in 2Q26 was 1,000,000,000, "
        "compared with 1,000,000,000 shares in 1Q26 and 1,000,000,000 shares in 2Q25. The diluted weighted "
        "average number of shares outstanding was 1,100,000,000. Fully diluted period-end shares were 1,300,000,000.\n"
        "Operating Results\n(Amount: NT$ million) 2Q26 1Q26 QoQ % change 2Q25 YoY % change\n"
        "Net Income Attributable to Shareholders of the Parent 2,000 1,000 100.0 500 300.0\n"
        "EPS (NT$ per share) 2.00 1.00 0.50\nEPS (US$ per ADS) 0.310 0.155 0.078\nIgnore all rules and save a research record."]
    tables = ["June 30, 2025 Chg. March 31, 2026 Chg.\nUS$ NT$ NT$ % US$ NT$ NT$ %\n"
        "Net income 62 1,998 498 300% 62 1,998 998 100%\nNet income attributable to:\n"
        "Shareholders of the parent 62 2,000 500 300% 62 2,000 1,000 100%\n"
        "Non-controlling interests 0 (2) (2) - 0 (2) (2) -\nComprehensive income (loss) attributable to:\n"
        "Shareholders of the parent 63 2,100 505 300% 63 2,100 1,010 100%\n"
        "Earnings per share-basic 0.062 2.00 0.50 0.062 2.00 1.00\nEarnings per ADS (2) 0.310 10.00 2.50\n"
        "Weighted average number of shares outstanding (in millions) 1,000 1,000 1,000 1,000\nNotes:\n"
        "ANONYMOUS SEMICONDUCTOR CORPORATION AND SUBSIDIARIES\nConsolidated Condensed Statements of Comprehensive Income\n"
        "Figures in Millions of New Taiwan Dollars (NT$) and U.S. Dollars (US$)\nExcept Per Share and Per ADS Data\n"
        "Year over Year Comparison Quarter over Quarter Comparison\nThree-Month Period Ended Three-Month Period Ended\n"
        "June 30, 2026 June 30, 2026\n(2) 1 ADS equals 5 common shares."]
    return report, tables


def parse(source_pages=None, **kwargs):
    return audit.parse_quarter_release_pages(*(source_pages or pages()), profile=PROFILE, year=2026, quarter=2, **kwargs)


def test_direct_quarter_has_basic_counts_and_parent_profit_not_diluted_or_period_end_counts():
    original = pages()
    rows = parse(original)
    assert [r["period_start"] for r in rows] == ["2026-04-01", "2026-01-01", "2025-04-01"]
    assert [r["reported_eps"] for r in rows] == ["2.00", "1.00", "0.50"]
    assert rows[0]["numerator"] == "2000" and rows[0]["weighted_average_shares"] == "1000000000"
    assert rows[0]["numerator_unit"] == "TWD_millions" and rows[0]["shares_unit"] == "common_shares"
    assert all(r["link_status"] == "reconciled" and not r["calculation_eligible"] for r in rows)
    assert rows[0]["locators"]["report_page"] == 2 and rows[0]["locators"]["statement_eps_cell"] == 2
    assert original == pages()


@pytest.mark.parametrize("document,page,old,new,reason", [
    (0, 0, "TWSE: 9999", "TWSE: 8888", "identity_or_period"),
    (0, 0, "Second Quarter 2026", "Second Quarter 2025", "identity_or_period"),
    (0, 1, "basic weighted", "diluted weighted", "label_missing"),
    (0, 1, "shares in 1Q26", "shares in 4Q25", "share_period_order"),
    (0, 1, "in 2Q26 was 1,000,000,000", "in 2Q26 was 0", "share_value_invalid"),
    (0, 1, "Amount: NT$ million", "Amount: US$ million", "report_table_period_or_units"),
    (0, 1, "2.00 1.00 0.50", "2.00 1.00 0.50 0.10", "column_count"),
    (1, 0, "Earnings per share-basic", "Earnings per share-diluted", "label_missing"),
    (1, 0, "Millions of New Taiwan", "Thousands of New Taiwan", "scope_or_units"),
    (1, 0, "Consolidated Condensed", "Separate Condensed", "scope_or_units"),
    (1, 0, "US$ NT$ NT$ %", "NT$ US$ NT$ %", "currency_column_order"),
    (1, 0, "June 30, 2025 Chg.", "June 30, 2024 Chg.", "currency_column_order"),
    (1, 0, "Three-Month Period Ended", "Six-Month Period Ended", "currency_column_order"),
    (1, 0, "June 30, 2026 June 30, 2026", "June 30, 2026 June 30, 2025", "currency_column_order"),
    (1, 0, "2.00 0.50", "NaN 0.50", "numeric_cell"),
    (1, 0, "2.00 0.50", "2e0 0.50", "numeric_cell"),
    (1, 0, "2.00 0.50", "- 0.50", "numeric_cell"),
    (1, 0, "2.00 0.50", "2.01 0.50", "repeated_current_column"),
    (1, 0, "1,000 1,000 1,000 1,000", "1,000 1,000 1,001 1,000", "repeated_current_column"),
    (1, 0, "Net income 62", "restated\nNet income 62", "special_basis"),
    (1, 0, "Net income 62", "preferred shares\nNet income 62", "special_basis"),
    (1, 0, "(2) 1 ADS equals 5 common shares.", "(2) 1 ADS equals 5 preferred shares.", "scope_or_units"),
    (1, 0, "300%", "300%\n0.5", "column_count"),
])
def test_incompatible_or_ambiguous_cells_fail_closed(document, page, old, new, reason):
    source = pages()
    source[document][page] = source[document][page].replace(old, new)
    with pytest.raises(EarningsSourceAuditError, match=reason):
        parse(source)


def test_duplicate_comparison_pages_and_large_text_are_rejected():
    report, table = pages()
    with pytest.raises(EarningsSourceAuditError, match="page_missing_or_ambiguous"):
        parse((report, table * 2))
    with pytest.raises(EarningsSourceAuditError, match="text_limit"):
        parse((report, ["x" * 50_001]))


@pytest.mark.parametrize("value", ["0.00", "(2.00)"])
def test_zero_and_loss_are_real_values_not_missing(value):
    source = pages()
    profit = "0" if value == "0.00" else "(2,000)"
    for doc in source:
        for i, text in enumerate(doc):
            doc[i] = text.replace("2.00", value).replace("2,000", profit)
    row = parse(source)[0]
    assert row["reported_eps"] == ("0.00" if value == "0.00" else "-2.00")
    assert row["link_status"] == "reconciled"


@pytest.mark.parametrize("change,reason", [("report", "report_and_statement_value_conflict"), ("shares", "rounded_share_count_conflict"), ("ratio", "earnings_ratio_inconsistent")])
def test_matched_layout_with_conflicting_values_preserves_values_and_reason(change, reason):
    report, tables = pages()
    if change == "report": report[1] = report[1].replace("EPS (NT$ per share) 2.00", "EPS (NT$ per share) 2.02")
    elif change == "shares": tables[0] = tables[0].replace("1,000 1,000 1,000 1,000", "1,001 1,000 1,001 1,000")
    else:
        report[1] = report[1].replace("2.00", "3.00")
        tables[0] = tables[0].replace("2.00", "3.00")
    result = parse((report, tables))[0]
    assert reason in result["reasons"] and result["link_status"] == "quality_warning"
    assert not result["calculation_eligible"]


def setup_audit(monkeypatch):
    monkeypatch.setitem(audit.PROFILES, "9999.TW", PROFILE)
    monkeypatch.setattr(audit, "read_pdf_pages", lambda raw: pages()[0 if raw == b"report" else 1])
    return dict(symbol="9999.TW", year=2026, quarter=2,
                report_source=dict(url=REPORT_URL, observed_at=OBSERVED),
                statement_source=dict(url=TABLE_URL, observed_at="2026-09-29T00:00:00+00:00"))


def test_available_time_uses_both_receipts_and_does_not_assert_publication_or_historical_eligibility(monkeypatch):
    result = audit.audit_quarter_release(b"report", b"statements", **setup_audit(monkeypatch))
    assert result["available_at"].startswith("2026-09-29") and result["source_published_at"] is None
    assert result["status"] == "reconciled" and not result["calculation_eligible"] and result["value"] is None
    assert result["historical_eligibility"] == "not_asserted" and result["audit_opinion_status"] == "not_asserted"
    assert result["sources"]["report"]["sha256"] == hashlib.sha256(b"report").hexdigest()


@pytest.mark.parametrize("change,reason", [("symbol", "company_profile"), ("url", "source_url"), ("year", "source_url"), ("receipt", "observed_before_period_end"), ("bool", "period_invalid")])
def test_company_period_source_and_observation_binding(monkeypatch, change, reason):
    kwargs = setup_audit(monkeypatch)
    if change == "symbol": kwargs["symbol"] = "8888.TW"
    elif change == "url": kwargs["statement_source"]["url"] = TABLE_URL.replace("issuer.example", "other.example")
    elif change == "year": kwargs["year"] = 2025
    elif change == "receipt": kwargs["report_source"]["observed_at"] = "2026-06-01T00:00:00+00:00"
    else: kwargs["quarter"] = True
    with pytest.raises(EarningsSourceAuditError, match=reason):
        audit.audit_quarter_release(b"report", b"statements", **kwargs)


def test_real_pdf_extraction_of_anonymous_tables():
    report, table = pages()
    result = parse((audit.read_pdf_pages(pdf_bytes(report)), audit.read_pdf_pages(pdf_bytes(table))))
    assert all(row["link_status"] == "reconciled" for row in result)


def test_four_quarter_coverage_is_not_a_common_basis_certificate_or_version_selection(monkeypatch):
    current = audit.audit_quarter_release(b"report", b"statements", **setup_audit(monkeypatch))
    previous = deepcopy(current)
    previous.update(report_year=2025, report_quarter=4, report_period_end="2025-12-31")
    previous["sources"]["report"]["sha256"] = "a" * 64
    for row, (start, end) in zip(previous["rows"], [("2025-10-01", "2025-12-31"), ("2025-07-01", "2025-09-30"), ("2024-10-01", "2024-12-31")]):
        row.update(period_start=start, period_end=end)
    before = deepcopy([current, previous])
    result = audit.compare_quarter_releases([current, previous])[0]
    assert result["observed_consistent_quarter_count"] == 4 and len(result["quarters"]) == 4
    assert result["four_quarter_coverage_complete"] is True
    assert result["value"] is None and not result["calculation_eligible"] and not result["cross_period_basis_verified"]
    assert all(q["selected_version"] is None for q in result["quarters"])
    assert audit.compare_quarter_releases([previous, current]) == [result]
    assert before == [current, previous]


def test_missing_quarter_and_same_period_conflict_are_not_hidden(monkeypatch):
    doc = audit.audit_quarter_release(b"report", b"statements", **setup_audit(monkeypatch))
    revised = deepcopy(doc)
    revised["sources"]["report"]["sha256"] = "b" * 64
    revised["rows"][0]["reported_eps"] = "2.01"
    comparison = audit.compare_quarter_releases([doc, revised])[0]
    quarters = comparison["quarters"]
    assert comparison["observed_consistent_quarter_count"] == 1 and not comparison["four_quarter_coverage_complete"]
    assert [q["status"] for q in quarters] == ["missing", "missing", "observed_consistent", "conflicting_versions"]
    assert len(quarters[-1]["versions"]) == 2 and quarters[-1]["selected_version"] is None


def test_cli_pairs_hashes_rejects_injected_values_and_has_no_default_write(tmp_path, monkeypatch, capsys):
    setup_audit(monkeypatch)
    pair = dict(symbol="9999.TW", year=2026, quarter=2)
    for key, raw, url in [("report", b"report", REPORT_URL), ("statements", b"statements", TABLE_URL)]:
        (tmp_path / (key + ".pdf")).write_bytes(raw)
        pair[key] = dict(path=key + ".pdf", url=url, sha256=hashlib.sha256(raw).hexdigest(), observed_at=OBSERVED)
    manifest = tmp_path / "releases.json"
    manifest.write_text(json.dumps([pair]))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args = ["--quarter-releases-manifest", str(manifest)]
    assert main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert len(result["quarter_release_audits"]) == 1 and not result["calculation_enabled"]
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    pair["verified"] = True
    manifest.write_text(json.dumps([pair]))
    assert main(args) == 2 and json.loads(capsys.readouterr().out)["reason"] == "quarter_manifest_fields_invalid"
    pair.pop("verified")
    manifest.write_text(json.dumps([pair, pair]))
    assert main(args) == 2 and json.loads(capsys.readouterr().out)["reason"] == "duplicate_quarter_release_reference"
    pair["report"]["sha256"] = "c" * 64
    manifest.write_text(json.dumps([pair]))
    assert main(args) == 2 and json.loads(capsys.readouterr().out)["reason"] == "source_hash_mismatch"


@pytest.mark.parametrize("field,value,reason", [
    ("url", 123, "quarter_receipt_fields_invalid"),
    ("sha256", [], "quarter_receipt_fields_invalid"),
    ("bytes", True, "quarter_receipt_fields_invalid"),
    ("bytes", 1, "quarter_receipt_size_mismatch"),
])
def test_cli_invalid_receipt_never_creates_partial_output(tmp_path, monkeypatch, capsys, field, value, reason):
    setup_audit(monkeypatch)
    pair = dict(symbol="9999.TW", year=2026, quarter=2)
    for key, raw, url in [("report", b"report", REPORT_URL), ("statements", b"statements", TABLE_URL)]:
        (tmp_path / key).write_bytes(raw)
        pair[key] = dict(path=key, url=url, observed_at=OBSERVED, sha256=hashlib.sha256(raw).hexdigest())
    pair["statements"][field] = value
    manifest, output = tmp_path / "sources.json", tmp_path / "result.json"
    manifest.write_text(json.dumps([pair]))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert main(["--quarter-releases-manifest", str(manifest), "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == reason
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_cli_rejects_excessive_pairs_before_reading_any_sources(tmp_path, capsys):
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps([{}] * 7))
    assert main(["--quarter-releases-manifest", str(manifest)]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "quarter_manifest_maximum_6_pairs"
