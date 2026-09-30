"""Read-only, bounded diagnostics. Passing a check never authorizes a wave."""
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from src.collectors.daily_public_data import parse_daily_public_dataset
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.universe_repository import UniverseRepository
from src.services.daily_public_data_service import DailyPublicDataService, PRICE_PARSER_VERSION

CONTRACT = "wave_qualification_v1"
DATASET = "TaiwanStockPrice"
MAX_ROWS = 5000
MAX_DAYS = 1100
SAMPLE_LIMIT = 8


def enabled():
    return all(os.getenv(key, "true").lower() == "true" for key in
               ("RESEARCH_WAVE_ASSIST_ENABLED", "RESEARCH_WAVE_QUALIFICATION_ENABLED"))


def check(key, title, state, reason, text, impact, *, owner="engineering", evidence=(), counts=None, samples=()):
    unique_evidence = list({(e["kind"], e["reference"]): e for e in evidence}.values())
    return dict(id=key, title=title, status=state, reason_code=reason, reason=text,
                impact=impact, owner=owner, evidence=unique_evidence[:SAMPLE_LIMIT],
                counts=counts or {}, samples=[str(s)[:40] for s in list(samples)[:SAMPLE_LIMIT]])


def requested_range(url, symbol):
    """Interpret a stored locator only. Never resolve or fetch its URL."""
    parsed = urlsplit(url)
    values = parse_qs(parsed.query, strict_parsing=True)
    expected = {"dataset": DATASET, "data_id": parse_canonical_symbol(symbol)[1]}
    if (parsed.scheme != "https" or parsed.netloc != "api.finmindtrade.com" or
            parsed.path != "/api/v4/data" or parsed.fragment or
            any(values.get(k) != [v] for k, v in expected.items())):
        raise ValueError("source_binding_mismatch")
    bounds = [values.get(k, []) for k in ("start_date", "end_date")]
    if any(len(v) != 1 for v in bounds):
        raise ValueError("requested_range_unknown")
    start, end = (date.fromisoformat(v[0]) for v in bounds)
    if start > end or (end - start).days > MAX_DAYS:
        raise ValueError("requested_range_unsupported")
    return dict(start=start.isoformat(), end=end.isoformat())


def price_quality(rows, raw_rows, excluded, bounds):
    invalid = []
    days = []
    for row in rows:
        try:
            day = date.fromisoformat(row["date"]).isoformat()
            days.append(day)
            numbers = [row[k] for k in ("open", "high", "low", "close", "volume")]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numbers):
                raise ValueError()
            opening, high, low, close, volume = numbers
            if not (0 < low <= opening <= high and low <= close <= high and volume > 0 and volume == int(volume)):
                raise ValueError()
            if bounds and not bounds["start"] <= day <= bounds["end"]:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            invalid.append(str(row.get("date", "日期缺失"))[:40])
    duplicates = [d for d, n in Counter(str(r.get("date")) for r in raw_rows).items() if n > 1]
    duplicates += [d for d, n in Counter(days).items() if n > 1]
    failed = bool(invalid or duplicates or excluded)
    return check("prices", "價格基本品質", "failed" if failed else "passed" if rows else "unknown",
                 "invalid_price_rows" if failed else "price_structure_checked" if rows else "no_prices",
                 "含異常價格、重複日期、零量或已排除資料。" if failed else
                 "已核對這份快照的日期、價格與成交股數基本結構。" if rows else "尚無可檢查的價格。",
                 "基本結構通過不代表交易日完整或價格已還原。", owner="program" if not rows else "engineering",
                 counts=dict(rows=len(rows), invalid=len(invalid), duplicate_dates=len(set(duplicates)), excluded=len(excluded)),
                 samples=sorted(set(invalid + duplicates + [str(r.get("date", "日期缺失")) for r in excluded])))


