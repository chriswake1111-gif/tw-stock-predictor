"""Link disclosed stock dividends to exact basic-EPS comparisons, offline only.

This narrow audit explains an existing source difference; it never restates a
number, selects a version, approves a hypothesis, or certifies a whole series.
"""
from datetime import date
from decimal import Decimal, localcontext
import re

REVISION_VERSION = "earnings-share-event-link-v1"
_DIGITS = dict(zip("〇零一二三四五六七八九", "00123456789"))
_CHINESE = r"[〇零一二三四五六七八九十]{1,5}"
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"


def _integer(text):
    if "十" in text:
        if text.count("十") != 1:
            raise ValueError("unsupported numeral")
        tens, ones = text.split("十")
        return (int(_DIGITS[tens]) if tens else 1) * 10 + (int(_DIGITS[ones]) if ones else 0)
    return int("".join(_DIGITS[char] for char in text))


def _roc_date(match, prefix):
    return date(_integer(match[prefix + "y"]) + 1911,
                _integer(match[prefix + "m"]), _integer(match[prefix + "d"])).isoformat()


def _date_pattern(prefix):
    return rf"民國(?P<{prefix}y>{_CHINESE})年(?P<{prefix}m>{_CHINESE})月(?P<{prefix}d>{_CHINESE})日"


def extract_stock_dividend_evidence(pages):
    """Recognize one disclosed ordinary stock dividend in the tested note format.

    Absence/unsupported wording is a coverage gap, never proof of no action.
    Identity, units, sizes and report scope are checked by the parent note audit.
    """
    compact = ["".join(page.split()) for page in pages]
    markers = [(i, match.start()) for i, text in enumerate(compact)
               for match in re.finditer(re.escape("(十二)資本及其他權益"), text)]
    base = dict(contract=REVISION_VERSION, events=[], coverage="not_established")
    if len(markers) != 1:
        return dict(base, reason="capital_note_missing_or_ambiguous")
    index, start = markers[0]
    selected = compact[index:index + 3]
    section = "".join(selected)[start:].split("(十三)股份基礎給付")[0]
    pattern = (r"本公司於" + _date_pattern("resolution_")
        + rf"經股東常會決議以未分配盈餘派發股東股票股利(?P<amount>{_NUMBER})千元增資發行新股"
        + rf"(?P<shares>{_NUMBER})千股，每股面額(?P<par>{_NUMBER})元。該項增資案業經金融監督管理委員會於"
        + _date_pattern("authorization_") + r"生效在案，其增資基準日為" + _date_pattern("effective_")
        + r"，相關法定程序於報導日尚未辦理完竣，故列入待分配股票股利。")
    matches = list(re.finditer(pattern, section))
    if len(matches) != 1 or section.count("派發股東股票股利") != 1:
        return dict(base, reason="stock_dividend_disclosure_missing_or_ambiguous")
    match = matches[0]
    # Rate and amount come from the ordinary-shareholder dividend table. Keep
    # whitespace between numbers: stripping it would merge the rate and amount.
    table_matches = []
    for page_index in range(index, min(index + 3, len(pages))):
        text = compact[page_index]
        headers = list(re.finditer(r"(?P<year>\d{3})年度配股率\(元\)金額分派予普通股業主之股利：", text))
        if (len(headers) != 1
                or "(十三)股份基礎給付" in text.split("分派予普通股業主之股利：")[0]):
            continue
        table_text = re.split(r"\(\s*十\s*三\s*\)\s*股\s*份\s*基\s*礎\s*給\s*付", pages[page_index], maxsplit=1)[0]
        for row in re.finditer(r"股\s*票\s+(?P<rate>\d{1,3}\.\d{1,4})\s+(?P<amount>" + _NUMBER + r")\s+合", table_text):
            table_matches.append((page_index, row, int(headers[0]["year"]) + 1911))
    if len(table_matches) != 1:
        return dict(base, reason="stock_dividend_rate_table_missing_or_ambiguous")
    table_page, table, dividend_year = table_matches[0]
    try:
        resolution, authorization, effective = [_roc_date(match, prefix) for prefix in
                                                ("resolution_", "authorization_", "effective_")]
        numbers = [match[key].replace(",", "") for key in ("amount", "shares", "par")]
        if any(len(number) > 13 for number in numbers):
            raise ValueError("unsupported precision")
        amount, shares, par = map(Decimal, numbers)
        rate = Decimal(table["rate"])
        if (not "2013-01-01" <= resolution <= authorization <= effective <= "2200-12-31"
                or dividend_year != int(resolution[:4]) - 1 or min(amount, shares, par, rate) <= 0
                or amount != Decimal(table["amount"].replace(",", ""))):
            raise ValueError("disclosure inconsistency")
        with localcontext() as context:
            context.prec = 50
            # Both amounts and issued shares are shown in thousands, rounded.
            if abs(amount - shares * par) > Decimal("0.5") * (1 + par):
                raise ValueError("share amount inconsistency")
            factor = 1 + rate / par
        if factor > 10:
            raise ValueError("unsupported factor")
    except (ValueError, KeyError, ArithmeticError):
        return dict(base, reason="stock_dividend_disclosure_inconsistent")
    paragraph_page = index + 1
    position = start + match.start()
    for offset, page in enumerate(selected):
        if position < len(page):
            paragraph_page = index + offset + 1
            break
        position -= len(page)
    event = dict(kind="ordinary_stock_dividend", dividend_year=dividend_year, resolution_date=resolution,
        authorization_date=authorization, stated_effective_date=effective,
        event_state="reported_as_pending_registration_at_balance_date",
        amount=str(amount), amount_unit="TWD_thousands", issued_shares=str(shares), shares_unit="thousand_shares",
        par_value=str(par), dividend_per_existing_share=str(rate), rate_unit="TWD_per_share",
        share_factor=format(factor, "f"), factor_method="1_plus_stock_dividend_per_share_divided_by_par_value",
        locators=dict(capital_note_page=paragraph_page, dividend_table_page=table_page + 1))
    return dict(base, events=[event], coverage="one_explicit_event_only", reason=None)


