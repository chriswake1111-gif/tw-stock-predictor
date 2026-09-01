import os
import sys
import logging
import inspect
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.analysis_service import analyze_symbol, normalize_symbol
from src.api.routes.v2_valuation import router as v2_valuation_router
from src.api.routes.research_workflow import router as research_workflow_router
from src.api.routes.daily_research import router as daily_research_router
from src.api.routes.v2_universe import router as v2_universe_router
from src.api.routes.v2_eod_close import router as v2_eod_close_router
from src.api.routes.v2_eod_coverage import router as v2_eod_coverage_router
from src.api.routes.v2_market_context import router as v2_market_context_router
from src.api.routes.runtime import router as runtime_router
from src.api.workflow_security import ResearchBoundaryMiddleware
from src.api.workflow_security import ResearchSecurityConfig, parse_research_origin
from src.domain.model_status import LEGACY_V1_MODEL_METADATA
from src.scheduler import AutoScheduler
from src.services.rule_registry import RuleRegistry
from src.runtime.settings import RuntimeSettings, RuntimeConfigurationError
from src.runtime.startup_coordinator import StartupResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

scheduler_instance = None
core_router = APIRouter()

def _research_security_config(settings: RuntimeSettings) -> ResearchSecurityConfig:
    if not settings.packaged:
        return ResearchSecurityConfig.from_environment()
    if not settings.application_origin:
        raise RuntimeConfigurationError("packaged mode requires research application origin")
    parsed = parse_research_origin(settings.application_origin)
    return ResearchSecurityConfig(
        valid=True,
        allowed_origins=frozenset({parsed.origin}),
        allowed_authorities=frozenset({parsed.authority}),
    )


def _readiness_state(
    settings: RuntimeSettings,
    startup_result: StartupResult | None,
) -> dict[str, Any]:
    if startup_result is None:
        ready = not settings.packaged
        database_state = None
        reason = "development_mode" if ready else "packaged_startup_not_completed"
    else:
        ready = startup_result.ready
        database_state = (
            startup_result.database.state.value
            if startup_result.database is not None else None
        )
        reason = startup_result.failure_code or ("startup_complete" if ready else "startup_not_ready")
    return {
        "ready": ready,
        "app_version": settings.app_version,
        "build_sha": settings.build_sha,
        "origin": settings.application_origin,
        "database_state": database_state,
        "scheduler_enabled": settings.scheduler_enabled,
        "reason": reason,
    }


def create_app(
    settings: RuntimeSettings | None = None,
    *,
    startup_result: StartupResult | None = None,
    scheduler_factory: Callable[[RuntimeSettings], Any] | None = None,
) -> FastAPI:
    """Create a development or packaged app without import-time package work."""

    runtime_settings = settings or RuntimeSettings.from_environment()
    if runtime_settings.packaged:
        static_dir = runtime_settings.paths.frontend_dist
        if not static_dir.is_dir() or not (static_dir / "index.html").is_file():
            raise RuntimeConfigurationError("packaged frontend assets are missing")
    else:
        phase9_static_dir = runtime_settings.paths.resource_root / "frontend" / "dist"
        legacy_static_dir = runtime_settings.paths.resource_root / "src" / "ui_alert" / "web"
        static_dir = phase9_static_dir if phase9_static_dir.exists() else legacy_static_dir

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global scheduler_instance
        app.state.runtime_readiness = _readiness_state(runtime_settings, startup_result)
        scheduler = None
        if runtime_settings.scheduler_enabled:
            logger.info("啟動 FastAPI 服務與 14:30 盤後 APScheduler 背景排程器 (workers=1)...")
            try:
                scheduler = (
                    scheduler_factory(runtime_settings)
                    if scheduler_factory
                    else AutoScheduler(
                        db_path=str(runtime_settings.paths.database_path),
                        config_path=str(runtime_settings.paths.config_path),
                    )
                )
                scheduler.start(cron_hour=14, cron_minute=30)
                scheduler_instance = scheduler
            except Exception as exc:
                logger.error(f"啟動背景排程器失敗: {str(exc)}")
        yield
        if scheduler is not None:
            logger.info("關閉 FastAPI 服務與 APScheduler 背景排程器...")
            scheduler.stop()
            if scheduler_instance is scheduler:
                scheduler_instance = None

    app = FastAPI(
        title="台股市場研究與決策支援 API",
        version=runtime_settings.app_version,
        lifespan=lifespan,
    )
    app.state.runtime_settings = runtime_settings
    app.state.runtime_readiness = _readiness_state(runtime_settings, startup_result)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        ResearchBoundaryMiddleware,
        config=_research_security_config(runtime_settings),
    )

    app.include_router(v2_valuation_router)
    app.include_router(research_workflow_router)
    app.include_router(daily_research_router)
    app.include_router(v2_universe_router)
    app.include_router(v2_eod_close_router)
    app.include_router(v2_eod_coverage_router)
    app.include_router(v2_market_context_router)
    app.include_router(runtime_router)
    app.include_router(core_router)

    if static_dir.exists():
        app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="web")
    elif runtime_settings.packaged:
        raise RuntimeConfigurationError("packaged frontend assets are missing")
    return app

@core_router.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "system": "Anti-Gravity TU-Predictor",
        "timestamp": datetime.now().isoformat(),
        **LEGACY_V1_MODEL_METADATA,
    }


@core_router.get("/api/model-rules")
def list_model_rules():
    registry = RuleRegistry(os.getenv("MODEL_RULES_PATH", "config/model_rules.yaml"))
    return {
        "model_version": "2.0.0-registry",
        "official_affiliation": False,
        "rules": [rule.to_dict() for rule in registry.list_rules()],
    }


@core_router.get("/api/model-rules/{rule_id}")
def get_model_rule(rule_id: str):
    try:
        return RuleRegistry(os.getenv("MODEL_RULES_PATH", "config/model_rules.yaml")).describe(rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@core_router.get("/api/analysis/{symbol}")
def get_analysis(symbol: str):
    kwargs = {"db_path": os.getenv("DATABASE_PATH", "data/cache.db")}
    if "config_path" in inspect.signature(analyze_symbol).parameters:
        kwargs["config_path"] = os.getenv("CONFIG_PATH", "config/config.yaml")
    analysis, _ = analyze_symbol(symbol, **kwargs)
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


def _compatibility_settings() -> RuntimeSettings:
    """Keep the legacy import export development-only in packaged processes."""

    environment = dict(os.environ)
    environment["TW_STOCK_PACKAGED"] = "false"
    return RuntimeSettings.from_environment(environment)


# Compatibility export for existing development/test callers. Packaged entry
# points call create_app only after startup preflight and absolute settings.
app = create_app(settings=_compatibility_settings())
