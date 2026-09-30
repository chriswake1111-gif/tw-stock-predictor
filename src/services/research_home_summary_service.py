"""Bounded, read-only presentation of the existing consistent journal review."""
import os
import sqlite3
from pathlib import Path

from src.domain.valuation import utc_now_timestamp
from src.repositories.analysis_snapshot_repository import SnapshotIntegrityError
from src.services.daily_research_journal_service import DailyResearchJournalService, comparable_facts
from src.services.research_library_service import canonical, library_enabled

CONTRACT = "research_home_summary_v1"
FACT_NAMES = {"official_close": "官方收盤價", "pe": "市場本益比", "pb": "股價淨值比",
              "yield_ratio": "殖利率", "volume": "成交股數"}
SCENARIO_REASONS = {
    "forward_eps_approval_revoked": "全年獲利假設的核准已撤銷。",
    "anchor_approval_revoked": "波段錨點的核准已撤銷。",
    "approval_revoked": "原情境的核准已撤銷。",
    "forward_eps_missing_at_knowledge_cutoff": "本次資料截止時間內缺少適用的全年獲利依據。",
    "manual_anchor_required": "本次沒有可用的人工波段錨點。",
    "approved_manual_anchor_required": "本次缺少有效核准的波段錨點。",
    "approved_forward_eps_required": "本次缺少有效核准的全年獲利假設。",
    "approved_symbol_pe_missing_at_knowledge_cutoff": "本次資料截止時間內缺少適用且已核准的估值倍數。",
    "approved_symbol_pe_year_mismatch": "本次獲利假設與估值倍數的適用年度不一致。",
    "pe_fiscal_year_required": "估值倍數尚未指定適用年度。",
}


def home_summary_enabled():
    return os.getenv("RESEARCH_HOME_SUMMARY_ENABLED", "true").lower() == "true"