def calendar_quality(rows, bounds, calendars, lifecycle, operational, cutoff):
    """Missing official session proof stays unknown; weekdays are never invented."""
    if not bounds:
        return check("sessions", "交易日與交易狀態", "unknown", "range_unknown", "要求區間尚未確認。", "不能判定整段行情是否完整。")
    by_day = {}
    for row in calendars:
        by_day.setdefault(row["trade_date"], []).append(row)
    observed = {r["date"] for r in rows if isinstance(r.get("date"), str)}
    missing, unknown, conflicts, suspended, outside_listing = [], [], [], [], []
    references = []
    start, end = map(date.fromisoformat, (bounds["start"], bounds["end"]))
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        proofs = by_day.get(day, [])
        references.extend(dict(kind="calendar_revision", reference=r["calendar_revision_id"]) for r in proofs)
        if not proofs or any(r["status"] != "available" or not r.get("raw_eligible") for r in proofs):
            unknown.append(day)
            continue
        trading = {r["session_status"] in {"trading", "special"} for r in proofs}
        if len(trading) != 1:
            conflicts.append(day)
            continue
        if not next(iter(trading)):
            if day in observed:
                conflicts.append(day)
            continue
        # Date-only events cannot prove intraday transitions. Reuse the existing
        # conservative event policy and don't resurrect a revoked event.
        state = UniverseRepository._compose_event_state(lifecycle, operational,
            cutoff=min(cutoff, day + "T15:59:59Z"))
        for key in ("lifecycle_event_id", "operational_event_id"):
            if state.get(key):
                references.append(dict(kind=key, reference=state[key]))
        listing, operation = state.get("listing_status"), state.get("trading_state")
        if listing == "delisted":
            outside_listing.append(day)
            if day in observed:
                conflicts.append(day)
        elif operation == "suspended":
            suspended.append(day)
            if day in observed:
                conflicts.append(day)
        elif listing != "listed" or operation != "normal":
            unknown.append(day)
        elif day not in observed:
            missing.append(day)
    failed = bool(missing or conflicts)
    return check("sessions", "交易日與交易狀態", "failed" if failed else "unknown" if unknown else "passed",
        "session_conflict_or_missing" if failed else "session_evidence_incomplete" if unknown else "sessions_checked",
        "有已知缺行情或交易狀態衝突。" if failed else "尚缺完整區間的交易日、上市或停復牌證據。" if unknown else "要求區間的交易日及交易狀態已逐日核對。",
        "不跨越不明缺口判定轉折；已知停牌與缺行情分開記錄。", evidence=references,
        counts=dict(interval_days=(end-start).days+1, missing=len(missing), unknown=len(unknown), conflicts=len(conflicts),
                    suspended=len(suspended), outside_listing=len(outside_listing)),
        samples=sorted(set(missing + conflicts + unknown)))


