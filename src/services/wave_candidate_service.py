"""Present research candidates only; never historical signals or approvals.

The official bundle is independent of the unadjusted FinMind qualification.
Only adjacent low/high pivots are paired. No integrated target-price method.
"""
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import unescape
import json
import os
import re
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd

from src.collectors.wave_candidate_sources import CLOSURES, parse_events, parse_stock, number
from src.collectors.wave_session_sources import parse_source
from src.domain.analysis_snapshot import sha256_json
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import utc_now_timestamp
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.repositories.wave_candidate_repository import WaveCandidateRepository
from src.repositories.wave_session_repository import connection
from src.services.daily_public_data_service import DailyPublicDataService
from src.services.wave_qualification_service import requested_range

VERSION = "present-wave-candidates-1"
RULE = "WAVE-CANDIDATE-01"
BASIS_RULE = "PRICE-BASIS-01"
# Explicitly reviewed pilot scope, not a search for a profitable interval.
# Expanding this list requires a separately reviewed source/coverage contract.
PILOT_SCOPE = dict(symbol="2330.TW", start="2025-09-29", end="2026-09-30")
LIMITATIONS = [
    "這是現在取得資料後的歷史波段研究，不代表過去當時已知，不供歷史回測或即時訊號使用。",
    "只選相鄰的已確認低點與高點；不判定第幾浪，不預測價格，不依潛在報酬排序。",
    "價格以檢查截止日為共同基準，並非起訖日實際成交價；換算因子不改動成交股數。",
    "核對每日價格比較中斷及已支援現金除息，不宣稱公司從未有其他事件或盤中停牌。",
    "新價格、公司行動、來源修訂或撤銷後需重查；已有人工核准不會被本功能自動撤銷。",
]


def enabled():
    return all(os.getenv(k, "true").lower() == "true" for k in
               ("RESEARCH_GUIDANCE_ENABLED", "RESEARCH_WAVE_ASSIST_ENABLED", "RESEARCH_WAVE_CANDIDATES_ENABLED"))


def price_binding(conn, db_path, symbol):
    service = DailyPublicDataService(db_path)
    row = service.select_snapshot(conn, symbol, "TaiwanStockPrice", utc_now_timestamp())
    if row is None:
        return None
    service.decode_snapshot(row)
    return dict(snapshot_id=row["snapshot_id"], bounds=requested_range(row["source_url"], symbol))


