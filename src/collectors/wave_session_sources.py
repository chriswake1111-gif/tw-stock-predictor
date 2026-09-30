"""Pinned official response contracts for retrospective session evidence only.

No inference from an absent row, weekday, stock price, or an empty event list.
Raw prices are inspected only for positive trading activity, never imported.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from src.domain.universe import parse_canonical_symbol

VERSION = "wave-session-sources-1"
MAX_DAYS = 1100
MAX_REQUESTS = 120
MAX_BODY = 2 * 1024 * 1024
SOURCES = {
    "twse_market": ("TWSE", "上市市場每日成交", "https://www.twse.com.tw/exchangeReport/FMTQIK", "month"),
    "twse_stock": ("TWSE", "上市個股日成交", "https://www.twse.com.tw/exchangeReport/STOCK_DAY", "month"),
    "twse_holiday": ("TWSE", "上市市場開休市日期", "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule", "year"),
    "twse_halt": ("TWSE", "上市暫停及恢復交易", "https://www.twse.com.tw/rwd/zh/afterTrading/TWTAWU", "year"),
    "tpex_stock": ("TPEX", "上櫃個股日成交", "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock", "month"),
    "tpex_holiday": ("TPEX", "上櫃市場開休市日期", "https://www.tpex.org.tw/www/zh-tw/bulletin/tradingDate", "year"),
    "tpex_halt": ("TPEX", "上櫃暫停及恢復交易", "https://www.tpex.org.tw/www/zh-tw/bulletin/sprcHis", "year"),
}


def interval(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a.isoformat() != start or b.isoformat() != end or not 0 <= (b-a).days <= MAX_DAYS:
        raise ValueError("wave_session_range_invalid")
    return a, b


def request_spec(source_id, symbol, start, end):
    parse_canonical_symbol(symbol)
    if not re.fullmatch(r"\d{4}\.(TW|TWO)", symbol):
        raise ValueError("wave_session_common_stock_symbol_required")
    a, b = interval(start, end)
    venue = "TWSE" if symbol.endswith(".TW") else "TPEX"
    source_venue, label, base, period = SOURCES[source_id]
    if source_venue != venue or (period == "month" and start[:7] != end[:7]) or start[:4] != end[:4]:
        raise ValueError("wave_session_source_scope_mismatch")
    code = symbol.split(".")[0]
    if source_id in {"twse_market", "twse_stock"}:
        params = {"date": a.strftime("%Y%m01"), "response": "json"}
        if source_id == "twse_stock":
            params["stockNo"] = code
    elif source_id == "twse_holiday":
        params = {"date": a.strftime("%Y0101"), "response": "json"}
    elif source_id == "twse_halt":
        params = {"startDate": a.strftime("%Y%m%d"), "endDate": b.strftime("%Y%m%d"), "response": "json"}
    elif source_id == "tpex_stock":
        params = {"code": code, "date": a.strftime("%Y/%m/01"), "response": "json"}
    else:
        params = {"date": str(a.year), "response": "json"}
        if source_id == "tpex_halt":
            params["cate"] = "1"
    return dict(source_id=source_id, symbol=symbol, venue=venue, label=label,
                start=start, end=end, url=base+"?"+urlencode(params), parser_version=VERSION)


def plan_requests(symbol, start, end):
    a, b = interval(start, end)
    prefix = "twse" if symbol.endswith(".TW") else "tpex"
    specs = []
    cursor = a.replace(day=1)
    while cursor <= b:
        last = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        for source in (["twse_market", "twse_stock"] if prefix == "twse" else ["tpex_stock"]):
            specs.append(request_spec(source, symbol, max(a, cursor).isoformat(), min(b, last).isoformat()))
        cursor = last + timedelta(days=1)
    for year in range(a.year, b.year+1):
        for suffix in ("holiday", "halt"):
            specs.append(request_spec(prefix+"_"+suffix, symbol,
                max(a, date(year, 1, 1)).isoformat(), min(b, date(year, 12, 31)).isoformat()))
    if len(specs) > MAX_REQUESTS:
        raise ValueError("wave_session_request_limit")
    return specs


def allowed_source_url(url):
    """Exact paths and a canonical bounded parameter grammar, never arbitrary URLs."""
    try:
        parsed = urlsplit(url)
        if parsed.fragment or parsed.username or parsed.password:
            return False
        params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if any(len(v) != 1 for v in params.values()):
            return False
        query = {k: v[0] for k, v in params.items()}
        base = parsed.scheme+"://"+parsed.netloc+parsed.path
        source = next((key for key, spec in SOURCES.items() if spec[2] == base), None)
        if not source:
            return False
        year = query.get("date", query.get("startDate", ""))[:4]
        if not year.isdigit() or not 1990 <= int(year) <= 2200:
            return False
        symbol = query.get("stockNo", query.get("code", "2330"))+(".TW" if source.startswith("twse_") else ".TWO")
        if source == "twse_halt":
            start, end = (datetime.strptime(query[k], "%Y%m%d").date().isoformat() for k in ("startDate", "endDate"))
        elif SOURCES[source][3] == "month":
            start = datetime.strptime(query["date"], "%Y/%m/%d" if source == "tpex_stock" else "%Y%m%d").date().isoformat()
            end = start
        else:
            start = end = year+"-01-01"
        return request_spec(source, symbol, start, end)["url"] == url
    except (KeyError, ValueError, TypeError, StopIteration):
        return False


def _day(value):
    text = str(value).strip()
    if re.fullmatch(r"\d{3}/\d{2}/\d{2}", text):
        text = str(int(text[:3])+1911)+text[3:].replace("/", "-")
    return date.fromisoformat(text).isoformat()


def _positive(value):
    try:
        number = Decimal(str(value).replace(",", ""))
        return number.is_finite() and number > 0
    except InvalidOperation:
        return False


def _rows(table, fields, count_key=None):
    if table.get("fields") != fields or not isinstance(table.get("data"), list):
        raise ValueError("official_schema_changed")
    rows = table["data"]
    if len(rows) > 10000 or any(not isinstance(r, list) or len(r) != len(fields) for r in rows):
        raise ValueError("official_rows_invalid")
    if count_key and (type(table.get(count_key)) is not int or table[count_key] != len(rows)):
        raise ValueError("official_response_incomplete")
    return rows


class _CalendarCells(HTMLParser):
    """Read inert cell text only. Skip rowspan-dependent and non-simple dates."""
    def __init__(self):
        super().__init__()
        self.rows, self.cells, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.handle_endtag("tr")
        if tag in {"td", "th"}:
            self.handle_endtag("td")
            self.cell = ""
        elif tag in {"br", "p"} and self.cell is not None:
            self.cell += " "

    def handle_endtag(self, tag):
        if tag in {"td", "th", "tr"} and self.cell is not None:
            self.cells.append(self.cell.strip())
            self.cell = None
        if tag == "tr" and self.cells:
            self.rows.append(self.cells)
            self.cells = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data


def parse_source(spec, payload):
    """Only extract explicitly supported positive facts. Missing is always unknown."""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        raise ValueError("official_response_not_ok")
    source, year = spec["source_id"], int(spec["start"][:4])
    code = spec["symbol"].split(".")[0]
    facts, events = [], []
    limitations = ["查詢成功不代表整段日期或所有停牌原因已完整覆蓋；未列出的日期不推定正常交易。"]
    def fact(day, kind):
        parsed = _day(day)
        if source.endswith(("stock", "market")) and parsed[:7] != spec["start"][:7]:
            raise ValueError("official_month_mismatch")
        if spec["start"] <= parsed <= spec["end"]:
            facts.append(dict(date=parsed, kind=kind))

    if source in {"twse_market", "twse_stock", "tpex_stock"}:
        if payload.get("date") != spec["start"].replace("-", "")[:6]+"01":
            raise ValueError("official_month_mismatch")
        if source == "twse_market":
            rows = _rows(payload, ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"])
            kind = "market_open"
        elif source == "twse_stock":
            if not re.search(r"\s"+re.escape(code)+r"\s", str(payload.get("title", ""))):
                raise ValueError("official_symbol_mismatch")
            rows = _rows(payload, ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數", "註記"], "total")
            kind = "stock_traded"
        else:
            if payload.get("code") != code or not isinstance(payload.get("tables"), list) or len(payload["tables"]) != 1:
                raise ValueError("official_symbol_or_table_mismatch")
            rows = _rows(payload["tables"][0], ["日 期", "成交張數", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"], "totalCount")
            kind = "stock_traded"
        days = [_day(r[0]) for r in rows]
        if len(days) != len(set(days)):
            raise ValueError("official_duplicate_dates")
        for row in rows:
            has_prices = kind == "market_open" or all(_positive(v) for v in row[3:7])
            if _positive(row[1]) and _positive(row[2]) and has_prices:
                fact(row[0], kind)
        limitations.append("僅證明來源所列交易範圍曾有成交，不證明全日正常交易、價格已還原或與第三方高低價相符。")
    elif source == "twse_holiday":
        if payload.get("date") != str(year)+"0101" or payload.get("queryYear") != year:
            raise ValueError("official_year_mismatch")
        for day, name, reason in _rows(payload, ["日期", "名稱", "說明"], "total"):
            text = str(name)+" "+str(reason)
            if any(word in text for word in ("放假", "補假", "無交易", "休市")) and "最後交易" not in str(name):
                fact(day, "market_closed")
        limitations.append("開休市表只採明列休市日期；未自行補週末或一般開市日，臨時公告另待證據。")
    elif source == "tpex_holiday":
        # endDate is the site's latest selectable year (see the official page's
        # convertHtmlTableToJson); it is NOT the returned table's requested year.
        latest = str(payload.get("endDate", ""))
        if not re.fullmatch(r"\d{4}0101", latest) or int(latest[:4]) < year or not isinstance(payload.get("data"), dict):
            raise ValueError("official_year_mismatch")
        html = payload["data"].get("html")
        title = re.search(r"中華民國\s*(\d{3})\s*年有價證券櫃檯買賣市場開（休）市日期表", html) if isinstance(html, str) else None
        if not title or int(title[1])+1911 != year:
            raise ValueError("official_calendar_schema_changed")
        parser = _CalendarCells()
        parser.feed(html)
        parser.handle_endtag("tr")
        for cells in parser.rows:
            # Full independent rows only: no HTML execution or inference across rowspans.
            if len(cells) != 4 or any(term in cells[0] for term in ("債券", "最後交易", "開始交易")):
                continue
            match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", cells[1])
            if match and any(word in cells[3] for word in ("放假", "補假", "休市", "無交易")):
                fact(date(year, int(match[1]), int(match[2])).isoformat(), "market_closed")
        limitations.append("僅解析明確單日、完整儲存格的股票市場休市列；跨日文字、合併儲存格及債券專屬列不推算。")
    else:
        fields = ["編號", "證券代號", "證券名稱", "暫停交易日期", "暫停交易時間", "恢復交易日期", "恢復交易時間"]
        if source == "tpex_halt":
            if payload.get("date") != str(year) or not isinstance(payload.get("tables"), list) or len(payload["tables"]) != 1:
                raise ValueError("official_year_mismatch")
            fields = ["編號", "有價證券類別", "有價證券代號", "有價證券名稱", *fields[3:]]
            rows = _rows(payload["tables"][0], fields, "totalCount")
            rows = [[r[0], *r[2:]] for r in rows if r[1] == "上櫃股票"]
        else:
            params = payload.get("params", {})
            if params.get("startDate") != spec["start"].replace("-", "") or params.get("endDate") != spec["end"].replace("-", ""):
                raise ValueError("official_halt_range_mismatch")
            rows = _rows(payload, fields)
        for row in rows:
            if row[1] != code:
                continue
            for pos, kind in ((3, "suspended"), (5, "resumed")):
                if row[pos] in ("", "-", "--", None):
                    continue
                day = _day(row[pos])
                clock = str(row[pos+1]).strip()
                if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", clock):
                    events.append(dict(date=day, time=None, kind=kind))
                else:
                    parsed_time = datetime.strptime(clock, "%H:%M:%S" if clock.count(":") == 2 else "%H:%M").strftime("%H:%M:%S")
                    events.append(dict(date=day, time=parsed_time, kind=kind))
        limitations.append("暫停及恢復交易名單不是所有停止買賣原因的完整名冊；沒有事件不代表從未停牌。")
    if len({(r["date"], r["kind"]) for r in facts}) != len(facts):
        raise ValueError("official_duplicate_facts")
    return dict(parser_version=VERSION, facts=facts, events=events, absence_proves_normal=False,
                historical_availability="not_asserted", limitations=limitations)