class WaveQualificationService:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def get(self, symbol, *, cutoff=None):
        symbol = symbol.strip().upper()
        parse_canonical_symbol(symbol)
        result = dict(contract_version=CONTRACT, enabled=enabled(), symbol=symbol)
        if not result["enabled"]:
            return result
        cutoff = normalize_utc_timestamp(cutoff or utc_now_timestamp(), "knowledge_cutoff_at")
        result.update(checked_at=cutoff, knowledge_cutoff_at=cutoff, snapshot=None, requested_range=None,
                      actual_range=None, status="insufficient_data", headline="尚無本機價格快照可檢查。",
                      checks=[], notices=[], automatic_candidates_eligible=False,
                      next_step=dict(owner="program", action="使用既有更新資料功能取得歷史價量，再重新檢查。"))
        try:
            path = Path(self.db_path).resolve()
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                return self._read(conn, symbol, cutoff, result)
        except (sqlite3.Error, ValueError, KeyError, TypeError, OverflowError):
            result.update(status="unavailable", headline="暫時無法檢查：本機讀取或快照完整性檢查未完成。",
                          snapshot=None, requested_range=None, actual_range=None, checks=[], notices=[],
                          next_step=dict(owner="program", action="重新檢查本機資料；沒有改用較舊快照。"))
            return result

    def _read(self, conn, symbol, cutoff, result):
        service = DailyPublicDataService(self.db_path)
        row = service.select_snapshot(conn, symbol, DATASET, cutoff)
        if row is None:
            result["checks"] = [check(key, title, "unknown", "price_snapshot_missing",
                "尚無本機價格快照，無法開始這項資料檢查。", "目前沒有足夠依據產生自動波段候選。",
                owner="program" if key in {"source", "prices"} else "engineering") for key, title in (
                    ("source", "來源與完整性"), ("prices", "價格基本品質"), ("sessions", "交易日與交易狀態"),
                    ("basis", "價格比較基礎"), ("availability", "資料可知時間"), ("confirmation", "轉折確認資格"))]
            return result
        if len(row["raw_json"]) > 8 * 1024 * 1024 or len(row["normalized_json"]) > 8 * 1024 * 1024:
            raise ValueError("snapshot_too_large")
        data = service.decode_snapshot(row)
        raw = json.loads(row["raw_json"])
        if not isinstance(data, dict) or not isinstance(raw, dict):
            raise ValueError("snapshot_object_required")
        rows = data.get("rows", [])
        raw_rows = raw.get("data", [])
        excluded = data.get("excluded_rows", [])
        if (any(not isinstance(v, list) for v in (rows, raw_rows, excluded)) or
                max(len(rows), len(raw_rows), len(excluded)) > MAX_ROWS or
                not all(isinstance(r, dict) for r in rows + raw_rows + excluded)):
            raise ValueError("price_rows_invalid_or_unbounded")
        bounds = None
        binding_ok = True
        try:
            bounds = requested_range(row["source_url"], symbol)
        except ValueError:
            binding_ok = False
        _, code = parse_canonical_symbol(symbol)
        binding_ok = binding_ok and data.get("source") == "FinMind" and data.get("dataset") == DATASET and data.get("symbol") == code
        identity = UniverseRepository(self.db_path, auto_migrate=False).contexts_for_symbols_with_connection(
            conn, canonical_symbols=[symbol], knowledge_cutoff_at=cutoff)[symbol]
        venue = "TWSE" if symbol.endswith(".TW") else "TPEX"
        if identity.get("identity_status") == "resolved" and identity.get("venue") != venue:
            binding_ok = False
        refs = [dict(kind="price_snapshot", reference=row["snapshot_id"])]
        source_state = "failed" if not binding_ok else "passed" if identity.get("identity_status") == "resolved" and row["parser_version"] == PRICE_PARSER_VERSION else "unknown"
        source = check("source", "來源與完整性", source_state,
            "source_binding_mismatch" if not binding_ok else "source_checked" if source_state == "passed" else "identity_or_parser_unverified",
            "來源或股票綁定與要求不符。" if not binding_ok else "快照雜湊與來源綁定已核對。" if source_state == "passed" else "快照雜湊已核對，市場身分或解析版本仍待確認。",
            "內容完整不等於行情具備自動波段資格。", evidence=refs)
        quality = price_quality(rows, raw_rows, excluded, bounds)
        try:
            parsed = parse_daily_public_dataset(DATASET, raw, symbol, row["observed_at"])
            if parsed.get("rows") != rows or parsed.get("excluded_rows", []) != data.get("excluded_rows", []):
                raise ValueError("raw_normalized_mismatch")
        except ValueError:
            quality.update(status="failed", reason_code="raw_price_validation_failed", reason="原始價格重新檢查未通過，或與標準化內容不一致。")
        valid_dates = []
        for item in rows:
            try:
                valid_dates.append(date.fromisoformat(item["date"]).isoformat())
            except (ValueError, TypeError, KeyError):
                pass
        actual = dict(start=min(valid_dates), end=max(valid_dates)) if valid_dates else None
        if bounds:
            calendars = self._calendars(conn, venue, bounds, cutoff)
        else:
            calendars = []
        instrument = (identity.get("identity") or {}).get("instrument_id")
        lifecycle, operational = [], []
        if instrument:
            for table, target in (("universe_lifecycle_events", lifecycle), ("universe_operational_state_events", operational)):
                target.extend(dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE instrument_id=? AND available_at<=? AND ingested_at<=?", (instrument, cutoff, cutoff)))
        sessions = calendar_quality(rows, bounds, calendars, lifecycle, operational, cutoff)
        basis = check("basis", "價格比較基礎", "unknown", "corporate_action_coverage_unverified",
            "目前是未還原價格，尚無完整公司行動與還原依據。", "不能把除權息、減資或分割造成的跳動當成波段；未查到事件不等於沒有事件。", evidence=refs)
        if data.get("adjusted") or data.get("price_basis") not in (None, "unadjusted"):
            basis.update(status="failed", reason_code="price_basis_conflict", reason="價格口徑聲明與此來源解析契約不一致。")
        temporal = check("availability", "資料可知時間", "unknown", "row_publication_times_unproven",
            "保留整份快照取得時間；尚無逐筆行情在歷史當時可用的完整證據。", "今天取得的歷史行情不能當成過去當時已知資料。", evidence=refs)
        observed = normalize_utc_timestamp(row["observed_at"], "observed_at")
        observed_day = datetime.fromisoformat(observed.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
        if data.get("observed_at") != row["observed_at"] or observed > cutoff or (actual and actual["end"] > observed_day):
            temporal.update(status="failed", reason_code="observation_time_conflict", reason="來源觀測時間不一致，或含取得時間之後的行情日期。")
        detector = check("confirmation", "轉折確認資格", "unknown", "experimental_detector_only",
            "現有轉折偵測只進行隔離驗證，正式自動候選仍關閉。", "通過固定資料重播不代表能判定第幾浪，也不會產生正式價格目標。",
            evidence=[dict(kind="project_rule", reference="PIVOT-EXP-01:1.0.0")])
        attempt = conn.execute("SELECT status,reason,checked_at FROM daily_public_attempts WHERE symbol=? AND dataset=? AND checked_at<=? ORDER BY checked_at DESC,attempt_id DESC LIMIT 1", (symbol, DATASET, cutoff)).fetchone()
        notices = ["只檢查本機已取得內容；本次不更新來源。", "自動候選的資格不會取代或撤銷已有的人工假設。"]
        if attempt and attempt["status"] != "available":
            notices.append("最近資料更新未完成，目前保留先前取得的快照與日期。")
        if actual and actual["end"] < cutoff[:10]:
            notices.append("行情資料日早於檢查日期；最新交易日完整性尚未確認，不能當作目前最新行情。")
        result.update(snapshot=dict(snapshot_id=row["snapshot_id"], source="FinMind", parser_version=row["parser_version"],
            raw_sha256=row["raw_sha256"], normalized_sha256=row["normalized_sha256"], observed_at=row["observed_at"], source_url=row["source_url"]),
            requested_range=bounds, actual_range=actual, checks=[source, quality, sessions, basis, temporal, detector], notices=notices,
            status="quality_warning", headline="部分檢查已有結果；自動候選仍缺資格證據。",
            next_step=dict(owner="engineering", action="先補齊交易日、交易狀態及價格比較證據；可繼續閱讀人工候選或保存部分研究。"))
        return result

    @staticmethod
    def _calendars(conn, venue, bounds, cutoff):
        rows = conn.execute("""WITH visible_raw AS (
            SELECT raw.*, ROW_NUMBER() OVER (
                PARTITION BY resource_id,logical_revision_key
                ORDER BY ingested_at DESC,available_at DESC,raw_resource_revision_id DESC) AS rank_no
            FROM raw_resource_revisions raw WHERE available_at<=? AND ingested_at<=?
            ) SELECT c.*, r.quality_status, r.eligibility_status, r.provider_id,
            r.available_at AS raw_available_at, r.ingested_at AS raw_ingested_at
            FROM trading_calendar_revisions c LEFT JOIN visible_raw r
            ON r.raw_resource_revision_id=c.raw_resource_revision_id AND r.rank_no=1
            WHERE c.market IN (?, 'TW') AND c.trade_date BETWEEN ? AND ?
              AND c.available_at<=? AND c.ingested_at<=?
            ORDER BY c.revision_number DESC,c.available_at DESC,c.ingested_at DESC,c.calendar_revision_id DESC""",
            (cutoff, cutoff, venue, bounds["start"], bounds["end"], cutoff, cutoff)).fetchall()
        latest = {}
        for row in rows:
            value = dict(row)
            value["raw_eligible"] = (value["eligibility_status"] == "eligible" and value["quality_status"] == "fresh"
                                     and value["provider_id"] == ("tpex" if value["market"] == "TPEX" else "twse")
                                     # Shared holiday evidence cannot prove an exchange's trading session.
                                     and (value["market"] != "TW" or value["session_status"] in {"holiday", "no_trading"})
                                     and value["raw_available_at"] is not None and value["raw_available_at"] <= cutoff
                                     and value["raw_ingested_at"] is not None and value["raw_ingested_at"] <= cutoff)
            latest.setdefault((value["market"], value["trade_date"]), value)
        return list(latest.values())
