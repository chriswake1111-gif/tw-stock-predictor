"""Tests for Phase 19 multi-stage pipeline orchestrator and worker service."""

from __future__ import annotations

import gc
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.collectors.installed_egress_client import InstalledEgressClient
from src.domain.installed_data_operations import (
    InstalledItemStatus,
    InstalledOperationStatus,
    InstalledOperationType,
    OperationActiveConflict,
    OperationCancelled,
)
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.services.installed_data_sync_service import InstalledDataSyncService
from src.services.installed_readiness_evaluator import evaluate_installed_readiness
from src.services.production_ingestion_service import ProductionIngestionService

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def sync_env() -> tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        db_path = str(Path(temp_dir) / "test.db")
        apply_valuation_migration(db_path)
        repo = InstalledDataOperationsRepository(db_path)

        mock_egress = MagicMock(spec=InstalledEgressClient)
        # Default mock returns
        isin_html = (FIXTURES / "eod_isin_supported.html").read_bytes()
        mock_egress.fetch.side_effect = lambda url, **kwargs: (
            (200, isin_html, {}) if "isin" in url
            else (200, b"[]", {}) if "STOCK_DAY_ALL" in url or "holiday" in url
            else (200, b"OK", {})
        )

        service = InstalledDataSyncService(
            db_path=db_path,
            egress_client=mock_egress,
            runtime_instance_id="test_instance_1",
            operation_repo=repo,
        )
        try:
            yield service, repo, db_path
        finally:
            del service
            del repo
            gc.collect()


def test_concurrent_sync_raises_conflict(
    sync_env: tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]
) -> None:
    service, repo, _ = sync_env

    # First operation created
    op_id, auth = service.create_operation_and_capability()
    assert op_id is not None

    # Attempting to start second concurrent operation fails
    with pytest.raises(OperationActiveConflict, match="is currently running"):
        service.create_operation_and_capability()


def test_cancellation_during_execution_halts_pipeline(
    sync_env: tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]
) -> None:
    service, repo, _ = sync_env

    op_id, auth = service.create_operation_and_capability()
    assert auth.is_valid("test_instance_1", "twse.trading-calendar") is True

    # Request cancel
    assert repo.request_cancel(op_id) is True

    # Next verify check halts pipeline and revokes capability
    with pytest.raises(OperationCancelled):
        service._verify_durable_running(op_id, auth)

    assert auth.revoked is True
    assert auth.is_valid("test_instance_1", "twse.trading-calendar") is False


def test_end_to_end_sync_pipeline(
    sync_env: tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]
) -> None:
    service, repo, _ = sync_env

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

    service.egress_client.fetch.side_effect = custom_fetch

    op_id = service.execute_sync(
        operation_type=InstalledOperationType.SYNC.value,
        target_symbols=["2330.TW"],
    )

    op_row = repo.get_operation_by_id(op_id)
    assert op_row is not None
    assert op_row.status == InstalledOperationStatus.SUCCEEDED.value
    assert op_row.completed_at is not None

    items = repo.list_items_by_operation(op_id)
    assert len(items) >= 4


def test_universe_ingestion_records_row_errors_and_partial_status(
    sync_env: tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]
) -> None:
    service, repo, db_path = sync_env

    # 1 valid row (2330) and 1 invalid row (missing/empty code)
    twse_universe_json = json.dumps([
        {"公司代號": "2330", "公司名稱": "台積電"},
        {"公司代號": "INVALID_CODE_EXTRA_LONG_12345", "公司名稱": "測試無效"},
    ], ensure_ascii=False).encode("utf-8")

    service.egress_client.fetch.side_effect = lambda url, **kw: (
        (200, twse_universe_json, {}) if "t187ap03_L" in url
        else (200, b"[]", {})
    )

    op_id, auth = service.create_operation_and_capability()
    try:
        service.run_stage_universe(op_id, auth)
    except Exception:
        pass

    items = repo.list_items_by_operation(op_id)
    twse_item = next((it for it in items if it.resource_id == "twse.t187ap03_L"), None)
    assert twse_item is not None
    # Must record actual status and error details without swallowing
    assert twse_item.status in (InstalledItemStatus.PARTIAL.value, InstalledItemStatus.FAILED.value)
    assert twse_item.error_detail is not None


