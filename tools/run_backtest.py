import argparse
import json
import os
import re
import sys
from datetime import datetime

import pandas as pd
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.twse_collector import TWSECollector
from src.research.backtest_evaluation import (
    DEFAULT_SYMBOLS,
    assess_expanded_universe,
    clean_ohlcv,
    dataframe_sha256,
    evaluate_symbol_walk_forward,
    load_cached_ohlcv,
    normalize_symbol,
    write_walk_forward_report,
)


def parse_symbols(raw_symbols: str, legacy_symbols: list[str] | None) -> list[str]:
    candidates = legacy_symbols or [item for item in raw_symbols.split(",") if item.strip()]
    normalized = []
    for symbol in candidates:
        clean = normalize_symbol(symbol)
        if clean not in normalized:
            normalized.append(clean)
    return normalized


def zero_volume_audit(frame: pd.DataFrame) -> dict:
    if frame.empty or "volume" not in frame or "date" not in frame:
        return {"zero_volume_rows_removed": 0, "zero_volume_dates_removed": []}
    mask = pd.to_numeric(frame["volume"], errors="coerce") <= 0
    return {
        "zero_volume_rows_removed": int(mask.sum()),
        "zero_volume_dates_removed": frame.loc[mask, "date"].astype(str).tolist(),
    }


def load_universe_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    required = {"universe_id", "status", "selection_policy", "symbols", "promotion_policy"}
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"universe config missing keys: {', '.join(missing)}")
    if config["status"] != "preregistered_before_backtest":
        raise ValueError("universe config must be preregistered before backtest")
    if not config["symbols"] or any(
        not item.get("symbol") or not item.get("category")
        for item in config["symbols"]
    ):
        raise ValueError("each universe symbol requires symbol and category")
    normalized = [normalize_symbol(item["symbol"]) for item in config["symbols"]]
    if len(normalized) != len(set(normalized)):
        raise ValueError("universe config contains duplicate symbols")
    reference_symbols = set(DEFAULT_SYMBOLS)
    if reference_symbols.intersection(normalized):
        raise ValueError("expanded universe must not include reference symbols")
    gate = config["promotion_policy"].get("universe_gate", {})
    if int(gate.get("minimum_usable_symbols", 0)) < 1:
        raise ValueError("minimum_usable_symbols must be positive")
    if int(gate["minimum_usable_symbols"]) > len(normalized):
        raise ValueError("minimum_usable_symbols cannot exceed universe size")
    pass_rate = float(gate.get("minimum_gate_pass_rate", -1))
    if not 0 <= pass_rate <= 1:
        raise ValueError("minimum_gate_pass_rate must be between 0 and 1")
    if float(gate.get("maximum_symbol_mdd_pct", 0)) <= 0:
        raise ValueError("maximum_symbol_mdd_pct must be positive")
    return config