def explain_basic_revision(old, new, event):
    """Check only an exact pair already matched against its structured filing."""
    required = ("period_start", "period_end", "eps_kind", "profit_scope", "statement_scope",
                "numerator_unit", "shares_unit", "unit")
    if any(old.get(key) != new.get(key) for key in required):
        return "incompatible_period_or_units"
    if (old.get("eps_kind") != "basic" or old.get("profit_scope") != "total"
            or old.get("unit") != "TWD_per_share" or old.get("numerator_unit") != "TWD_thousands"
            or old.get("shares_unit") != "thousand_common_shares"):
        return "unsupported_earnings_basis"
    if any(row.get("link_status") != "matched" or row.get("arithmetic_check") != "compatible_with_display_rounding" for row in (old, new)):
        return "unmatched_source_row"
    if new.get("restatement_disclosure") != "explicitly_restated":
        return "restatement_not_disclosed"
    if (event.get("kind") != "ordinary_stock_dividend"
            or not old["period_end"] < event["stated_effective_date"]):
        return "event_does_not_cover_comparative_period"
    with localcontext() as context:
        context.prec = 50
        if Decimal(old["numerator"]) != Decimal(new["numerator"]):
            return "earnings_numerator_changed"
        factor = Decimal(event["share_factor"])
        if not 1 < factor <= 10:
            return "unsupported_share_factor"
        before, after = Decimal(old["weighted_average_shares"]), Decimal(new["weighted_average_shares"])
        half = Decimal(".5")
        if before <= half or after <= half or abs(before * factor - after) > half * (1 + factor):
            return "share_factor_does_not_reconcile"
        # This interval check preserves the precision of both published ratios.
        old_eps, new_eps, epsilon = Decimal(old["reported_eps"]), Decimal(new["reported_eps"]), Decimal(".005")
        if (old_eps + epsilon) / factor < new_eps - epsilon or (old_eps - epsilon) / factor > new_eps + epsilon:
            return "reported_eps_does_not_reconcile"
    return "consistent_with_disclosed_stock_dividend"
