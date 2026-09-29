"""Code-reviewed source coverage and offline work plans, never financial facts.

These descriptions cannot enable a parser, an outbound URL, or a calculation.
New periods still require original documents and financial regression evidence.
"""
from copy import deepcopy
from datetime import date

from src.collectors.earnings_quarter_audit import _period, _previous
from src.collectors.earnings_sources_v2 import sources_for
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import normalize_utc_timestamp

COVERAGE_CONTRACT = "earnings-source-coverage-v1"
REVIEWS = {
    "2303.TW": dict(status="reviewed_window", reviewed_at="2026-09-29T14:51:51+00:00",
                    window_start="2025-07-01", window_end="2026-06-30",
                    basis_start="2025-01-01", basis_end="2026-06-30"),
    "4966.TWO": dict(status="reviewed_window", reviewed_at="2026-09-29T14:51:51+00:00",
                     window_start="2025-07-01", window_end="2026-06-30",
                     basis_start="2025-01-01", basis_end="2026-06-30"),
    "3491.TWO": dict(
        status="evidence_incomplete", reviewed_at="2026-09-29T14:51:51+00:00",
        window_start="2025-07-01", window_end="2026-06-30",
        blockers=[
            "2025 年第四季尚缺可核對基本口徑、盈餘分子及期間加權平均股數的直接單季來源。",
            "全年與前三季的加權平均股數不同，不能直接相減每股盈餘；四季股數變動與修訂基準也仍需完整核對。",
        ],
        next_action="助理先補查第四季原始明細；取得充分來源後，由開發端完成格式與股數核對。可先保存部分研究，不需猜值。",
        review_scope="已核對既有財報附註，並補讀 2026 年 3 月 11 日、4 月 28 日、8 月 28 日法說會及股東會年報；不是全市場來源搜尋結果。",
        references=[
            dict(title="昇達科原始財報索引", url="https://www.umt-tw.com/tw/financial_report.php"),
            dict(title="昇達科 2026 年 4 月 28 日簡報，第 7 頁", url="https://www.umt-tw.com/upload/Investors/3491_20260428.pdf"),
        ],
    ),
}


def coverage_for(symbol, cutoff):
    """Do not expose a later source review in an earlier knowledge cut."""
    review = REVIEWS.get(symbol)
    cutoff = normalize_utc_timestamp(cutoff, "knowledge_cutoff_at")
    if review is None or cutoff < normalize_utc_timestamp(review["reviewed_at"], "reviewed_at"):
        return None
    return dict(deepcopy(review), contract_version=COVERAGE_CONTRACT,
                financial_eligibility=False, automatically_discovers_new_reports=False)


def plan_coverage(symbol, window_end):
    """A successful planning command is not successful financial verification."""
    parse_canonical_symbol(symbol)
    end = date.fromisoformat(window_end)
    quarter = (end.month - 1) // 3 + 1
    if window_end != _period(end.year, quarter)[1] or not 1901 <= end.year <= 2200:
        raise ValueError("quarter_end_required")
    periods = [(end.year, quarter)]
    for _ in range(3):
        periods.append(_previous(*periods[-1]))
    review = deepcopy(REVIEWS.get(symbol))
    sources = sources_for(symbol)
    required = []
    for year, q in reversed(periods):
        start, finish = _period(year, q)
        items = [s for s in sources if (s["year"], s["quarter"]) == (year, q) and s["role"] != "basis"]
        required.append(dict(period_start=start, period_end=finish,
            status="catalogued_documents" if items else "source_review_required",
            source_keys=[s["key"] for s in items]))
    same_window = bool(review and review["status"] == "reviewed_window" and review["window_end"] == window_end)
    return dict(status="coverage_plan", contract_version=COVERAGE_CONTRACT,
        symbol=symbol, window_start=required[0]["period_start"], window_end=window_end,
        calculation_enabled=False, network_performed=False, catalog_changed=False,
        catalog_window_supported=same_window, reviewed_coverage=review, required_quarters=required,
        basis_coverage_sufficient=bool(review and review["status"] == "reviewed_window"
                                     and review["basis_start"] <= required[0]["period_start"]
                                     and review["basis_end"] >= window_end),
        next_steps=[
            "取得所缺季度的公司原始文件，保留網址、原始位元組、內容指紋與實際取得時間。",
            "核對基本每股盈餘、合併範圍、母公司股東盈餘、期間加權平均股數、幣別與單季期間。",
            "核對涵蓋整個窗口的股數變動、追溯修訂及重複揭露；第四季不得直接以全年每股盈餘減前三季。",
            "新增季度或格式須更新解析器及有限來源登錄，通過固定來源、衝突、缺季及歷史截止測試後才可啟用。",
            "在隔離資料重跑來源稽核及更新讀回；舊快照、核准與正式研究保持原內容。",
        ] if not same_window else ["可沿用既有受控更新；仍須實際下載及內容核對通過，清單本身不授予資料資格。"])