def load_snapshot_overrides(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if config.get("schema_version") != 1 or not isinstance(config.get("overrides"), list):
        raise ValueError("snapshot override file requires schema_version 1 and overrides")
    required = {
        "symbol", "date", "mode", "raw_ohlcv", "adjustment_factor",
        "factor_derivation", "factor_anchors", "source_url",
    }
    for item in config["overrides"]:
        missing = required - set(item)
        if missing:
            raise ValueError(f"snapshot override missing keys: {', '.join(sorted(missing))}")
        if item["mode"] != "insert_missing_adjusted_ohlcv":
            raise ValueError("only insert_missing_adjusted_ohlcv is supported")
        if not str(item["source_url"]).startswith("https://www.twse.com.tw/"):
            raise ValueError("snapshot override source_url must be an official TWSE URL")
        if float(item["adjustment_factor"]) <= 0:
            raise ValueError("snapshot override adjustment_factor must be positive")
        if not item["factor_anchors"]:
            raise ValueError("snapshot override requires factor_anchors")
    return config


def apply_snapshot_overrides(
    snapshots: dict[str, pd.DataFrame],
    provenance: dict[str, dict],
    override_config: dict,
) -> None:
    for item in override_config["overrides"]:
        symbol = normalize_symbol(item["symbol"])
        if symbol not in snapshots or snapshots[symbol].empty:
            raise ValueError(f"override target snapshot is unavailable: {symbol}")
        frame = snapshots[symbol].copy()
        date = str(item["date"])
        if date in set(frame["date"].astype(str)):
            raise ValueError(f"override refuses to replace existing row: {symbol} {date}")
        raw = item["raw_ohlcv"]
        factor = float(item["adjustment_factor"])
        for anchor in item["factor_anchors"]:
            anchor_date = str(anchor["date"])
            matching_anchor = frame.loc[frame["date"].astype(str) == anchor_date]
            if matching_anchor.empty:
                raise ValueError(f"override factor anchor is unavailable: {symbol} {anchor_date}")
            observed_ratio = (
                float(matching_anchor.iloc[0]["close"])
                / float(anchor["official_raw_close"])
            )
            if abs(observed_ratio - factor) > 0.000001:
                raise ValueError(
                    f"override adjustment factor is stale: {symbol} {anchor_date}"
                )
        row = {
            "symbol": symbol,
            "date": date,
            "open": float(raw["open"]) * factor,
            "high": float(raw["high"]) * factor,
            "low": float(raw["low"]) * factor,
            "close": float(raw["close"]) * factor,
            "volume": float(raw["volume"]),
        }
        snapshots[symbol] = clean_ohlcv(
            pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
        )
        audit_record = {
            "date": date,
            "mode": item["mode"],
            "source_url": item["source_url"],
            "adjustment_factor": factor,
            "factor_derivation": item["factor_derivation"],
            "factor_anchors": item["factor_anchors"],
            "raw_ohlcv": raw,
            "adjusted_row_sha256": dataframe_sha256(pd.DataFrame([row])),
        }
        provenance.setdefault(symbol, {}).setdefault("snapshot_overrides", []).append(
            audit_record
        )
        provenance[symbol]["normalized_snapshot_sha256"] = dataframe_sha256(
            snapshots[symbol]
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="台股研究模式：離線固定快照、walk-forward 與基準策略比較"
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="逗號分隔標的，預設 ^TWII,0050.TW,2330.TW",
    )
    parser.add_argument(
        "--universe-config",
        default=None,
        help="預註冊擴大驗證 YAML；標的與驗證日期由此檔案固定。",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        dest="legacy_symbols",
        help="單一標的相容參數，可重複指定",
    )
    parser.add_argument("--start", default="2015-01-01", help="第一個驗證窗最早日期")
    parser.add_argument(
        "--data-start",
        default="2014-01-01",
        help="資料快照起始日，應早於驗證開始日以供指標暖機",
    )
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="研究截止日期")
    parser.add_argument(
        "--cash",
        type=float,
        default=10_000_000.0,
        help="每個驗證窗初始模擬資金；預設 10,000,000 元以降低整張不可買偏誤",
    )
    parser.add_argument("--validation-months", type=int, default=12, help="每個驗證窗月數")
    parser.add_argument("--warmup-bars", type=int, default=233, help="驗證窗前指標暖機筆數")
    parser.add_argument("--min-validation-bars", type=int, default=120, help="有效驗證窗最少交易日")
    parser.add_argument("--db-path", default="data/cache.db", help="SQLite 快取路徑")
    parser.add_argument("--output-dir", default="reports/backtest", help="報告輸出根目錄")
    parser.add_argument(
        "--snapshot-dir",
        default=None,
        help="既有報告目錄；直接讀取 data/*.csv 與 data_provenance.csv",
    )
    parser.add_argument(
        "--snapshot-overrides",
        default=None,
        help="逐筆、可稽核的官方資料補值 YAML；只允許新增缺失日期。",
    )
    parser.add_argument("--run-id", default=None, help="自訂可重現執行識別字；僅允許英數、底線與連字號")
    parser.add_argument("--refresh", action="store_true", help="從資料來源完整重抓研究區間並更新快取")
    parser.add_argument(
        "--no-sensitivity",
        action="store_true",
        help="停用訓練窗參數敏感度（適合快速診斷）",
    )
    parser.add_argument("--json", action="store_true", help="終端輸出精簡 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cash <= 0:
        parser.error("--cash 必須大於 0")
    if args.validation_months <= 0 or args.warmup_bars < 1 or args.min_validation_bars < 1:
        parser.error("驗證窗、暖機筆數與最少驗證筆數必須為正數")
    if args.run_id and not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        parser.error("--run-id 僅允許英數、底線與連字號")
    if args.data_start > args.start:
        parser.error("--data-start 不得晚於 --start")
    if args.snapshot_dir and args.refresh:
        parser.error("--snapshot-dir 與 --refresh 不得同時使用")

    universe_config = None
    if args.universe_config:
        if args.legacy_symbols:
            parser.error("--universe-config 不可與 --symbol 併用")
        try:
            universe_config = load_universe_config(args.universe_config)
        except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as exc:
            parser.error(str(exc))
        symbols = [normalize_symbol(item["symbol"]) for item in universe_config["symbols"]]
        selection = universe_config["selection_policy"]
        args.data_start = selection["data_start"]
        args.start = selection["validation_start"]
        args.end = selection["validation_end"]
        configured_overrides = selection.get("snapshot_override_file")
        if configured_overrides:
            resolved_overrides = os.path.join(
                os.path.dirname(os.path.abspath(args.universe_config)),
                configured_overrides,
            )
            if args.snapshot_overrides and os.path.abspath(args.snapshot_overrides) != resolved_overrides:
                parser.error("--snapshot-overrides must match the preregistered universe config")
            args.snapshot_overrides = resolved_overrides
        if args.data_start > args.start or args.start > args.end:
            parser.error("universe config dates must satisfy data_start <= validation_start <= validation_end")
    else:
        symbols = parse_symbols(args.symbols, args.legacy_symbols)
    if not symbols:
        parser.error("至少需要一個標的")

    snapshots = {}
    provenance = {}
    required_symbols = list(symbols)
    if "^TWII" not in required_symbols:
        required_symbols.append("^TWII")

    if args.snapshot_dir:
        snapshot_root = os.path.abspath(args.snapshot_dir)
        provenance_path = os.path.join(snapshot_root, "data_provenance.csv")
        provenance_frame = (
            pd.read_csv(provenance_path)
            if os.path.exists(provenance_path)
            else pd.DataFrame()
        )
        for symbol in required_symbols:
            safe_symbol = symbol.replace("^", "index-").replace(".", "-")
            csv_path = os.path.join(snapshot_root, "data", f"{safe_symbol}.csv")
            if not os.path.exists(csv_path):
                snapshots[symbol] = pd.DataFrame()
                provenance[symbol] = {
                    "provider": "fixed_report_snapshot",
                    "symbol": symbol,
                    "status": "missing_snapshot",
                }
                continue
            raw_df = pd.read_csv(csv_path)
            snapshots[symbol] = clean_ohlcv(raw_df)
            matching = (
                provenance_frame[provenance_frame["symbol"] == symbol]
                if not provenance_frame.empty and "symbol" in provenance_frame.columns
                else pd.DataFrame()
            )
            if matching.empty:
                source_contract = {
                    "provider": "fixed_report_snapshot",
                    "symbol": symbol,
                }
            else:
                source_contract = {
                    key: (None if pd.isna(value) else value)
                    for key, value in matching.iloc[0].to_dict().items()
                }
            actual_hash = dataframe_sha256(snapshots[symbol])
            expected_hash = source_contract.get("normalized_snapshot_sha256")
            if expected_hash and actual_hash != expected_hash:
                print(
                    f"快照雜湊不符：{symbol} expected={expected_hash} actual={actual_hash}",
                    file=sys.stderr,
                )
                return 3
            source_contract["snapshot_reused_from"] = snapshot_root
            source_contract["normalized_snapshot_sha256"] = actual_hash
            raw_zero_audit = zero_volume_audit(raw_df)
            source_contract.setdefault(
                "zero_volume_rows_removed",
                raw_zero_audit["zero_volume_rows_removed"],
            )
            source_contract.setdefault(
                "zero_volume_dates_removed",
                raw_zero_audit["zero_volume_dates_removed"],
            )
            provenance[symbol] = source_contract
    elif args.refresh:
        collector = TWSECollector(db_path=args.db_path)
        for symbol in required_symbols:
            raw_df, source_contract = collector.fetch_research_snapshot(
                symbol,
                start_date=args.data_start,
                end_date=args.end,
            )
            if not raw_df.empty and "symbol" not in raw_df.columns:
                raw_df.insert(0, "symbol", symbol)
            snapshots[symbol] = clean_ohlcv(raw_df)
            source_contract.update(zero_volume_audit(raw_df))
            source_contract["normalized_snapshot_sha256"] = dataframe_sha256(
                snapshots[symbol]
            ) if not snapshots[symbol].empty else None
            provenance[symbol] = source_contract
    else:
        for symbol in required_symbols:
            raw_df = load_cached_ohlcv(
                args.db_path,
                symbol,
                start_date=args.data_start,
                end_date=args.end,
            )
            snapshots[symbol] = clean_ohlcv(raw_df)
            provenance[symbol] = {
                "provider": "local_sqlite_cache",
                "official_exchange_source": False,
                "symbol": symbol,
                "requested_start": args.data_start,
                "requested_end_inclusive": args.end,
                "actual_start": str(raw_df["date"].min()) if not raw_df.empty else None,
                "actual_end": str(raw_df["date"].max()) if not raw_df.empty else None,
                "row_count": int(len(raw_df)),
                "adjustment_contract": "legacy_cache_unknown",
                "dataset_sha256": None,
                **zero_volume_audit(raw_df),
            }

    if args.snapshot_overrides and not args.snapshot_dir:
        try:
            override_config = load_snapshot_overrides(args.snapshot_overrides)
            apply_snapshot_overrides(snapshots, provenance, override_config)
        except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as exc:
            parser.error(str(exc))

    evaluations = []
    for symbol in symbols:
        clean_df = snapshots[symbol]
        evaluation = evaluate_symbol_walk_forward(
            symbol,
            clean_df,
            initial_cash=args.cash,
            requested_start=args.start,
            requested_end=args.end,
            validation_months=args.validation_months,
            warmup_bars=args.warmup_bars,
            min_validation_bars=args.min_validation_bars,
            benchmark_df=snapshots.get("^TWII"),
            data_provenance=provenance.get(symbol),
            sensitivity_enabled=not args.no_sensitivity,
        )
        evaluations.append(evaluation)

    valid = [item for item in evaluations if item["status"] == "available"]
    if not valid:
        message = {
            "status": "insufficient_data",
            "reason": "沒有標的具備足夠資料建立 walk-forward 驗證窗",
            "evaluations": evaluations,
        }
        print(json.dumps(message, ensure_ascii=False, indent=2) if args.json else message["reason"])
        return 2

    universe_assessment = (
        assess_expanded_universe(evaluations, universe_config)
        if universe_config is not None
        else None
    )
    run_dir = write_walk_forward_report(
        evaluations,
        snapshots,
        output_dir=args.output_dir,
        run_timestamp=args.run_id,
        universe_assessment=universe_assessment,
    )

    terminal_summary = {
        "status": "success",
        "mode": "walk_forward_historical_research",
        "execution_capability": "simulated_orders_only",
        "report_dir": str(run_dir.resolve()),
        "universe_assessment": universe_assessment,
        "symbols": [
            {
                "symbol": item["symbol"],
                "status": item["status"],
                "dataset_sha256": item["dataset_sha256"],
                "data_range": {
                    "start": item["data_quality"].get("start_date"),
                    "end": item["data_quality"].get("end_date"),
                    "rows": item["data_quality"].get("row_count", 0),
                },
                "aggregates": item["aggregates"],
            }
            for item in evaluations
        ],
    }
    if args.json:
        print(json.dumps(terminal_summary, ensure_ascii=False, indent=2))
    else:
        print("=" * 78)
        print("台股 Walk-forward 歷史研究完成｜所有訂單皆為虛擬模擬")
        print("=" * 78)
        for item in evaluations:
            strategy = item["aggregates"].get("tu_strategy", {})
            buy_hold = item["aggregates"].get("buy_and_hold", {})
            print(
                f"{item['symbol']}: {item['status']} | "
                f"策略 {strategy.get('compounded_return_pct', 'N/A')}% | "
                f"買進持有 {buy_hold.get('compounded_return_pct', 'N/A')}% | "
                f"視窗 {strategy.get('window_count', 0)}"
            )
        print(f"報告：{run_dir.resolve()}")
        print("僅供個人研究與決策參考，不構成投資建議或真實委託指令。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