def project_home_summary(review):
    """No valuation calculation, source fetching, or human-decision inference."""
    current = review["current"]["summary"]
    previous = review["previous"]
    comparison = review["comparison"]
    guide = review.get("guidance") or {}
    gaps = guide.get("gaps", [])
    limits = [dict(id=g["id"], text=g["title"], impact=g["impact"], owner=g["owner"])
              for g in gaps]
    for dataset, data in {**current.get("market_data", {}), **current.get("public_data", {})}.items():
        if data.get("last_update_status") in {"failed", "partial"}:
            limits.append(dict(id=dataset + ":update", text="資料更新未完成，保留已取得內容與日期。", owner="program"))
        if data.get("is_stale") or data.get("status") == "stale":
            limits.append(dict(id=dataset + ":stale", text="部分資料已過期，不能視為目前最新資料。", owner="program"))
        if data.get("status") in {"quality_warning", "insufficient_data", "unavailable", "failed", "not_collected"} or data.get("quality_status") == "quality_warning":
            limits.append(dict(id=dataset + ":quality", text="部分來源資料不足或品質待核對，請閱讀資料狀態。", owner="program"))
    market = current.get("market_context", {})
    if market.get("close_status") != "available":
        limits.append(dict(id="close", text="官方行情尚未就緒，不能宣稱行情沒有變化。", owner="program"))
    changes = []
    lost = []
    if previous:
        for key, name in (("valuation_context", "估值情境"), ("technical_context", "波段情境")):
            old, new = previous["summary"].get(key, {}), current.get(key, {})
            def combinations(context):
                return {tuple(row.get(k) for k in ("fiscal_year", "observation_id", "pe_scenario_id", "eps_scenario"))
                        for row in context.get("target_matrix", []) if row.get("status", "available") == "available"}
            if old.get("status") == "available" and (new.get("status") != "available" or combinations(old) - combinations(new)):
                reason = new.get("reason_code") or "scenario_content_no_longer_available"
                explanation = SCENARIO_REASONS.get(reason, "原情境組合未出現在本次可用結果；請閱讀目前資料及核准狀態。")
                lost.append(dict(field=key, text="部分原先可用的" + name + "目前需要重新確認。" + explanation, reason=reason))
    for fact in comparison.get("facts", []):
        name = FACT_NAMES[fact["field"]]
        before, after = fact["before"], fact["after"]
        if fact["status"] != "comparable":
            if before != after:
                changes.append(dict(**fact, kind="not_comparable", text=name + "的資料口徑或可用性改變，不能直接計算差值。"))
            else:
                limits.append(dict(id=fact["field"] + ":missing", text=name + "缺少可直接比較的資料。", owner="program"))
        elif before["value"] != after["value"] or before["date"] != after["date"]:
            kind = "same_date_revision" if before["date"] == after["date"] else "date_updated" if before["value"] == after["value"] else "value_changed"
            message = {"same_date_revision": "同一資料日的數字有修訂", "date_updated": "資料日期已更新，數字相同", "value_changed": "數字與資料日期有變化"}[kind]
            changes.append(dict(**fact, kind=kind, text=name + message + "。"))
    if comparison.get("assumptions_changed"):
        changes.append(dict(kind="scenario_changed", text="程式情境內容有差異，請閱讀前後依據；不能直接推論你修改了假設。"))
    next_step = dict(target="research-data", label="閱讀資料狀態", owner="program")
    main = guide.get("next_step") or {}
    if lost:
        status, text = "scenario_unavailable", lost[0]["text"]
        next_step = dict(target="research-changes", label="查看情境變化與原因", owner="user")
    elif changes:
        status, text = "changed", changes[0]["text"]
        next_step = dict(target="research-changes", label="閱讀前後比較", owner="user")
    elif main.get("owner") == "user" and main.get("id") not in {None, "read"}:
        status, text = "choice_required", main["title"]
        next_step = dict(target="research-candidates", label=main["action"], owner="user")
    elif not previous:
        status, text = "no_baseline", "尚無比較基準，可先閱讀目前資料。"
    elif limits:
        status, text = "incomplete", limits[0]["text"]
        next_step["owner"] = limits[0]["owner"]
    else:
        status, text = "unchanged", "本機可比較內容與上次相同。"
        next_step = dict(target="research-changes", label="閱讀比較依據", owner="user")
    # Explicit allowlist: never expose notes, candidate prose, or confirmation hashes.
    prior_facts = comparable_facts(previous["summary"]) if previous else {}
    return dict(contract_version=CONTRACT, enabled=True, symbol=review["symbol"],
                prepared_at=review["knowledge_cutoff_at"], local_only=True,
                baseline={k: previous.get(k) for k in ("entry_id", "created_at", "knowledge_cutoff_at")} if previous else None,
                selected_year=guide.get("selected_year"), status=status, headline=text,
                dates=[dict(field=key, label=FACT_NAMES[key], current=value["date"],
                            previous=prior_facts[key]["date"] if previous else None)
                       for key, value in comparable_facts(current).items()],
                changes=changes, unavailable_scenarios=lost, limitations=limits, next_step=next_step)


class ResearchHomeSummaryService:
    def __init__(self, db_path):
        self.db_path = db_path

    def get(self, symbol):
        symbol = canonical(symbol)
        if not library_enabled() or not home_summary_enabled():
            return dict(contract_version=CONTRACT, enabled=False, symbol=symbol)
        try:
            if not Path(self.db_path).is_file():
                raise sqlite3.OperationalError("local_database_unavailable")
            review = DailyResearchJournalService(self.db_path).preview(symbol, prefer_saved_year=True)
            return project_home_summary(review)
        except (sqlite3.Error, SnapshotIntegrityError, ValueError, KeyError, TypeError):
            # Never fall back to an older entry or leak stored content in errors.
            return dict(contract_version=CONTRACT, enabled=True, symbol=symbol, prepared_at=utc_now_timestamp(),
                        local_only=True, baseline=None, selected_year=None, status="unavailable",
                        headline="暫時無法比較：本機讀取或保存紀錄完整性檢查未完成。",
                        dates=[], changes=[], unavailable_scenarios=[], limitations=[dict(id="read_failed", owner="program",
                        text="無法確認最近保存的比較基準，沒有改用較舊研究。")],
                        next_step=dict(target="research-data", label="閱讀資料狀態", owner="program"))
