"""Independently refreshed official market observations for current viewing."""
import math
from datetime import date

from src.collectors.cbc_collector import CBCCollector
from src.collectors.market_turnover_collector import MarketTurnoverCollector
from src.services.daily_public_data_service import DailyPublicDataService

SOURCES = {
    "TWSE_TURNOVER": ("twse.market-turnover", MarketTurnoverCollector.TWSE_OPENAPI_URL),
    "TPEX_TURNOVER": ("tpex.market-turnover", MarketTurnoverCollector.TPEX_OPENAPI_URL),
    "CBC_M1B": ("cbc.m1b", CBCCollector.OFFICIAL_M1B_URL),
}


class DailyMarketDataService(DailyPublicDataService):
    def source_metadata(self, dataset):
        source = {"TWSE_TURNOVER": "TWSE", "TPEX_TURNOVER": "TPEx", "CBC_M1B": "CBC"}[dataset]
        return {"source": source, "official_exchange_source": source != "CBC"}

    datasets = tuple(SOURCES)

    def request_spec(self, symbol, dataset, today):
        return SOURCES[dataset]

    def parse(self, dataset, payload, symbol, observed):
        rows = []
        if dataset == "CBC_M1B":
            data = payload.get("data", {})
            source_rows = data.get("dataSets", []) if "dataSets" in data else payload.get("result", {}).get("data", [])
            periods = {CBCCollector._cbc_period(row[0])[0]: observed for row in source_rows}
            parsed = CBCCollector.parse_official_m1b(payload, periods, observed)
            for observation in parsed["observations"]:
                item = observation.canonical_payload()
                rows.append({"date": item["data_date"], "period": item["period"], "value": item["value_twd"]})
        else:
            if not isinstance(payload, list):
                raise ValueError("market_payload_must_be_list")
            key = "TradeValue" if dataset == "TWSE_TURNOVER" else "TradeAmount"
            seen = set()
            for item in payload:
                day = MarketTurnoverCollector._iso_date(item["Date"])
                date.fromisoformat(day)
                if day in seen:
                    raise ValueError("duplicate_market_date")
                seen.add(day)
                value = float(str(item[key]).replace(",", ""))
                if not math.isfinite(value) or value < 0:
                    raise ValueError("invalid_turnover")
                rows.append({"date": day, "value": value})
        from src.domain.valuation import parse_aware_timestamp
        from zoneinfo import ZoneInfo
        observed_day = parse_aware_timestamp(observed, "observed").astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
        if any(row["date"] > observed_day for row in rows):
            raise ValueError("future_market_period")
        return {"source": "CBC" if dataset == "CBC_M1B" else "TWSE" if dataset == "TWSE_TURNOVER" else "TPEx",
                "official_exchange_source": dataset != "CBC_M1B", "dataset": dataset,
                "observed_at": observed, "unit": "TWD", "publication_instant_verified": False,
                "status": "available" if rows else "insufficient_data", "rows": sorted(rows, key=lambda r: r["date"])}
