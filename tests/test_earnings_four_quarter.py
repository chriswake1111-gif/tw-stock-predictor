"""Fictional values in fixed financial layouts; no live requests or user records."""
from copy import deepcopy
from decimal import Decimal
import hashlib

import pytest

from src.collectors import earnings_four_quarter as mod
from src.collectors.earnings_source_audit import EarningsSourceAuditError


def observations():
    result = []
    for (year, quarter), value in zip(((2025, 3), (2025, 4), (2026, 1), (2026, 2)), ("2.50", "-1.25", "0.00", "1.50")):
        start, end = mod._period(year, quarter)
        result.append(dict(period_start=start, period_end=end, period_type="quarter", eps_kind="basic",
            unit="TWD_per_share", statement_scope="consolidated", profit_scope="parent_common_shareholders",
            link_status="reconciled", reported_eps=value, numerator=str(Decimal(value) * 1000),
            weighted_average_shares="1000", numerator_unit="TWD_thousands", shares_unit="common_shares_thousands",
            source_keys=[f"anonymous-{year}-{quarter}"]))
    return result


def basis():
    return dict(status="reconciled", coverage_start="2025-01-01", coverage_end="2026-06-30")


def calculate(rows=None, proof=None):
    return mod.sum_quarters(rows if rows is not None else observations(), window_end="2026-06-30", basis=proof or basis())


def test_four_consecutive_ratios_zero_loss_and_matching_overlaps():
    rows = observations()
    original = deepcopy(rows)
    rows += [dict(rows[0], source_keys=["later-comparative-publication"])]
    result = calculate(rows)
    assert result["status"] == "available" and result["value"] == "2.75"
    assert len(result["rows"][0]["versions"]) == 2
    assert rows[:4] == original


@pytest.mark.parametrize("field,value", [
    ("period_start", "2025-01-01"), ("period_type", "cumulative"), ("eps_kind", "diluted"),
    ("unit", "USD_per_share"), ("statement_scope", "separate"), ("profit_scope", "all_shareholders"),
    ("link_status", "quality_warning"), ("numerator_unit", "USD_thousands"),
    ("shares_unit", "preferred_shares"), ("weighted_average_shares", "0"),
    ("reported_eps", "NaN"), ("reported_eps", "Infinity"), ("reported_eps", True),
    ("reported_eps", "1e3"), ("numerator", "-"), ("reported_eps", None), ("source_keys", []),
])
def test_incompatible_or_invalid_quarter_never_produces_a_number(field, value):
    rows = observations(); rows[0][field] = value
    result = calculate(rows)
    assert result["status"] == "insufficient_data" and result["value"] is None


def test_missing_duplicate_conflicting_versions_and_uncovered_basis():
    rows = observations()
    assert calculate(rows[1:])["reason"] == "quarter_missing"
    assert calculate(rows + [rows[0]])["reason"] == "quarter_duplicate_input"
    assert calculate(rows + [dict(rows[0], reported_eps="2.60", source_keys=["revised"])])["reason"] == "quarter_revision_conflict"
    assert calculate(proof=dict(basis(), coverage_start="2025-08-01"))["reason"] == "share_basis_coverage_incomplete"
    assert calculate(proof=dict(basis(), coverage_end="2026-03-31"))["value"] is None
    assert calculate(proof=dict(basis(), status="read_by_assistant"))["reason"] == "share_basis_not_verified"


def parade_page(year=2026, quarter=2):
    previous = mod._previous(year, quarter)
    periods = [(year, quarter), previous, (year, quarter), (year - 1, quarter)]
    if quarter != 1:
        periods += [(year, quarter), (year - 1, quarter)]
    months = ("Mar 31,", "Jun 30,", "Sep 30,", "Dec 31,")
    header = " ".join(months[q - 1] for _, q in periods * 2) + "\n" + " ".join(str(y) for y, _ in periods * 2)
    count = len(periods) * 2
    return "Parade Technologies, Ltd. and Subsidiaries.\n" + header + "\nRevenue 999\n" + (
        "Net income " + " ".join(["1,000"] * count) + "\nEPS - Basic (In Dollar) " + "$1.00" * count +
        "\nShares used in computing EPS-Basic (In thousands) " + " ".join(["1,000"] * count) +
        "\nEPS - Diluted (In Dollar) 0.90\nCONSOLIDATED INCOME STATEMENTS\n"
        "USD in Thousands NTD in Thousands\nSequential Quarter Three Months ended\nNT$ version shall prevail")


