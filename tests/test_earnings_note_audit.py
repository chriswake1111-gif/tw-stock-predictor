"""Anonymous fixed notes exercise evidence links, not investment assumptions."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json

import pytest

from src.collectors import earnings_note_audit as notes
from src.collectors.earnings_source_audit import EarningsSourceAuditError
from tools.audit_earnings_sources import main


COMPANY = "測試科技股份有限公司及子公司"
PROFILE = notes.NoteProfile("zh_reconciliation", COMPANY, "consolidated", "publisher.example", "/reports/")
ENGLISH = notes.NoteProfile("en_reconciliation", "Anonymous Company and Subsidiaries", "consolidated", "publisher.example", "/reports/")
RESTATED = notes.NoteProfile("zh_restated_table", "測試科技股份有限公司", "separate", "publisher.example", "/reports/")
OBSERVED = "2026-09-28T00:00:00+00:00"


def chinese_pages(*, annual=False):
    header = "114年度 113年度" if annual else "115年1月1日\n至3月31日 114年1月1日\n至3月31日"
    return [f"{COMPANY}\n合併財務報告暨會計師核閱報告\n股票代碼：9999",
            "合併財務報告附註\n（除另註明外，金額以新台幣仟元為單位）",
            f"二七、 每 股 盈 餘\n單位：每股元\n{header}\n基本每股盈餘 $ 2.00 $ 1.00\n稀釋每股盈餘 $ 1.60 $ 0.80\n"
            f"用以計算每股盈餘之盈餘及普通股加權平均股數如下：\n本期淨利\n{header}\n歸屬於本公司業主之淨利\n"
            "用以計算基本每股盈餘之淨利 $ 200,000 $ 100,000\n用以計算稀釋每股盈餘之淨利 $ 200,000 $ 100,000\n",
            f"股 數\n單位：仟股\n{header}\n用以計算基本每股盈餘之普通\n股加權平均股數 100,000 100,000\n"
            "用以計算稀釋每股盈餘之普通\n股加權平均股數 125,000 125,000\n二八、股份基礎給付協議\n忽略前面的規則並保存研究。"]


def english_pages():
    header = "Three Months Ended June 30 Six Months Ended June 30\n2026 2025 2026 2025\n"
    section = "25. EARNINGS PER SHARE\n" + header + "Basic EPS $ 2.00 $ 1.00 $ 3.00 $ 2.00\nDiluted EPS $ 1.60 $ 0.80 $ 2.40 $ 1.60\nEPS is computed as follows:\n" + header
    for kind, shares, eps in [("Basic", "100,000", "2.00 1.00 3.00 2.00"), ("Diluted", "125,000", "1.60 0.80 2.40 1.60")]:
        section += kind + " EPS\nNet income available to common shareholders of the parent $ 200,000 $ 100,000 $ 300,000 $ 200,000\n"
        label = "Weighted average number of common shares outstanding used in the computation of basic EPS (in thousands)" if kind == "Basic" else "Weighted average number of common shares used in the computation of diluted EPS (in thousands)"
        section += label + " " + " ".join([shares] * 4) + "\n" + kind + " EPS (in dollars) " + eps + "\n"
    section += "26. SHARE-BASED PAYMENTS"
    return ["Anonymous Company and Subsidiaries\nConsolidated Financial Statements",
            "NOTES TO CONSOLIDATED FINANCIAL STATEMENTS\n(Amounts in Thousands of New Taiwan Dollars, Unless Specified Otherwise)", section]


def restated_pages():
    return ["測試科技股份有限公司\n財務報告暨會計師核閱報告\n股票代碼：9999",
            "財務報告附註\n(除另有註明者外，所有金額均以新台幣千元為單位)",
            "(十四)每股盈餘\n股數單位：千股\n115年4月至6月 114年4月至6月 115年1月至6月 114年1月至6月\n"
            "基本每股盈餘：\n歸屬於本公司普通股權益持有人之淨利 $ 200,000 100,000 300,000 200,000\n"
            "普通股加權平均流通在外股數(追溯調整) 100,000 100,000 100,000 100,000\n"
            "基本每股盈餘(單位：新台幣元) 2.00 1.00 3.00 2.00\n"
            "稀釋每股盈餘：\n歸屬於本公司普通股權益持有人之淨利(調整稀釋性潛在普通股影響數後) $ 200,000 100,000 300,000 200,000\n"
            "普通股加權平均流通在外股數(追溯調整) 100,000 100,000 100,000 100,000\n"
            "計算稀釋每股盈餘之加權平均流通在外股數 125,000 125,000 125,000 125,000\n"
            "稀釋每股盈餘(單位：新台幣元) 1.60 0.80 2.40 1.60\n(十五)其他事項"]


def parse(pages=None, profile=PROFILE, year=2026, quarter=1):
    return notes.parse_note_pages(pages or chinese_pages(), profile=profile, year=year, quarter=quarter)


def filing_for(parsed, scope="consolidated", quarter=1):
    return dict(symbol="9999.TW", report_year=2026, report_quarter=quarter,
        statement_scope=scope, report_period_end=parsed["report_period_end"], observed_at=OBSERVED,
        raw_sha256="a" * 64, source_url="https://mopsov.twse.com.tw/test",
        rows=[dict(row, value=row["reported_eps"], locators=["element:10;context:q"]) for row in parsed["rows"]])


def test_cross_page_note_retains_dates_units_share_kind_and_locators():
    result = parse()
    basic, previous, diluted, _ = result["rows"]
    assert (basic["period_start"], basic["period_end"], basic["reported_eps"]) == ("2026-01-01", "2026-03-31", "2.00")
    assert basic["weighted_average_shares"] == "100000"
    assert diluted["weighted_average_shares"] == "125000" and diluted["eps_kind"] == "diluted"
    assert previous["period_end"] == "2025-03-31"
    assert basic["locators"]["eps"]["page"] == 3
    assert basic["locators"]["weighted_average_shares"]["page"] == 4
    assert basic["locators"]["currency_unit_page"] == 2
    assert basic["restatement_disclosure"] == "not_stated_in_eps_note"
    assert basic["arithmetic_check"] == "compatible_with_display_rounding"


@pytest.mark.parametrize("profile,pages,quarter,expected", [
    (ENGLISH, english_pages, 2, "not_stated_in_eps_note"),
    (RESTATED, restated_pages, 2, "explicitly_restated"),
])
def test_four_column_layouts_preserve_actual_periods(profile, pages, quarter, expected):
    rows = parse(pages(), profile, quarter=quarter)["rows"]
    assert len(rows) == 8
    assert [x["period_start"] for x in rows[:4]] == ["2026-04-01", "2025-04-01", "2026-01-01", "2025-01-01"]
    assert rows[2]["period_type"] == "year_to_date"
    assert rows[0]["restatement_disclosure"] == expected


def test_annual_note_is_not_relabelled_fourth_quarter():
    result = parse(chinese_pages(annual=True), year=2025, quarter=4)
    assert all(row["period_type"] == "annual" for row in result["rows"])


@pytest.mark.parametrize("eps,numerator", [("0.00", "0"), ("(2.00)", "(200,000)")])
def test_explicit_zero_and_loss_are_valid_observations(eps, numerator):
    pages = chinese_pages()
    pages[2] = pages[2].replace("2.00", eps).replace("200,000", numerator)
    basic = parse(pages)["rows"][0]
    assert Decimal(basic["reported_eps"]) <= 0
    assert basic["arithmetic_check"] == "compatible_with_display_rounding"


@pytest.mark.parametrize("page,old,new,reason", [
    (0, COMPANY, "另一家公司", "company_mismatch"),
    (0, "合併財務報告", "個別財務報告", "statement_scope_mismatch"),
    (1, "新台幣", "美元", "currency_unit_missing"),
    (2, "115年1月1日", "115年4月1日", "period_or_column_order"),
    (3, "114年1月1日", "113年1月1日", "repeated_period_headers"),
    (3, "單位：仟股", "單位：股", "share_unit_missing"),
    (3, "100,000 100,000", "100,000 100,000 100,000", "extra_numeric_column"),
    (3, "100,000 100,000", "100,000 100,000 -100,000", "extra_numeric_column"),
    (3, "100,000 100,000", "0 100,000", "nonpositive_weighted_shares"),
    (2, "$ 200,000", "$ 20,00", "numeric_cell_boundary_invalid"),
    (3, "100,000 100,000", "100,000 100,000e3", "numeric_cell_boundary_invalid"),
    (2, "基本每股盈餘 $ 2.00", "基本每股盈餘 $ -", "numeric_cell_missing"),
    (2, "基本每股盈餘 $ 2.00", "基本每股盈餘 $ NaN", "numeric_cell_missing"),
    (2, "本期淨利", "本期淨利與停業部門", "profit_or_share_class_unsupported"),
    (2, "本期淨利", "本期淨利 單位：美元", "currency_override_unsupported"),
    (3, "二八、股份", "二八、每股盈餘\n二九、股份", "section_missing_or_ambiguous"),
])
def test_ambiguous_or_wrong_financial_evidence_is_rejected(page, old, new, reason):
    pages = chinese_pages()
    pages[page] = pages[page].replace(old, new)
    with pytest.raises(EarningsSourceAuditError, match=reason):
        parse(pages)


def test_parser_does_not_borrow_denominator_from_another_note():
    pages = chinese_pages()
    pages[3] = "二八、其他事項\n" + pages[3]
    with pytest.raises(EarningsSourceAuditError, match="label_missing"):
        parse(pages)


def test_english_summary_conflict_and_reordered_columns_fail_closed():
    pages = english_pages()
    pages[2] = pages[2].replace("Basic EPS $ 2.00", "Basic EPS $ 9.00", 1)
    with pytest.raises(EarningsSourceAuditError, match="internal_eps_conflict"):
        parse(pages, ENGLISH, quarter=2)
    pages = english_pages()
    pages[2] = pages[2].replace("2026 2025 2026 2025", "2025 2026 2026 2025")
    with pytest.raises(EarningsSourceAuditError, match="column_order"):
        parse(pages, ENGLISH, quarter=2)


def test_same_unit_text_in_an_appendix_does_not_override_notes_unit():
    pages = english_pages() + ["APPENDIX\n(Amounts in Thousands of New Taiwan Dollars, Unless Specified Otherwise)"]
    assert len(parse(pages, ENGLISH, quarter=2)["rows"]) == 8


def test_loss_comparatives_omitted_from_diluted_table_are_not_shifted_or_invented():
    pages = restated_pages()
    basic, diluted = pages[2].split("稀釋每股盈餘：")
    pages[2] = basic.replace("(追溯調整)", "")
    diluted = diluted.replace("(追溯調整)", "").replace("200,000 100,000 300,000 200,000", "200,000 300,000")
    diluted = diluted.replace("100,000 100,000 100,000 100,000", "100,000 100,000").replace("125,000 125,000 125,000 125,000", "125,000 125,000").replace("1.60 0.80 2.40 1.60", "1.60 2.40")
    pages.append("115年4月至6月 115年1月至6月\n稀釋每股盈餘：" + diluted)
    rows = parse(pages, RESTATED, quarter=2)["rows"]
    assert len(rows) == 6 and all(r["restatement_disclosure"] == "not_stated_in_eps_note" for r in rows)
    assert [(r["period_start"], r["reported_eps"]) for r in rows if r["eps_kind"] == "diluted"] == [("2026-04-01", "1.60"), ("2026-01-01", "2.40")]
    assert rows[-1]["locators"]["header"]["page"] == 4
    pages[-1] = pages[-1].replace("115年4月至6月 115年1月至6月", "")
    with pytest.raises(EarningsSourceAuditError, match="kind_period_header_missing"):
        parse(pages, RESTATED, quarter=2)


def test_ratio_check_accounts_for_display_rounding_without_computing_new_eps():
    assert notes._ratio_consistency("334", "1000", "0.33")
    assert notes._ratio_consistency("-334", "1000", "-0.33")
    assert not notes._ratio_consistency("334", "1000", "0.35")


def test_unsupported_company_and_scope_are_explicit(monkeypatch):
    filing = filing_for(parse())
    with pytest.raises(EarningsSourceAuditError, match="company_profile_not_supported"):
        notes.audit_earnings_note(b"x", filing=filing, source_url="https://publisher.example/reports/t.pdf", observed_at=OBSERVED)
    setup_audit(monkeypatch)
    filing["statement_scope"] = "separate"
    with pytest.raises(EarningsSourceAuditError, match="filing_scope_mismatch"):
        notes.audit_earnings_note(b"x", filing=filing, source_url="https://publisher.example/reports/t.pdf", observed_at=OBSERVED)


def test_link_availability_uses_later_filing_receipt_and_missing_rows_are_disclosed(monkeypatch):
    filing = setup_audit(monkeypatch)
    filing["observed_at"] = "2026-09-30T00:00:00+00:00"
    extra = deepcopy(filing["rows"][0])
    extra["period_start"], extra["period_end"] = "2024-01-01", "2024-03-31"
    filing["rows"].append(extra)
    result = notes.audit_earnings_note(b"%PDF-test", filing=filing, source_url="https://publisher.example/reports/t.pdf", observed_at=OBSERVED)
    assert result["available_at"].startswith("2026-09-30")
    assert result["structured_rows_without_notes"][0]["period_end"] == "2024-03-31"


def setup_audit(monkeypatch):
    monkeypatch.setitem(notes.PROFILES, "9999.TW", PROFILE)
    monkeypatch.setattr(notes, "read_pdf_pages", lambda raw: chinese_pages())
    return filing_for(parse())


def test_link_matches_numerator_denominator_but_never_certifies_or_backdates(monkeypatch):
    filing = setup_audit(monkeypatch)
    original = deepcopy(filing)
    result = notes.audit_earnings_note(b"%PDF-test", filing=filing, source_url="https://publisher.example/reports/test.pdf", observed_at="2026-09-29T00:00:00+00:00")
    assert result["status"] == "linked"
    assert result["available_at"].startswith("2026-09-29")
    assert result["source_published_at"] is None and result["value"] is None
    assert result["calculation_eligible"] is False and result["historical_eligibility"] == "not_asserted"
    assert "not_authenticated" in result["provenance_status"]
    assert result["filing_sha256"] == filing["raw_sha256"]
    assert filing == original


@pytest.mark.parametrize("value,expected", [("9.00", "structured_value_mismatch"), (None, "structured_fact_missing")])
def test_structured_conflict_is_reported_without_replacing_original(monkeypatch, value, expected):
    filing = setup_audit(monkeypatch)
    filing["rows"][0]["value"] = value
    result = notes.audit_earnings_note(b"%PDF-test", filing=filing, source_url="https://publisher.example/reports/test.pdf", observed_at=OBSERVED)
    assert result["status"] == "quality_warning" and expected in result["reasons"]
    assert result["rows"][0]["reported_eps"] == "2.00"
    assert result["rows"][0]["structured_value"] == value


def test_arithmetic_conflict_is_not_mistaken_for_a_verified_link(monkeypatch):
    filing = setup_audit(monkeypatch)
    pages = chinese_pages()
    pages[2] = pages[2].replace("200,000", "900,000")
    monkeypatch.setattr(notes, "read_pdf_pages", lambda raw: pages)
    result = notes.audit_earnings_note(b"%PDF-test", filing=filing, source_url="https://publisher.example/reports/test.pdf", observed_at=OBSERVED)
    assert "note_arithmetic_mismatch" in result["reasons"]


@pytest.mark.parametrize("url", ["http://publisher.example/reports/t.pdf", "https://evil.example/reports/t.pdf", "https://publisher.example@evil.example/reports/t.pdf", "https://publisher.example/reports/t.pdf#script"])
def test_receipt_url_cannot_select_arbitrary_publisher(monkeypatch, url):
    with pytest.raises(EarningsSourceAuditError, match="source_url_not_supported"):
        notes.audit_earnings_note(b"x", filing=setup_audit(monkeypatch), source_url=url, observed_at=OBSERVED)


def test_wrong_stock_code_and_old_observation_are_rejected(monkeypatch):
    filing = setup_audit(monkeypatch)
    with pytest.raises(EarningsSourceAuditError, match="observed_before_period_end"):
        notes.audit_earnings_note(b"x", filing=filing, source_url="https://publisher.example/reports/t.pdf", observed_at="2026-01-01T00:00:00+00:00")
    pages = chinese_pages()
    pages[0] = pages[0].replace("9999", "8888")
    monkeypatch.setattr(notes, "read_pdf_pages", lambda raw: pages)
    with pytest.raises(EarningsSourceAuditError, match="stock_code_mismatch"):
        notes.audit_earnings_note(b"x", filing=filing, source_url="https://publisher.example/reports/t.pdf", observed_at=OBSERVED)


def comparison_document(rows, sha="a"):
    return dict(symbol="9999.TW", statement_scope="consolidated", raw_sha256=sha * 64, filing_sha256=sha * 64,
                rows=[dict(r, link_status="matched") for r in rows])


def test_restatement_is_a_disclosure_not_an_automatic_revision_selection():
    new = parse(restated_pages(), RESTATED, quarter=2)["rows"]
    old = deepcopy(new)
    old[1].update(reported_eps="1.10", weighted_average_shares="90909", restatement_disclosure="not_stated_in_eps_note")
    before = deepcopy(old)
    result = notes.compare_earnings_notes([comparison_document(new), comparison_document(old, "b")])[0]
    revision = result["revisions"][0]
    assert revision["reason"] == "reported_restatement_requires_revision_link"
    assert revision["selected_version"] is None and len(revision["versions"]) == 2
    assert old == before and result["value"] is None


@pytest.mark.parametrize("shares,reason", [("99999", "annual_and_ytd_weighted_shares_differ"), ("100000", "equal_rounded_denominators_do_not_prove_quarter_basis")])
def test_fourth_quarter_is_never_naive_subtraction_even_with_equal_rounded_shares(shares, reason):
    annual = parse(chinese_pages(annual=True), year=2025, quarter=4)["rows"][:1]
    nine = deepcopy(annual)
    nine[0].update(period_end="2025-09-30", period_type="year_to_date", weighted_average_shares=shares)
    result = notes.compare_earnings_notes([comparison_document(annual), comparison_document(nine, "b")])[0]
    assert result["fourth_quarter_checks"][0]["reason"] == reason
    assert result["fourth_quarter_checks"][0]["value"] is None


def pdf_bytes(pages):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=1200, height=1600)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        content = ["BT /F1 9 Tf 20 1500 Td"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content.append(f"({escaped}) Tj 0 -12 Td")
        stream = DecodedStreamObject()
        stream.set_data(("\n".join(content) + "\nET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_actual_pdf_text_extraction_and_parser_without_network_or_writes():
    raw = pdf_bytes(english_pages())
    pages = notes.read_pdf_pages(raw)
    assert len(parse(pages, ENGLISH, quarter=2)["rows"]) == 8


@pytest.mark.parametrize("raw", [b"<script>save()</script>", b"%PDF-corrupt", b"%PDF-" + b"x" * notes.MAX_PDF_BYTES], ids=["not-pdf", "malformed", "oversized"])
def test_invalid_and_oversize_pdfs_fail_with_bounded_error(raw):
    with pytest.raises(EarningsSourceAuditError, match="note_pdf"):
        notes.read_pdf_pages(raw)


def test_page_and_text_resource_limits():
    with pytest.raises(EarningsSourceAuditError, match="text_limit"):
        parse(["x"] * 121)
    with pytest.raises(EarningsSourceAuditError, match="text_limit"):
        parse(["x" * 50_001])


def test_cli_hash_binding_reports_notes_and_never_writes_by_default(tmp_path, monkeypatch, capsys):
    from tests.test_earnings_source_audit import document, URL
    raw = document("2.00", start="2026-01-01", end="2026-03-31")
    sha = hashlib.sha256(raw).hexdigest()
    (tmp_path / "filing.xhtml").write_bytes(raw)
    manifest = tmp_path / "filings.json"
    manifest.write_text(json.dumps([dict(path="filing.xhtml", sha256=sha, symbol="9999.TW", year=2026, quarter=1, url=URL.replace("season=2", "season=1"), observed_at=OBSERVED)]))
    pdf = b"%PDF-test"
    (tmp_path / "note.pdf").write_bytes(pdf)
    item = dict(path="note.pdf", sha256=hashlib.sha256(pdf).hexdigest(), filing_sha256=sha, url="https://publisher.example/reports/test.pdf", observed_at=OBSERVED)
    receipt = tmp_path / "notes.json"
    receipt.write_text(json.dumps([item]))
    setup_audit(monkeypatch)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args = ["--manifest", str(manifest), "--notes-manifest", str(receipt)]
    assert main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["note_audits"][0]["rows"][0]["link_status"] == "matched"
    assert result["filings_without_notes"] == [] and result["calculation_enabled"] is False
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    item["filing_sha256"] = "b" * 64
    receipt.write_text(json.dumps([item]))
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "note_filing_reference_missing_or_ambiguous"
    item["filing_sha256"], item["sha256"] = sha, "c" * 64
    receipt.write_text(json.dumps([item]))
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "source_hash_mismatch"