def qualify(package):
    """Recompute every check from source bodies, never from supplied pass flags."""
    bounds = package["range"]
    start, end = bounds["start"], bounds["end"]
    market, holidays, closes, bars, events, halts = set(), set(), set(), [], {}, []
    checks = []
    errors = []
    if package["status"] != "accepted":
        return [], [dict(id="source", title="來源版本", status="failed", reason="最新證據版本失敗或已撤銷，不能退回舊版本。")]
    for source in package["sources"]:
        spec = source["spec"]
        key = spec["source_id"]
        try:
            if source["status"] != "accepted":
                raise ValueError("source_failed")
            observed = datetime.fromisoformat(source["fetched_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
            # This first batch accepts completed dates only; never an intraday row.
            if observed <= spec["end"]:
                raise ValueError("completed_period_not_observed")
            if key == "weekly_rule":
                text = unescape(re.sub(r"<[^>]*>", "", source["raw_text"]))
                if "集中市場交易時間為星期一至星期五" not in text:
                    raise ValueError("weekly_rule_changed")
                continue
            payload = json.loads(source["raw_text"])
            if key.startswith("closure:"):
                closed = key.split(":", 1)[1]
                _, title, path = CLOSURES[closed]
                item = payload["tables"][0]
                if (payload.get("stat") != "ok" or item["title"] != title or
                        item["fields"] != ["text", "html", "pdf", "lang"] or item["data"][2] != path):
                    raise ValueError("closure_announcement_changed")
                closes.add(closed)
            elif key in {"exright", "reduction", "denomination"}:
                events[key] = parse_events(spec, payload)
            else:
                normalized = parse_source(spec, payload)
                if key == "twse_stock":
                    bars += parse_stock(spec, payload)
                if key == "twse_market":
                    market.update(f["date"] for f in normalized["facts"])
                if key == "twse_holiday":
                    holidays.update(f["date"] for f in normalized["facts"])
                if key == "twse_halt":
                    halts += normalized["events"]
        except (ValueError, KeyError, TypeError, IndexError):
            errors.append(key)
    checks.append(dict(id="source", title="官方來源與完整性", status="failed" if errors else "passed",
                       reason="來源讀取、期間或格式未通過："+"、".join(errors[:8]) if errors else "各月、年度、公司行動及交易規則的原始內容與查詢範圍已核對。"))
    if errors:
        return [], checks
    bars.sort(key=lambda r: r["date"])
    if len({r["date"] for r in bars}) != len(bars):
        return [], checks+[dict(id="prices", title="價格基本品質", status="failed", reason="來源含重複日期，未組成候選。")]
    active = [r for r in bars if start <= r["date"] <= end]
    by_date = {r["date"]: r for r in active}
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    missing = []
    conflict = sorted(market & (holidays | closes))
    for offset in range((b-a).days+1):
        current = a+timedelta(days=offset)
        value = current.isoformat()
        if value in market:
            if value not in by_date:
                missing.append(value)
        elif value in by_date:
            conflict.append(value)
        elif current.weekday() < 5 and value not in holidays | closes:
            missing.append(value)
    blocked_halts = [e for e in halts if start <= e["date"] <= end]
    invalid_sessions = bool(missing or conflict or blocked_halts or not active)
    checks.append(dict(id="sessions", title="完整期間與交易日期", status="failed" if invalid_sessions else "passed",
        reason=(f"未通過：缺少 {len(missing)} 日證據、{len(conflict)} 日衝突、{len(blocked_halts)} 筆停復牌事件。" if invalid_sessions else
                f"完整要求期間內 {len(active)} 個官方成交日均有價格；例行及已核對的臨時休市另有依據。"),
        samples=(missing+conflict)[:8], missing_count=len(missing)))
    by_event = {e["date"]: e for e in events["exright"]}
    problems = []
    if events["reduction"] or events["denomination"]:
        problems.append("含尚未支援的減資或變更面額")
    for event in by_event.values():
        prev, ref, amount = map(number, (event["previous"], event["reference"], event["amount"]))
        # TWSE publishes a two-decimal reference. Use that published value,
        # not the differently tick-rounded opening auction reference.
        if event["kind"] != "息" or not 0 < ref < prev or amount <= 0 or not Decimal(0) <= prev-amount-ref < Decimal("0.01"):
            problems.append("除權息種類或官方參考價無法對帳")
    seen = set()
    for i, row in enumerate(bars):
        if not start <= row["date"] <= end:
            continue
        if row["note"]:
            problems.append("價格註記尚未支援")
        event = by_event.get(row["date"])
        if row["change"].startswith("X"):
            if not event or event["kind"] != "息" or i == 0 or number(event["previous"]) != number(bars[i-1]["close"]):
                problems.append("不比價日期缺少可對帳的現金除息依據")
            seen.add(row["date"])
        elif event:
            problems.append("事件日期與不比價紀錄不符")
        elif i:
            try:
                if number(row["close"])-number(bars[i-1]["close"]) != number(row["change"]):
                    problems.append("逐日價差與前次收盤無法銜接")
            except ValueError:
                problems.append("價差無法解析")
    if seen != set(by_event):
        problems.append("事件與價格比較中斷未逐筆吻合")
    checks.append(dict(id="basis", title="同一價格比較基礎", status="failed" if problems else "passed",
        reason="；".join(sorted(set(problems))) if problems else
        f"逐日價差已銜接；{len(by_event)} 次現金除息與不比價標記、前日收盤、官方參考價逐筆吻合。"))
    checks.append(dict(id="availability", title="本次研究用途", status="passed",
                       reason="保留各來源取得時間，僅供現在研究；歷史當時可用性未取得資格。"))
    if invalid_sessions or problems:
        return [], checks
    adjusted = []
    for row in active:
        factor = Decimal(1)
        for event in by_event.values():
            if row["date"] < event["date"] <= end:
                factor *= number(event["reference"])/number(event["previous"])
        adjusted.append(dict(row, factor=str(factor), **{k: float(number(row[k])*factor) for k in ("open", "high", "low", "close")},
                             raw_low=float(row["low"]), raw_high=float(row["high"]), volume=int(number(row["volume"]))))
    return adjusted, checks


def pairs(rows):
    if not rows:
        return []
    pivots = WaveFibonacciEngine(config_path="").detect_confirmed_pivots(pd.DataFrame(rows), confirmation_bars=3, lookback=5)
    count = Counter(p.pivot_date for p in pivots)
    # Do not filter ambiguous points out first: doing so would bridge a pivot.
    return [(a, b) for a, b in zip(pivots, pivots[1:]) if
            count[a.pivot_date] == count[b.pivot_date] == 1 and a.pivot_type == "low" and b.pivot_type == "high"
            and a.pivot_index < b.pivot_index and b.pivot_price > a.pivot_price][-3:][::-1]


def candidate_identifier(package, anchors):
    return "wc_"+package["package_ref"][3:]+"_"+sha256_json(dict(package=package["package_ref"], content=package["content_sha256"], anchors=anchors, version=VERSION))


def anchor_points(rows, a, b):
    return [dict(role=role, market_date=p.pivot_date, price=p.pivot_price,
        raw_price=rows[p.pivot_index]["raw_low" if p.pivot_type == "low" else "raw_high"],
        adjustment_factor=float(rows[p.pivot_index]["factor"]), confirmed_at=p.confirmed_at)
        for p, role in ((a, "origin"), (b, "swing_end"))]


class WaveCandidateService:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def get(self, symbol):
        parse_canonical_symbol(symbol)
        result = dict(contract_version="wave_candidates_v1", symbol=symbol, enabled=enabled(), status="insufficient_data",
            headline="尚無完整的官方波段證據；可先閱讀人工候選或保存部分研究。", checked_at=utc_now_timestamp(),
            requested_range=None, actual_range=None, package_ref=None, known_at=None, basis=None,
            limitations=LIMITATIONS, checks=[], candidates=[], evidence_level="C", project_operationalization=True,
            official_affiliation=False, historical_backtest_eligible=False, rule_id=RULE, algorithm_version=VERSION)
        if not result["enabled"]:
            return result
        if symbol != PILOT_SCOPE["symbol"]:
            result.update(status="unsupported", headline="這一批先驗證台積電的指定完整期間；其他股票仍可閱讀人工候選。")
            return result
        try:
            with closing(connection(self.db_path)) as conn:
                conn.execute("BEGIN")
                package = WaveCandidateRepository.latest(conn, symbol)
                if package is None:
                    return result
                result.update(requested_range=package["range"], package_ref=package["package_ref"], known_at=package["known_at"])
                if package["range"] != {k: PILOT_SCOPE[k] for k in ("start", "end")}:
                    result.update(status="unsupported", headline="這份資料超出本批已核對的完整期間，需要先補做資料資格評估。")
                    return result
                if package["price_binding"] != price_binding(conn, self.db_path, symbol):
                    result.update(headline="本機價格版本已變更，請由研究助理重新核對官方證據。",
                                  checks=[dict(id="version", title="價格版本", status="failed", reason="證據與目前價格版本不同，舊候選已停止提供。")])
                    return result
                rows, checks = qualify(package)
                result["checks"] = checks
                if not rows:
                    result["headline"] = "證據仍有缺項，交由研究助理補查；目前不產生可採用數字。"
                    return result
                result.update(actual_range=dict(start=rows[0]["date"], end=rows[-1]["date"]),
                    basis=dict(label="官方現金除息參考價比例換算；新臺幣／股", anchor_date=package["range"]["end"]))
                for a, b in pairs(rows):
                    anchors = anchor_points(rows, a, b)
                    identifier = candidate_identifier(package, anchors)
                    result["candidates"].append(dict(candidate_id=identifier, title=f"{a.pivot_date} 低點 → {b.pivot_date} 高點",
                        anchors=anchors, confirmed_at=b.confirmed_at, known_at=package["known_at"],
                        source_summary=f"臺灣證券交易所官方每日價量及事件資料；證據版本 {package['package_ref']}", limitations=LIMITATIONS))
                result.update(status="available" if result["candidates"] else "insufficient_data",
                    headline=f"有 {len(result['candidates'])} 組可閱讀比較的波段候選，選用後仍需人工核准。" if result["candidates"] else "資料已核對，但固定規則沒有找到可配對的向上波段。")
                return result
        except (ValueError, KeyError, TypeError, IndexError, sqlite3.Error):
            result.update(status="unavailable", headline="暫時無法檢查官方證據版本；請重新讀取，不以舊候選替代。", candidates=[], checks=[])
            return result

    def saved_reference(self, symbol, candidate_id):
        """Freeze referenced inputs, independent of newer failures/revocations.

        Existing drafts already preserve their exact selected prices, rules and
        reasons. This adds the immutable source hashes, not a new selection.
        """
        parse_canonical_symbol(symbol)
        if not re.fullmatch(r"wc_[0-9a-f]{32}_[0-9a-f]{64}", candidate_id):
            raise ValueError("wave_candidate_reference_invalid")
        from src.repositories.wave_candidate_repository import decode
        with closing(connection(self.db_path)) as conn:
            row = conn.execute("SELECT * FROM wave_candidate_packages WHERE package_id=? AND symbol=?",
                               ("wp_"+candidate_id[3:35], symbol)).fetchone()
            if row is None:
                raise ValueError("wave_candidate_reference_missing")
            package = decode(row)
        rows, _ = qualify(package)
        if candidate_id not in {candidate_identifier(package, anchor_points(rows, a, b)) for a, b in pairs(rows)}:
            raise ValueError("wave_candidate_reference_invalid")
        return dict(contract_version="wave_candidate_reference_v1", record_id=candidate_id, symbol=symbol,
                    package_ref=package["package_ref"], content_sha256=package["content_sha256"],
                    requested_range=package["range"], known_at=package["known_at"],
                    basis_date=package["range"]["end"], rule_id=RULE, algorithm_version=VERSION,
                    title="人工草稿引用的自動波段候選證據", limitations=LIMITATIONS,
                    sources=[dict(source_id=s["spec"]["source_id"], url=s["spec"]["url"],
                                  fetched_at=s["fetched_at"], raw_sha256=s["raw_sha256"]) for s in package["sources"]])

    def candidate_values(self, symbol, candidate_id):
        result = self.get(symbol)
        candidate = next((c for c in result["candidates"] if c["candidate_id"] == candidate_id), None)
        if result["status"] != "available" or candidate is None:
            raise ValueError("research_evidence_changed_review_again")
        source = f"TWSE 官方不可變資料包 {result['package_ref']}；{RULE}／{VERSION}；{BASIS_RULE}；僅供目前研究"
        raw = "；".join(f"{p['market_date']} 原價 {p['raw_price']} × {p['adjustment_factor']}；確認交易日 {p['confirmed_at']}" for p in candidate["anchors"])
        rationale = (f"候選 {candidate_id}；完整期間 {result['requested_range']['start']} 至 {result['requested_range']['end']}；"
                     f"價格基準日 {result['basis']['anchor_date']}；取得時間 {result['known_at']}；{raw}；"
                     "五筆回看、三筆確認之相鄰低高點。C 級專案操作化，非杜金龍已驗證判浪。"
                     "不是歷史當時已知；不可供回測或混用其他基準價格；新事件或來源修訂需重查。人工核准僅採用本情境。")
        values = dict(rule_id="FB-04", anchors=[{k: p[k] for k in ("role", "market_date", "price")} for p in candidate["anchors"]],
                      source=source, rationale=rationale)
        return "anchor", values
