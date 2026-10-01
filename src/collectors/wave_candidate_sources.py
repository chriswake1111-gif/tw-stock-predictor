"""Pinned, bounded TWSE inputs for present-day candidate research.

Raw source bytes are retained by the package repository. This module never
fetches a caller URL, imports financial rows, or asserts historical availability.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from src.collectors.wave_session_sources import plan_requests, parse_source, interval

VERSION = "wave-candidate-sources-1"
EVENT_SOURCES = {
    "exright": "https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
    "reduction": "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU",
    "denomination": "https://www.twse.com.tw/rwd/zh/change/TWTB8U",
}
WEEKLY_URL = "https://www.twse.com.tw/zh/products/system/trading.html"
# Reviewed primary announcement. No empty event list or cross-market proxy.
CLOSURES = {
    "2026-07-10": ("https://www.twse.com.tw/rwd/zh/news/newsDetail?id=8a8216d69ef76943019f46cb86bf0111",
                   "臺灣證券交易所集中交易市場115年7月10日休市一天",
                   "/news/news/tsecnews/8a8216d69ef76943019f46cb86ae0110.pdf"),
}
EVENT_FIELDS = {
    "exright": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息", "漲停價格", "跌停價格", "開盤競價基準", "減除股利參考價", "詳細資料", "最近一次申報資料 季別/日期", "最近一次申報每股 (單位)淨值", "最近一次申報每股 (單位)盈餘"],
    "reduction": ["恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價", "漲停價格", "跌停價格", "開盤競價基準", "除權參考價", "減資原因", "詳細資料"],
    "denomination": ["恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價", "漲停價格", "跌停價格", "開盤競價基準", "詳細資料"],
}


def event_spec(source_id, symbol, start, end):
    interval(start, end)
    if not re.fullmatch(r"[1-9]\d{3}\.TW", symbol):
        raise ValueError("wave_candidate_twse_common_stock_required")
    return dict(source_id=source_id, symbol=symbol, start=start, end=end,
                url=EVENT_SOURCES[source_id]+"?"+urlencode(dict(startDate=start.replace("-", ""), endDate=end.replace("-", ""), response="json")))


def plan(symbol, start, end):
    if not re.fullmatch(r"[1-9]\d{3}\.TW", symbol):
        raise ValueError("wave_candidate_twse_common_stock_required")
    specs = plan_requests(symbol, start, end)
    specs += [event_spec(key, symbol, start, end) for key in EVENT_SOURCES]
    specs.append(dict(source_id="weekly_rule", symbol=symbol, start=start, end=end, url=WEEKLY_URL))
    for closed, (url, _, _) in CLOSURES.items():
        if start <= closed <= end:
            specs.append(dict(source_id="closure:"+closed, symbol=symbol, start=start, end=end, url=url))
    return specs


def allowed_source_url(url):
    if url == WEEKLY_URL or url in {v[0] for v in CLOSURES.values()}:
        return True
    try:
        parts = urlsplit(url)
        if parts.fragment or parts.username or parts.password:
            return False
        source = next(k for k, v in EVENT_SOURCES.items() if v == parts.scheme+"://"+parts.netloc+parts.path)
        params = parse_qs(parts.query, strict_parsing=True, keep_blank_values=True)
        if set(params) != {"startDate", "endDate", "response"} or any(len(v) != 1 for v in params.values()):
            return False
        a, b = [datetime.strptime(params[k][0], "%Y%m%d").date().isoformat() for k in ("startDate", "endDate")]
        return event_spec(source, "2330.TW", a, b)["url"] == url
    except (ValueError, KeyError, TypeError, StopIteration):
        return False


def number(value):
    try:
        result = Decimal(str(value).strip().replace(",", ""))
        if not result.is_finite():
            raise ValueError("official_number_invalid")
        return result
    except InvalidOperation as exc:
        raise ValueError("official_number_invalid") from exc


def day(value):
    match = re.fullmatch(r"(\d{3})[年/](\d{2})[月/](\d{2})日?", str(value))
    if match:
        return date(int(match[1])+1911, int(match[2]), int(match[3])).isoformat()
    return date.fromisoformat(value).isoformat()


def parse_events(spec, payload):
    source = spec["source_id"]
    if payload.get("stat") != "OK" or payload.get("fields") != EVENT_FIELDS[source]:
        raise ValueError("official_event_schema_changed")
    echo = payload.get("params") if source == "denomination" else payload
    start_key = "startDate" if source == "denomination" else "strDate"
    if not echo or echo.get(start_key) != spec["start"].replace("-", "") or echo.get("endDate") != spec["end"].replace("-", ""):
        raise ValueError("official_event_scope_mismatch")
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("official_event_rows_invalid")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(EVENT_FIELDS[source]):
            raise ValueError("official_event_row_invalid")
        date_value = day(row[0])
        if not spec["start"] <= date_value <= spec["end"]:
            raise ValueError("official_event_date_out_of_scope")
        if row[1] != spec["symbol"].split(".")[0]:
            continue
        if date_value in seen:
            raise ValueError("official_event_duplicate")
        seen.add(date_value)
        previous, reference = number(row[3]), number(row[4])
        if not previous > 0 or not reference > 0:
            raise ValueError("official_event_price_invalid")
        result.append(dict(date=date_value, previous=str(previous), reference=str(reference),
                           kind=row[6] if source == "exright" else source,
                           amount=str(number(row[5])) if source == "exright" else None))
    return result


def parse_stock(spec, payload):
    parse_source(spec, payload)
    rows = payload["data"]
    if type(payload.get("total")) is not int or payload["total"] != len(rows) or not rows:
        raise ValueError("official_stock_count_required")
    notes = " ".join(payload.get("notes", []))
    if "不比價" not in notes or "變更面額" not in notes:
        raise ValueError("official_stock_price_semantics_missing")
    result = []
    for row in rows:
        values = [number(row[i]) for i in (3, 4, 5, 6, 1)]
        opening, high, low, close, volume = values
        if not (0 < low <= opening <= high and low <= close <= high and volume > 0 and volume == int(volume)):
            raise ValueError("official_stock_invalid_ohlcv")
        result.append(dict(date=day(row[0]), **dict(zip(("open", "high", "low", "close", "volume"), map(str, values))),
                           change=str(row[7]).strip(), note=str(row[9])))
    if [r["date"] for r in result] != sorted(r["date"] for r in result):
        raise ValueError("official_stock_order_invalid")
    return result
