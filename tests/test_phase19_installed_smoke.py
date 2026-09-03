"""Phase 19 Installed Data Synchronization / Clean-Machine and Upgrade Smoke Proofs."""

from __future__ import annotations

import gc
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.collectors.installed_egress_client import InstalledEgressClient
from src.domain.installed_data_operations import (
    InstalledOperationStatus,
    InstalledOperationType,
    InstalledReadiness,
)
from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.repositories.migration_runner import (
    ADDITIONAL_MIGRATION_IDS,
    MIGRATION_IDS,
    apply_valuation_migration,
    migration_manifest,
)
from src.repositories.research_workflow_repository import ResearchWorkflowRepository
from src.repositories.universe_repository import UniverseRepository
from src.runtime.database_state import DatabaseState, classify_database
from src.runtime.manifest import (
    build_internal_manifest,
    validate_internal_manifest,
    write_manifest,
)
from src.runtime.paths import RuntimePaths
from src.runtime.settings import RuntimeSettings
from src.runtime.startup_coordinator import StartupCoordinator
from src.services.installed_data_sync_service import InstalledDataSyncService
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import seed_raw_provenance
from tests.test_phase14_eod_repository_service import (
    _classification,
    _observation,
    _raw,
    _source,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _create_packaged_settings(tmp_path: Path) -> tuple[RuntimeSettings, StartupCoordinator]:
    resource_root = tmp_path / "resource"
    shutil.copytree(REPO_ROOT / "config", resource_root / "config")
    shutil.copytree(REPO_ROOT / "migrations", resource_root / "migrations")
    shutil.copytree(REPO_ROOT / "frontend" / "dist", resource_root / "frontend" / "dist")

    manifest = build_internal_manifest(
        resource_root,
        app_version="19.0.0-test",
        build_sha="phase19-test-build",
        migration_records=migration_manifest(resource_root),
        frontend_dist=resource_root / "frontend" / "dist",
        model_rules_path=resource_root / "config" / "model_rules.yaml",
    )
    manifest_path = write_manifest(resource_root / "package-manifest.json", manifest)
    validate_internal_manifest(manifest, resource_root, manifest_path=manifest_path)

    user_root = tmp_path / "user"
    environment = {
        "TW_STOCK_PACKAGED": "true",
        "TW_STOCK_APP_VERSION": "19.0.0-test",
        "TW_STOCK_BUILD_SHA": "phase19-test-build",
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
        "TW_STOCK_PORT": "8000",
    }
    paths = RuntimePaths.from_environment(
        environment,
        project_root=REPO_ROOT,
        packaged_install_root=tmp_path / "install",
        packaged_resource_root=resource_root,
        packaged_user_root=user_root,
    )
    settings = RuntimeSettings.from_environment(environment, paths=paths)
    coordinator = StartupCoordinator(settings)
    return settings, coordinator


def test_scenario_a_clean_machine_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario A: Clean machine bootstrap in packaged mode."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        tmp_path = Path(temp_dir)
        settings, coordinator = _create_packaged_settings(tmp_path)
        monkeypatch.setenv("DATABASE_PATH", str(settings.paths.database_path))
        monkeypatch.setenv("EOD_DB_PATH", str(settings.paths.database_path))
        monkeypatch.setenv("UNIVERSE_DB_PATH", str(settings.paths.database_path))

        startup_res = coordinator.prepare()
        assert startup_res.status == "ready"
        assert startup_res.database is not None
        assert startup_res.database.state is DatabaseState.KNOWN_V2_CURRENT

        app = create_app(settings=settings, startup_result=startup_res)
        client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

        # 1. Initial status before sync
        res_initial = client.get("/api/v2/data-operations/status")
        assert res_initial.status_code == 200
        initial_data = res_initial.json()
        assert initial_data["readiness"] == InstalledReadiness.NOT_INITIALIZED.value
        assert initial_data["is_syncing"] is False

        # 2. Execute sync with mock official data
        mock_egress = MagicMock(spec=InstalledEgressClient)
        eod_json = (FIXTURES / "eod_twse_stock_day_all.json").read_bytes()
        isin_html = (FIXTURES / "eod_isin_supported.html").read_bytes()

        calendar_json = json.dumps([
            {"Name": "國曆新年開始交易日", "Date": "1150102", "Weekday": "五", "Description": "國曆新年開始交易"},
            {"Name": "農曆春節最後交易日", "Date": "1150211", "Weekday": "三", "Description": "最後交易日"},
            {"Name": "交易日", "Date": "1150827", "Weekday": "四", "Description": "正常交易日"},
        ], ensure_ascii=False).encode("utf-8")

        twse_universe_json = json.dumps([
            {"公司代號": "2330", "公司名稱": "台灣積體電路製造股份有限公司"},
        ], ensure_ascii=False).encode("utf-8")
        tpex_universe_json = json.dumps([
            {"SecuritiesCompanyCode": "8069", "CompanyName": "元太科技工業股份有限公司"},
        ], ensure_ascii=False).encode("utf-8")

        twse_turnover_json = (FIXTURES / "twse_fmtqik_openapi.json").read_bytes()
        tpex_turnover_json = (FIXTURES / "tpex_daily_trading_index_openapi.json").read_bytes()
        cbc_json = (FIXTURES / "cbc_ef15m01_response.json").read_bytes()

        def custom_fetch(url: str, **kwargs):
            if "isin" in url:
                return 200, isin_html, {}
            if "STOCK_DAY_ALL" in url:
                return 200, eod_json, {}
            if "holidaySchedule" in url or "holiday" in url:
                return 200, calendar_json, {}
            if "t187ap03_L" in url:
                return 200, twse_universe_json, {}
            if "mopsfin_t187ap03_O" in url:
                return 200, tpex_universe_json, {}
            if "FMTQIK" in url:
                return 200, twse_turnover_json, {}
            if "tpex_daily_trading_index" in url:
                return 200, tpex_turnover_json, {}
            if "EF15M01" in url:
                return 200, cbc_json, {}
            return 200, b"[]", {}

        mock_egress.fetch.side_effect = custom_fetch

        sync_svc = InstalledDataSyncService(
            db_path=str(settings.paths.database_path),
            egress_client=mock_egress,
            runtime_instance_id="test_instance_scenario_a",
        )
        op_id = sync_svc.execute_sync(
            operation_type=InstalledOperationType.SYNC.value,
            target_symbols=["2330.TW"],
        )

        # 3. Status after sync
        res_after = client.get("/api/v2/data-operations/status")
        assert res_after.status_code == 200
        after_data = res_after.json()
        assert after_data["readiness"] == InstalledReadiness.READY.value
        assert after_data["market_context_summary"]["latest_eod_date"] == "2026-08-27"

        res_analysis = client.get("/api/v2/analysis/2330.TW")
        assert res_analysis.status_code == 200
        analysis_data = res_analysis.json()
        assert analysis_data.get("symbol") == "2330.TW" or "symbol" in analysis_data or "status" in analysis_data

        import sqlite3
        conn = sqlite3.connect(str(settings.paths.database_path))
        try:
            fks = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
        assert fks == []
        db_classification = classify_database(str(settings.paths.database_path))
        assert db_classification.state is DatabaseState.KNOWN_V2_CURRENT

        del client
        del app
        del sync_svc
        gc.collect()


def test_scenario_b_upgraded_database_migration_21(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario B: Existing database upgraded with Migration 21 preserving data and backups."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        tmp_path = Path(temp_dir)
        settings, coordinator = _create_packaged_settings(tmp_path)
        db_path = settings.paths.database_path
        settings.paths.ensure_user_dirs()

        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("EOD_DB_PATH", str(db_path))
        monkeypatch.setenv("UNIVERSE_DB_PATH", str(db_path))

        # Build database and seed identity directly into db_path
        apply_valuation_migration(str(db_path))
        seed_raw_provenance(db_path)
        universe = UniverseRepository(str(db_path), guard=UniverseWriteGuard(True))
        context = UniverseOperatorContext("operator", "run-1", "lock-1", "audit-1")
        anchor = universe.allocate_instrument(
            venue="TWSE",
            official_code="2330",
            source_identity="twse:2330:v1",
            first_observed_at="2026-08-21T00:00:00Z",
            source_reference="fixture",
            context=context,
        )
        universe.add_revision(
            instrument_id=anchor["instrument_id"],
            resource_id="twse-universe-master",
            logical_revision_key="master",
            revision_number=1,
            payload={
                "venue": "TWSE",
                "official_code": "2330",
                "canonical_symbol": "2330.TW",
                "display_name": "台積電",
                "security_type": "股票",
                "fetched_at": "2026-08-21T00:01:00Z",
                "received_at": "2026-08-21T00:01:00Z",
                "ingested_at": "2026-08-21T00:02:00Z",
                "available_at": "2026-08-21T00:01:00Z",
                "source_reference": "phase14 fixture",
                "status": "accepted",
                "freshness_status": "current",
                "freshness_mode": "official_cadence_window",
                "current_complete": True,
                "coverage_complete": True,
                "raw_resource_revision_id": "raw-phase13-twse_universe_master",
                "raw_payload_sha256": hashlib.sha256(b"phase13:twse-universe-master").hexdigest(),
            },
            context=context,
            idempotency_key="identity-revision-2330",
        )

        repo = EodCloseRepository(str(db_path))
        classification = _classification(repo, db_path, at="2026-08-27T07:00:00Z")
        raw, raw_hash = _raw(
            db_path,
            "twse.eod.stock_day_all",
            "twse-universe-official",
            "rev-1",
            "2026-08-27T08:00:00Z",
        )
        source = _source(repo, raw, raw_hash, date="2026-08-27", at="2026-08-27T08:00:00Z")
        _observation(
            repo,
            raw=raw,
            raw_hash=raw_hash,
            source=source,
            classification=classification,
            anchor=anchor,
            at="2026-08-27T08:05:00Z",
            close="1005.00",
            volume="10000",
            instrument_revision_id=None,
        )

        # Seed Phase 17 Queue membership
        ResearchWorkflowRepository(str(db_path)).add_membership("2330.TW")

        # Run startup coordinator prepare (which applies migration 21 and runs recovery)
        startup_res = coordinator.prepare()
        assert startup_res.status == "ready"
        assert startup_res.database is not None
        assert startup_res.database.state is DatabaseState.KNOWN_V2_CURRENT

        # Preflight / API read verification (BC-2)
        app = create_app(settings=settings, startup_result=startup_res)
        client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

        # BC-2: GET /api/v2/market-context/eod-close/as-of/2330.TW?knowledge_cutoff_at=2026-08-27T16:00:00Z
        res_eod = client.get(
            "/api/v2/market-context/eod-close/as-of/2330.TW?knowledge_cutoff_at=2026-08-27T16:00:00Z"
        )
        assert res_eod.status_code == 200
        eod_dto = res_eod.json()
        assert eod_dto["status"] == "available"
        assert eod_dto["canonical_symbol"] == "2330.TW"
        assert eod_dto["product_scope"] == "supported_stock"
        assert float(eod_dto["close_value"]) == 1005.00
        assert eod_dto["provider"] == "TWSE"
        assert eod_dto["resource_key"] == "twse.eod.stock_day_all"
        assert eod_dto["source_trade_date"] == "2026-08-27"
        assert len(eod_dto["reason_codes"]) == 0

        # BC-3: GET /api/v2/analysis/2330.TW
        res_v2_analysis = client.get(
            "/api/v2/analysis/2330.TW?knowledge_cutoff_at=2026-08-27T16:00:00Z"
        )
        assert res_v2_analysis.status_code == 200

        # P1-8: Phase 17 Queue consumption verification
        res_daily = client.get(
            "/api/v2/research/daily-context?market_date=2026-08-27&knowledge_cutoff_at=2026-08-27T16:00:00Z"
        )
        assert res_daily.status_code == 200
        daily_payload = res_daily.json()
        assert "items" in daily_payload

        # UI bookmarkable route
        res_ui = client.get("/research/daily")
        assert res_ui.status_code == 200

        del client
        del app
        del repo
        del universe
        gc.collect()
