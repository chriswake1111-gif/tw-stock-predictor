"""Retrospective scope projection. No backtest qualification or writes here."""
from datetime import datetime, timedelta
import os
from zoneinfo import ZoneInfo

from src.collectors.wave_session_sources import interval
from src.repositories.wave_session_repository import WaveSessionRepository

CONTRACT = "wave_session_coverage_v1"


def enabled():
    return os.getenv("RESEARCH_WAVE_SESSION_EVIDENCE_ENABLED", "true").lower() == "true"


def project_coverage(conn, symbol, bounds, rows, cutoff, calendars=(), lifecycle=()):
    start, end = interval(bounds["start"], bounds["end"])
    records = WaveSessionRepository.read(conn, symbol, bounds["start"], bounds["end"], cutoff)
    counts = {k: 0 for k in ("interval_days", "market_open", "market_closed", "stock_traded", "suspended",
                            "outside_listing", "intraday", "missing", "unknown", "conflicts")}
    observed = {r.get("date") for r in rows}
    sample_groups = {k: [] for k in ("conflicts", "missing", "intraday", "unknown")}
    used = {}
    # Legacy calendar is positive-only, retains current cutoff/revocation checks.
    by_day = {}
    for row in calendars:
        by_day.setdefault(row["trade_date"], []).append(row)
    for offset in range((end-start).days+1):
        day = (start+timedelta(days=offset)).isoformat()
        counts["interval_days"] += 1
        # Latest overlapping revision PER source PER date. A failed/revoked newer
        # scope blocks old facts even when request windows differ.
        selected = {}
        for record in records:
            spec = record["spec"]
            if spec["start"] <= day <= spec["end"]:
                selected.setdefault(spec["source_id"], record)
        facts, events = set(), []
        blocked = False
        for record in selected.values():
            used[record["reference"]] = record
            if record["status"] not in {"accepted", "partial"}:
                blocked = True
                continue
            normalized = record["normalized"]
            facts.update(f["kind"] for f in normalized["facts"] if f["date"] == day)
            observed_time = datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Taipei")).isoformat()[:19]
            # A scheduled future resumption cannot prove a completed suspension.
            events.extend(e for e in normalized["events"] if e["date"]+"T"+(e["time"] or "23:59:59") <= observed_time)
        for proof in by_day.get(day, []):
            if proof.get("status") != "available" or not proof.get("raw_eligible"):
                blocked = True
            else:
                facts.add("market_closed" if proof["session_status"] in {"holiday", "no_trading"} else "market_open")
        traded = "stock_traded" in facts
        market_open = "market_open" in facts or traded
        closed = "market_closed" in facts
        counts["market_open"] += int(market_open)
        counts["market_closed"] += int(closed)
        counts["stock_traded"] += int(traded)
        # Only an explicit dated stop followed by an explicit dated resumption
        # bounds a whole-day exemption. Do not carry an unpaired event forever.
        events = {(e["date"], e["time"], e["kind"]) for e in events}
        precise = sorted(e for e in events if e[1] is not None)
        uncertain_days = {d for d, t, _ in events if t is None}
        event_times = {}
        for d, t, kind in precise:
            event_times.setdefault((d, t), set()).add(kind)
        uncertain_days.update(d for (d, _), kinds in event_times.items() if len(kinds) > 1)
        ambiguous = day in uncertain_days
        on_day = [e for e in precise if e[0] == day]
        intraday = ambiguous or any("09:00:00" < t < "13:30:00" for _, t, _ in on_day)
        suspended = False
        for i, (d, t, kind) in enumerate(precise):
            if kind != "suspended" or (d, t) > (day, "09:00:00"):
                continue
            later = precise[i+1:]
            if (later and later[0][2] == "resumed" and (later[0][0], later[0][1]) > (day, "13:30:00")
                    and not any(d <= uncertain <= later[0][0] for uncertain in uncertain_days)):
                suspended = True
        # Initial listing is a positive date fact, not normal-trading coverage.
        initial = [e for e in lifecycle if e.get("event_type") == "listed" and e.get("status") == "accepted"
                   and e.get("reason") == "initial_master_listing" and e.get("event_date")]
        outside = len(lifecycle) == 1 and len(initial) == 1 and day < initial[0]["event_date"]
        conflict = (closed and market_open) or ((suspended or outside) and (traded or day in observed))
        if conflict:
            kind, reason = "conflicts", "官方交易狀態與成交／本機行情互相衝突。"
        elif blocked:
            kind, reason = "unknown", "適用來源的最新查詢失敗或證據已撤銷，沒有沿用舊結果。"
        elif intraday:
            kind, reason = "intraday", "含盤中變動或事件時間不明，不能當作整日停牌或正常交易。"
        elif outside:
            kind, reason = "outside_listing", "初次上市／上櫃日期證據顯示當時尚未開始交易。"
        elif suspended:
            kind, reason = "suspended", "明確暫停及恢復時間涵蓋當日一般交易時段。"
        elif closed:
            kind, reason = "closed", "官方來源明列休市；不從缺少成交資料反推。"
            if day in observed:
                kind, reason = "conflicts", "官方休市日期仍出現本機行情。"
        elif traded:
            kind, reason = ("traded", "官方紀錄證明當日曾有成交。") if day in observed else ("missing", "官方紀錄有個股成交，本機價格快照缺少該日。")
        else:
            kind, reason = "unknown", "尚缺足以解釋該日的市場或個股交易證據。"
        if kind in counts and kind not in {"market_open", "market_closed", "stock_traded", "interval_days"}:
            counts[kind] += 1
        if kind in sample_groups and len(sample_groups[kind]) < 8:
            sample_groups[kind].append(dict(date=day, kind=kind, reason=reason))
    source_list = []
    # Return bounded references, not whole histories or raw official payloads.
    ordered = sorted(used.values(), key=lambda r: r["fetched_at"], reverse=True)
    ordered.sort(key=lambda r: r["status"] == "accepted")
    representative = {}
    for record in ordered:
        representative.setdefault(record["spec"]["source_id"], record)
    shown = list(representative.values()) + [r for r in ordered if r not in representative.values()]
    for record in shown[:12]:
        spec = record["spec"]
        source_list.append(dict(source_id=spec["source_id"], label=spec["label"], status=record["status"],
            start=spec["start"], end=spec["end"], fetched_at=record["fetched_at"], reference=record["reference"], url=spec["url"],
            limitations=record["normalized"]["limitations"] if record["normalized"] else ["本次來源不可用；沒有採用較舊內容。", record["reason"]]))
    status = "missing" if not records else "partial" if any(counts[k] for k in ("unknown", "intraday", "conflicts", "missing")) else "available"
    action = "研究助理先針對本股票及原要求區間查證官方交易日與停復牌紀錄，再重新讀取本機結果。"
    if records and counts["missing"]:
        action = "已有官方成交證據顯示本機缺行情；可使用既有更新資料功能，再核對仍缺少的日期。"
    elif records and counts["conflicts"]:
        action = "官方狀態與行情互相衝突，交由工程核對來源與日期；目前不以衝突資料補齊資格。"
    elif records and (counts["unknown"] or counts["intraday"]):
        action = "仍未涵蓋的交易狀態或特殊休市需補強來源；不用自行填值，可繼續閱讀人工情境。"
    elif records:
        action = "已取得本區間的日期佐證；後續由工程補強完整交易狀態、公司行動及歷史可用時間。"
    samples = [sample for group in sample_groups.values() for sample in group][:8]
    return dict(contract_version=CONTRACT, mode="retrospective_local", status=status,
        requested_range=bounds, checked_at=cutoff, known_at=max((r["fetched_at"] for r in used.values()), default=None),
        historical_availability="not_asserted", counts=counts, samples=samples, sources=source_list,
        next_step=dict(owner="assistant" if not records else "program" if counts["missing"] else "engineering", action=action),
        assistant_request=f"請查證 {symbol} 在 {bounds['start']} 至 {bounds['end']} 的官方交易日與停復牌證據。先讀本機紀錄，僅更新這檔股票及原區間；保留來源、取得時間及失敗紀錄，不縮短區間，不從空名單推定正常交易，不保存研究或核准假設。這是現在回頭查證，不宣稱歷史當時已知。")
