import os
import sys
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.analysis_service import analyze_symbol, normalize_symbol
from src.api.routes.v2_valuation import router as v2_valuation_router
from src.api.routes.research_workflow import router as research_workflow_router
from src.api.workflow_security import ResearchBoundaryMiddleware
from src.domain.model_status import LEGACY_V1_MODEL_METADATA
from src.scheduler import AutoScheduler
from src.services.rule_registry import RuleRegistry

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

# Added after legacy CORS so this route-local boundary is the outer middleware.
app.add_middleware(ResearchBoundaryMiddleware)

app.include_router(v2_valuation_router)
app.include_router(research_workflow_router)

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "system": "Anti-Gravity TU-Predictor",
        "timestamp": datetime.now().isoformat(),
        **LEGACY_V1_MODEL_METADATA,
    }


@app.get("/api/model-rules")
def list_model_rules():
    registry = RuleRegistry()
    return {
        "model_version": "2.0.0-registry",
        "official_affiliation": False,
        "rules": [rule.to_dict() for rule in registry.list_rules()],
    }


@app.get("/api/model-rules/{rule_id}")
def get_model_rule(rule_id: str):
    try:
        return RuleRegistry().describe(rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.get("/api/analysis/{symbol}")
def get_analysis(symbol: str):
    analysis, _ = analyze_symbol(symbol, db_path="data/cache.db")
    if analysis.get("status") != "available":
        raise HTTPException(status_code=404, detail=f"無法獲取標的 {symbol} 的 K 線數據")
    return {**analysis, **LEGACY_V1_MODEL_METADATA}

class SPAStaticFiles(StaticFiles):
    """Serve bookmarkable React routes while leaving API routing untouched."""

    _ASSET_SUFFIXES = {
        ".css", ".gif", ".ico", ".jpeg", ".jpg", ".js", ".json", ".map",
        ".png", ".svg", ".txt", ".webmanifest", ".webp", ".woff", ".woff2",
    }

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix.lower() in self._ASSET_SUFFIXES:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and Path(path).suffix.lower() not in self._ASSET_SUFFIXES:
            return await super().get_response("index.html", scope)
        return response


# Phase 9 build is preferred when present; legacy static UI remains the fallback.
project_root = Path(__file__).resolve().parents[2]
phase9_static_dir = project_root / "frontend" / "dist"
legacy_static_dir = project_root / "src" / "ui_alert" / "web"
static_dir = phase9_static_dir if phase9_static_dir.exists() else legacy_static_dir
if static_dir.exists():
    app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="web")
