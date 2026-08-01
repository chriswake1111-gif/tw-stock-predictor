import os
import sys
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.analysis_service import analyze_symbol, normalize_symbol
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
    title="台股市場研究與決策支援 API",
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

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "system": "Anti-Gravity TU-Predictor",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/analysis/{symbol}")
def get_analysis(symbol: str):
    analysis, _ = analyze_symbol(symbol, db_path="data/cache.db")
    if analysis.get("status") != "available":
        raise HTTPException(status_code=404, detail=f"無法獲取標的 {symbol} 的 K 線數據")
    return analysis

# 掛載靜態網頁端點
static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../ui_alert/web"))
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
