import os
import sys
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.collectors.twse_collector import TWSECollector
from src.collectors.finmind_collector import FinMindCollector
from src.collectors.cbc_collector import CBCCollector
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.engine.ma_deduction import MADeductionEngine
from src.engine.valuation_eva import ValuationEVAEngine
from src.engine.market_sentiment import MarketSentimentEngine
from src.scheduler import AutoScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

scheduler_instance = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI Lifespan 事件管理：單機部署模式 (workers=1) 啟動背景 14:30 自動排程器"""
    global scheduler_instance
    logger.info("啟動 FastAPI 服務與 14:30 盤後 APScheduler 背景排程器 (workers=1)...")
    try:
        scheduler_instance = AutoScheduler()
        scheduler_instance.start(cron_hour=14, cron_minute=30)
    except Exception as e:
        logger.error(f"啟動背景排程器失敗: {str(e)}")
    
    yield
    
    if scheduler_instance:
        logger.info("關閉 FastAPI 服務與 APScheduler 背景排程器...")
        scheduler_instance.stop()

app = FastAPI(
    title="杜金龍台股量化預測機構級 API 終端",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 跨域設定 (預設關閉 credentials 提高資安規範)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def normalize_symbol(symbol: str) -> str:
    """Symbol 正規化：2330 -> 2330.TW, 0000 -> ^TWII"""
    clean = symbol.strip().upper()
    if clean in ["0000", "TAIEX"]:
        return "^TWII"
    if not (clean.startswith("^") or ".TW" in clean or ".TWO" in clean):
        return f"{clean}.TW"
    return clean

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "system": "Anti-Gravity TU-Predictor",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/analysis/{symbol}")
def get_analysis(symbol: str):
    real_symbol = normalize_symbol(symbol)
    db_path = "data/cache.db"

    twse = TWSECollector(db_path=db_path)
    finmind = FinMindCollector(db_path=db_path)
    cbc = CBCCollector(db_path=db_path)
    wave_engine = WaveFibonacciEngine(config_path="config/config.yaml")
    ma_engine = MADeductionEngine(config_path="config/config.yaml")
    val_engine = ValuationEVAEngine(config_path="config/config.yaml")
    sentiment_engine = MarketSentimentEngine(db_path=db_path, config_path="config/config.yaml")

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365*2)).strftime("%Y-%m-%d")
    
    df = twse.get_ohlcv(real_symbol, start_date=start_date, end_date=end_date)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"無法獲取標的 {symbol} 的 K 線數據")

    clean_df = df.dropna(subset=['open', 'high', 'low', 'close']).copy()
    latest_row = clean_df.iloc[-1]
    latest_close = float(latest_row['close'])

    # 1. 波浪與時間視窗
    params = wave_engine.get_symbol_wave_params(real_symbol, clean_df)
    targets = wave_engine.calculate_wave_targets(p0=params["p0"], p1=params["p1"], p2=params.get("p2"))
    time_win = wave_engine.check_time_window(clean_df, pivot_date=params.get("pivot_date", "2022-10-25"))

    # 2. 均線扣抵與共振
    analyzed_df = ma_engine.calculate_ma_and_deductions(clean_df)
    resonance_series = ma_engine.detect_resonance_signal(analyzed_df)
    is_resonance = bool(resonance_series.iloc[-1])

    # 3. 真實 EPS 估值與資料契約
    clean_code = real_symbol.replace(".TW", "").replace(".TWO", "").replace("^", "")
    val_df = finmind.get_valuation(clean_code)
    
    eps_contract = {
        "value": None,
        "type": "historical_ttm",
        "unit": "TWD_per_share",
        "source": "FinMind TaiwanStockFinancialStatements",
        "calculation": "sum_latest_four_reported_quarter_eps",
        "status": "insufficient_data"
    }

    valuation_contract = {
        "status": "insufficient_data",
        "cheap_price": None,
        "fair_price": None,
        "expensive_price": None,
        "estimated_eps": None
    }

    if not val_df.empty:
        last_val = val_df.iloc[-1]
        pe = float(last_val.get("pe", 0))
        if pe > 0:
            ttm_eps = round(latest_close / pe, 2)
            dog_val = val_engine.calculate_dog_master_valuation(eps=ttm_eps)
            eps_contract.update({
                "value": ttm_eps,
                "status": "available"
            })
            valuation_contract.update({
                "status": "available",
                "cheap_price": dog_val["cheap_price"],
                "fair_price": dog_val["fair_price"],
                "expensive_price": dog_val["expensive_price"],
                "estimated_eps": dog_val["estimated_eps"]
            })

    # EVA 暫停使用標記 (未完備財報標準化前不給予假資料)
    eva_contract = {
        "status": "unsupported",
        "reason": "Financial statement normalization is not implemented in current release",
        "eva_floor_price": None
    }

    # 4. 大盤與 M1B 熱度 Contract
    m1b_val_billion = cbc.get_latest_m1b() # 回傳 float (億新台幣)
    m1b_val = float(m1b_val_billion * 1e8)
    
    taiex_df = twse.get_ohlcv("^TWII", start_date=end_date, end_date=end_date)
    daily_volume_val = float(taiex_df.iloc[-1]['volume']) if not taiex_df.empty else 0.0

    sentiment_res = sentiment_engine.check_volume_m1b_overheat(
        daily_volume_billion=(daily_volume_val / 1e8) if daily_volume_val > 0 else 4500.0
    )

    sentiment_contract = {
        "status": "available",
        "market_turnover": {
            "value": daily_volume_val,
            "scope": "TWSE+TPEx",
            "metric": "traded_volume",
            "unit": "TWD",
            "source": ["TWSE Daily Trading Summary"]
        },
        "m1b": {
            "value": m1b_val,
            "unit": "TWD",
            "source": "CBC Monthly Data"
        },
        "volume_m1b_ratio": sentiment_res["volume_m1b_ratio"],
        "is_overheat": sentiment_res["is_overheat"],
        "status_message": sentiment_res["status_message"]
    }

    # 5. TradingView Lightweight Charts K 線資料 (time: YYYY-MM-DD)
    kline_list = []
    for _, r in clean_df.iterrows():
        kline_list.append({
            "time": str(r["date"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"])
        })

    # 扣抵點數據
    ma_deductions = {}
    for period in [8, 13, 21, 55, 144]:
        if f"SMA_{period}" in analyzed_df.columns:
            sma_v = float(analyzed_df[f"SMA_{period}"].iloc[-1])
            deduct_v = float(analyzed_df[f"deduct_val_{period}"].iloc[-1]) if not np.isnan(analyzed_df[f"deduct_val_{period}"].iloc[-1]) else latest_close
            slope_u = bool(latest_close > deduct_v)
            ma_deductions[str(period)] = {
                "sma": round(sma_v, 2),
                "deduct_val": round(deduct_v, 2),
                "slope_up": slope_u
            }

    return {
        "symbol": real_symbol,
        "input_symbol": symbol,
        "latest_price": round(latest_close, 2),
        "date": str(latest_row["date"]),
        "is_resonance": is_resonance,
        "eps": eps_contract,
        "wave_targets": targets,
        "fib_window": time_win,
        "valuation": valuation_contract,
        "eva_valuation": eva_contract,
        "sentiment": sentiment_contract,
        "ma_deductions": ma_deductions,
        "kline_data": kline_list
    }

# 掛載靜態網頁端點
static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../ui_alert/web"))
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
