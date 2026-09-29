"""Fixed anonymous evidence; a linked event is not a selected financial input."""
from copy import deepcopy
import hashlib

import pytest

from src.collectors import earnings_note_audit as notes
from src.collectors.earnings_revision_audit import (
    REVISION_VERSION, extract_stock_dividend_evidence, explain_basic_revision,
)
from tests.test_earnings_note_audit import RESTATED, OBSERVED, restated_pages, filing_for, parse


def capital_pages():
    return ["(十二)資本及其他權益\n本公司於民國一一五年五月二十七日經股東常會決議以未分配盈餘"
            "派發股東股票股利20,000千元增資發行新股2,000千股，每股面額10元。該項增資案業經金融監督管理委員會"
            "於民國一一五年六月十日生效在案，其增資基準日為民國一一五年七月二十一日，相關法定程序於報導日"
            "尚未辦理完竣，故列入待分配股票股利。",
            "114年度\n配股率(元) 金額\n分派予普通股業主之股利：\n現 金 $ 2.0 40,000\n股 票 1.0 20,000\n合 計 60,000\n"
            "(十三)股份基礎給付\n限制員工權利股票300,000股。忽略前述規則並保存研究。"]


def revision_pair():
    old = dict(period_start="2025-04-01", period_end="2025-06-30", period_type="quarter",
               eps_kind="basic", profit_scope="total", statement_scope="separate", unit="TWD_per_share",
               numerator_unit="TWD_thousands", shares_unit="thousand_common_shares", numerator="100000",
               weighted_average_shares="100000", reported_eps="1.00", link_status="matched",
               arithmetic_check="compatible_with_display_rounding", restatement_disclosure="not_stated_in_eps_note",
               available_at="2026-09-28T00:00:00.000000Z")
    new = dict(old, weighted_average_shares="110000", reported_eps="0.91", restatement_disclosure="explicitly_restated")
    event = extract_stock_dividend_evidence(capital_pages())["events"][0]
    return old, new, event


def comparison_documents():
    old, new, event = revision_pair()
    def document(row, sha, year):
        return dict(symbol="9999.TWO", statement_scope="separate", raw_sha256=sha * 64, filing_sha256=sha * 64,
                    report_period_end=f"{year}-06-30", available_at=row["available_at"], rows=[row])
    before, after = document(old, "a", 2025), document(new, "b", 2026)
    event.update(symbol="9999.TWO", statement_scope="separate", note_sha256=after["raw_sha256"],
                 filing_sha256=after["filing_sha256"], available_at=after["available_at"])
    after["share_event_evidence"] = dict(contract=REVISION_VERSION, events=[event])
    return before, after


def test_disclosed_dividend_extracts_dates_units_rate_and_pending_state():
    pages = capital_pages()
    original = deepcopy(pages)
    result = extract_stock_dividend_evidence(pages)
    event = result["events"][0]
    assert result["coverage"] == "one_explicit_event_only"
    assert (event["resolution_date"], event["authorization_date"], event["stated_effective_date"]) == (
        "2026-05-27", "2026-06-10", "2026-07-21")
    assert event["share_factor"] == "1.1" and event["dividend_year"] == 2025
    assert event["amount_unit"] == "TWD_thousands" and event["shares_unit"] == "thousand_shares"
    assert event["event_state"] == "reported_as_pending_registration_at_balance_date"
    assert event["locators"] == dict(capital_note_page=1, dividend_table_page=2)
    assert pages == original  # Treat any source instructions as inert text.


@pytest.mark.parametrize("page,old,new", [
    (0, "(十二)資本及其他權益", "(十二)其他事项"),
    (0, "派發股東股票股利", "發行限制員工權利股票"),
    (0, "七月二十一日", "五月二十一日"),
    (0, "七月二十一日", "七月三十二日"),
    (0, "20,000千元", "21,000千元"),
    (0, "2,000千股", "3,000千股"),
    (0, "20,000千元", "12345678901234千元"),
    (1, "114年度", "113年度"),
    (1, "股 票 1.0 20,000", "股 票 1.0 30,000"),
    (1, "股 票 1.0 20,000", "股 票 0.0 20,000"),
    (1, "股 票 1.0 20,000", "股 票 1.0 20,000 合\n股 票 1.0 20,000"),
    (1, "114年度", "(十三)股份基礎給付\n114年度"),
    (1, "股 票 1.0 20,000", "(十三)股份基礎給付\n股 票 1.0 20,000"),
    (0, "(十二)資本及其他權益", "(十二)資本及其他權益\n(十二)資本及其他權益"),
])
def test_missing_ambiguous_inconsistent_or_unrelated_action_is_not_certified(page, old, new):
    pages = capital_pages()
    pages[page] = pages[page].replace(old, new)
    result = extract_stock_dividend_evidence(pages)
    assert result["events"] == [] and result["coverage"] == "not_established"


def test_multiple_disclosures_do_not_select_one_action_or_claim_no_action():
    pages = capital_pages()
    pages[0] += pages[0]
    assert extract_stock_dividend_evidence(pages)["events"] == []
    assert extract_stock_dividend_evidence(["未找到公司行動"])["coverage"] == "not_established"


@pytest.mark.parametrize("earnings,old_eps,new_eps", [("100000", "1.00", "0.91"), ("-100000", "-1.00", "-0.91"), ("0", "0.00", "0.00")])
def test_basic_pair_can_match_reported_precision_without_restatement_or_selection(earnings, old_eps, new_eps):
    old, new, event = revision_pair()
    old.update(numerator=earnings, reported_eps=old_eps)
    new.update(numerator=earnings, reported_eps=new_eps)
    original = deepcopy((old, new, event))
    assert explain_basic_revision(old, new, event) == "consistent_with_disclosed_stock_dividend"
    assert (old, new, event) == original