def test_cbc_failure_records_failed_status_and_does_not_block_sync(
    sync_env: tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]
) -> None:
    service, repo, db_path = sync_env
    ProductionIngestionService(db_path)

    twse_turnover_json = (FIXTURES / "twse_fmtqik_openapi.json").read_bytes()
    tpex_turnover_json = (FIXTURES / "tpex_daily_trading_index_openapi.json").read_bytes()
    # Malformed CBC payload
    corrupt_cbc_json = b"{\"data\": {\"dataSets\": [\"corrupt_string_not_list\"]}}"

    def custom_fetch(url: str, **kwargs):
        if "FMTQIK" in url:
            return 200, twse_turnover_json, {}
        if "tpex_daily_trading_index" in url:
            return 200, tpex_turnover_json, {}
        if "EF15M01" in url:
            return 200, corrupt_cbc_json, {}
        return 200, b"[]", {}

    service.egress_client.fetch.side_effect = custom_fetch

    op_id, auth = service.create_operation_and_capability()
    # Turnover and CBC stage should succeed without raising, but record CBC as failed/partial
    service.run_stage_turnover_and_cbc(op_id, auth)

    items = repo.list_items_by_operation(op_id)
    cbc_item = next((it for it in items if it.resource_id == "cbc.m1b"), None)
    assert cbc_item is not None
    assert cbc_item.status in (InstalledItemStatus.FAILED.value, InstalledItemStatus.PARTIAL.value)


def test_symbol_enablement_fails_closed_on_missing_session(
    sync_env: tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]
) -> None:
    service, repo, _ = sync_env

    eod_json = (FIXTURES / "eod_twse_stock_day_all.json").read_bytes()
    isin_html = (FIXTURES / "eod_isin_supported.html").read_bytes()

    service.egress_client.fetch.side_effect = lambda url, **kw: (
        (200, isin_html, {}) if "isin" in url
        else (200, eod_json, {}) if "STOCK_DAY_ALL" in url
        else (200, b"[]", {})
    )

    op_id, auth = service.create_operation_and_capability()
    # Since calendar was not run, calendar session for the date is missing -> must fail closed
    with pytest.raises(ValueError, match="not an authorized trading session"):
        service.run_symbol_enablement_pipeline(op_id, auth, symbol="2330.TW")


def test_symbol_enablement_fails_closed_on_missing_identity(
    sync_env: tuple[InstalledDataSyncService, InstalledDataOperationsRepository, str]
) -> None:
    service, repo, _ = sync_env

    # Valid calendar session
    calendar_json = json.dumps([
        {"Name": "交易日", "Date": "1150827", "Weekday": "四", "Description": "正常交易日"},
    ], ensure_ascii=False).encode("utf-8")
    eod_json = (FIXTURES / "eod_twse_stock_day_all.json").read_bytes()
    isin_html = (FIXTURES / "eod_isin_supported.html").read_bytes()

    service.egress_client.fetch.side_effect = lambda url, **kw: (
        (200, calendar_json, {}) if "holiday" in url
        else (200, isin_html, {}) if "isin" in url
        else (200, eod_json, {}) if "STOCK_DAY_ALL" in url
        else (200, b"[]", {})
    )

    op_id, auth = service.create_operation_and_capability()
    service.run_stage_prerequisites_calendar(op_id, auth)

    # Without universe ingestion, persisted identity context is missing -> must fail closed
    with pytest.raises(ValueError, match="persisted identity context missing"):
        service.run_symbol_enablement_pipeline(op_id, auth, symbol="2330.TW")