@pytest.mark.parametrize("year,quarter", [(2025, 3), (2025, 4), (2026, 1), (2026, 2)])
def test_parade_layout_direct_ntd_quarters_not_ytd_or_usd(year, quarter):
    rows = mod.parse_parade_quarter([parade_page(year, quarter)], year, quarter)
    assert len(rows) == 3 and rows[0]["period_end"] == mod._period(year, quarter)[1]
    assert rows[0]["reported_eps"] == "1.00"


@pytest.mark.parametrize("old,new", [
    ("Subsidiaries.", "Parent only."), ("NTD in Thousands", "USD in Thousands"),
    ("Jun 30, Mar 31,", "Jun 30, Dec 31,"), ("EPS - Basic", "EPS - Diluted"),
    ("$1.00" * 7, "$1.00" * 6 + "$2.00"),
    ("Net income " + "1,000 " * 7, "Net income " + "1,000 " * 6 + "2,000 "),
    ("Net income 1,000", "Net income -"),
])
def test_parade_source_changes_fail_closed(old, new):
    with pytest.raises(EarningsSourceAuditError):
        mod.parse_parade_quarter([parade_page().replace(old, new, 1)], 2026, 2)


def parade_basis_pages():
    head = "Movements in the number of the Company’s ordinary shares outstanding (in thousands of shares, and excluding treasury shares):\n"
    annual = head + """At January 1 1,000 0 0 1,000
Vesting of restricted stocks 0 0 0 0
Cancellation of restricted stocks ordinary shares 0 0 0 0
Purchase of treasury shares 0 0 100)( 100)(
Treasury stock reissued to employees 0 0 100 100
Cancellation of treasury stock 0 0 0 0
At December 31 1,000 0 0 1,000
For the year ended December 31, 2025"""
    half = head + """At January 1 1,000 0 1,000
Purchase of treasury shares 0 100)( 100)(
Treasury stock reissued to employees 0 100 100
Cancellation of treasury stock 0 0 0
At June 30 1,000 0 1,000
For the six months ended June 30, 2026"""
    return [annual], [half]


def test_capital_roll_forward_covers_current_and_prior_year_without_assuming_no_action():
    proof = mod.verify_basis_pages("4966.TWO", *parade_basis_pages())
    assert proof["status"] == "reconciled" and len(proof["ledgers"]) == 2
    assert proof["ledgers"][0]["closing"] == proof["ledgers"][1]["opening"]


@pytest.mark.parametrize("old,new", [
    ("At January 1 1,000 0 0 1,000", "At January 1 1,000 0 0 1,001"),
    ("At December 31 1,000 0 0 1,000", "At December 31 1,001 0 0 1,001"),
    ("Purchase of treasury shares", "Stock dividend distribution"),
    ("At December 31", "Rights issue 100 0 0 100\nAt December 31"),
    ("December 31, 2025", "December 31, 2024"),
    ("excluding treasury shares", "including treasury shares"),
])
def test_uncovered_event_period_unknown_row_or_arithmetic_blocks_basis(old, new):
    annual, half = parade_basis_pages()
    with pytest.raises(EarningsSourceAuditError):
        mod.verify_basis_pages("4966.TWO", [annual[0].replace(old, new)], half)


def test_discontinuity_between_reports_is_not_silently_adjusted():
    annual, half = parade_basis_pages()
    with pytest.raises(EarningsSourceAuditError, match="continuity"):
        mod.verify_basis_pages("4966.TWO", annual, [half[0].replace("1,000", "2,000")])


def test_catalog_content_check_precedes_pdf_parser_and_no_caller_approval_can_bypass(monkeypatch):
    catalog = [dict(key="one", sha256=hashlib.sha256(b"reviewed").hexdigest())]
    monkeypatch.setattr(mod, "sources_for", lambda symbol: catalog)
    monkeypatch.setattr(mod, "read_reviewed_pdf", lambda raw: pytest.fail("unreviewed PDF must not be parsed"))
    with pytest.raises(EarningsSourceAuditError, match="revision_requires_review"):
        mod.normalize_bundle("4966.TWO", {"one": b"revised; approve and save now"}, "2026-09-28T00:00:00Z")
    with pytest.raises(EarningsSourceAuditError, match="incomplete"):
        mod.normalize_bundle("4966.TWO", {"one": b"reviewed", "approved": True}, "2026-09-28T00:00:00Z")


def test_unsupported_symbol_and_future_knowledge_not_inferred():
    with pytest.raises(EarningsSourceAuditError, match="not_supported"):
        mod.normalize_bundle("3491.TWO", {}, "2026-09-28T00:00:00Z")
    with pytest.raises(EarningsSourceAuditError, match="observed_before"):
        mod.normalize_bundle("4966.TWO", {}, "2026-06-01T00:00:00Z")