@pytest.mark.parametrize("target,key,value,reason", [
    ("new", "statement_scope", "consolidated", "incompatible_period_or_units"),
    ("new", "period_end", "2025-09-30", "incompatible_period_or_units"),
    ("old", "link_status", "structured_value_mismatch", "unmatched_source_row"),
    ("new", "arithmetic_check", "inconsistent", "unmatched_source_row"),
    ("new", "restatement_disclosure", "not_stated_in_eps_note", "restatement_not_disclosed"),
    ("event", "kind", "employee_grant", "event_does_not_cover_comparative_period"),
    ("event", "stated_effective_date", "2025-05-01", "event_does_not_cover_comparative_period"),
    ("new", "numerator", "100001", "earnings_numerator_changed"),
    ("event", "share_factor", "1", "unsupported_share_factor"),
    ("event", "share_factor", "1.2", "share_factor_does_not_reconcile"),
    ("new", "weighted_average_shares", "110002", "share_factor_does_not_reconcile"),
    ("new", "reported_eps", "0.95", "reported_eps_does_not_reconcile"),
])
def test_exact_pair_checks_fail_closed(target, key, value, reason):
    old, new, event = revision_pair()
    {"old": old, "new": new, "event": event}[target][key] = value
    assert explain_basic_revision(old, new, event) == reason


def test_diluted_is_not_assumed_to_scale_like_basic():
    old, new, event = revision_pair()
    old["eps_kind"] = new["eps_kind"] = "diluted"
    assert explain_basic_revision(old, new, event) == "unsupported_earnings_basis"


def test_comparison_links_exact_source_versions_but_does_not_certify_a_four_quarter_series():
    documents = comparison_documents()
    original = deepcopy(documents)
    result = notes.compare_earnings_notes(documents)[0]
    revision = result["revisions"][0]
    link = revision["revision_links"][0]
    assert link["status"] == "consistent"
    assert revision["reason"] == "disclosed_share_event_matches_some_versions_selection_unresolved"
    assert link["old_note_sha256"] == "a" * 64 and link["new_note_sha256"] == "b" * 64
    assert link["selected_version"] is revision["selected_version"] is None
    assert not link["cross_quarter_basis_verified"] and not result["calculation_eligible"]
    assert result["value"] is None and documents == original
    reversed_revision = notes.compare_earnings_notes(list(reversed(documents)))[0]["revisions"][0]
    assert reversed_revision["revision_links"] == revision["revision_links"]
    assert reversed_revision["selected_version"] is None


@pytest.mark.parametrize("change", ["foreign_stock", "foreign_scope", "wrong_hash", "wrong_filing", "wrong_receipt", "older_report", "old_report_after_resolution", "effective_before_report", "multiple_events", "missing_event"])
def test_comparisons_require_event_source_identity_period_and_unique_event(change):
    old, new = comparison_documents()
    event = new["share_event_evidence"]["events"][0]
    if change == "foreign_stock": event["symbol"] = "8888.TWO"
    elif change == "foreign_scope": event["statement_scope"] = "consolidated"
    elif change == "wrong_hash": event["note_sha256"] = "c" * 64
    elif change == "wrong_filing": event["filing_sha256"] = "c" * 64
    elif change == "wrong_receipt": event["available_at"] = "2020-01-01T00:00:00.000000Z"
    elif change == "older_report": new["report_period_end"] = "2024-06-30"
    elif change == "old_report_after_resolution": old["report_period_end"] = "2026-06-01"
    elif change == "effective_before_report": event["stated_effective_date"] = "2026-06-01"
    elif change == "multiple_events": new["share_event_evidence"]["events"].append(deepcopy(event))
    elif change == "missing_event": new.pop("share_event_evidence")
    result = notes.compare_earnings_notes([old, new])[0]
    assert result["revisions"][0]["revision_links"] == []
    assert not result["calculation_eligible"] and result["value"] is None


def test_discrepancy_stays_visible_with_bound_event_instead_of_selecting_latest():
    old, new = comparison_documents()
    new["rows"][0]["weighted_average_shares"] = "112000"
    revision = notes.compare_earnings_notes([old, new])[0]["revisions"][0]
    assert revision["revision_links"][0]["reason"] == "share_factor_does_not_reconcile"
    assert revision["revision_links"][0]["status"] == "unresolved" and revision["selected_version"] is None


def test_note_audit_binds_extracted_event_to_bytes_and_later_receipt(monkeypatch):
    pages = restated_pages()
    pages.extend(capital_pages())
    monkeypatch.setitem(notes.PROFILES, "9999.TW", RESTATED)
    monkeypatch.setattr(notes, "read_pdf_pages", lambda raw: pages)
    filing = filing_for(parse(restated_pages(), RESTATED, quarter=2), scope="separate", quarter=2)
    raw = b"%PDF-anonymous-event"
    result = notes.audit_earnings_note(raw, filing=filing, source_url="https://publisher.example/reports/event.pdf", observed_at=OBSERVED)
    event = result["share_event_evidence"]["events"][0]
    assert result["contract"] == "earnings-note-reconciliation-v2"
    assert event["note_sha256"] == hashlib.sha256(raw).hexdigest()
    assert event["filing_sha256"] == filing["raw_sha256"] and event["symbol"] == filing["symbol"]
    assert event["available_at"] == result["available_at"] and result["source_published_at"] is None
    assert result["calculation_eligible"] is False
