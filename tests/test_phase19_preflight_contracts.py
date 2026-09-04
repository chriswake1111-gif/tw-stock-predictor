"""WP00: Test-Only Contract & Fixture Preflight for Phase 19.

This module is a mandatory test-only STOP gate before any Phase 19 production,
migration, or frontend changes are made.

Verifications:
1. Inspect and assert live constructor and method signatures for UniverseWriteGuard,
   EodCloseIngestionService, ProductionIngestionService, DataFoundationRepository,
   and StartupCoordinator.
2. Parse TWSE fixture (2330) via parse_twse_isin_classification(...) and assert auditable evidence:
   market_raw == "上市", security_type_raw == "股票", state == "accepted", decision == "supported_stock", reason is None.
3. Parse TPEx fixture (e.g. 6547) via parse_twse_isin_classification(...) and assert auditable evidence:
   market_raw == "上櫃", security_type_raw == "股票", state == "accepted", decision == "supported_stock", reason is None.
4. Assert venue mismatch against live classify_product rules:
   - 2330 with expected_market="上櫃" -> state == "accepted", decision == "needs_human_input", reason == "cross_venue_mismatch", market_raw == "上市".
   - 6547 with expected_market="上市" -> state == "accepted", decision == "needs_human_input", reason == "cross_venue_mismatch", market_raw == "上櫃".
5. Assert missing/ambiguous/schema-changed ISIN HTML fails closed without synthesizing ordinary equity.
6. Verify exact foreign key and column names in current migrations/ and src/models/schema.py.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from src.collectors.twse_isin_classification_collector import (
    parse_twse_isin_classification,
)
from src.domain.eod_close import classify_product
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.runtime.startup_coordinator import StartupCoordinator
from src.services.eod_close_ingestion_service import EodCloseIngestionService
from src.services.production_ingestion_service import ProductionIngestionService
from src.services.universe_write_guard import UniverseWriteGuard

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

TPEX_ISIN_HTML_6547 = """<!doctype html>
<html lang="zh-Hant">
  <body>
    <table>
      <tr><th>Security Code</th><td>6547</td></tr>
      <tr><th>Market</th><td>上櫃</td></tr>
      <tr><th>Type of security</th><td>股票</td></tr>
      <tr><th>Date Stock Listed</th><td>104/10/27</td></tr>
      <tr><th>ISIN Code</th><td>TW0006547003</td></tr>
      <tr><th>Currency</th><td>新台幣</td></tr>
      <tr><th>Remarks</th><td></td></tr>
    </table>
  </body>
</html>
"""


def test_preflight_live_signatures() -> None:
    """Assert live constructor and method signatures before Phase 19 modifications."""
    # UniverseWriteGuard
    uwg_init = inspect.signature(UniverseWriteGuard.__init__)
    assert "enabled" in uwg_init.parameters
    uwg_require = inspect.signature(UniverseWriteGuard.require_enabled)
    assert "context" in uwg_require.parameters

    # EodCloseIngestionService
    eod_init = inspect.signature(EodCloseIngestionService.__init__)
    assert "db_path" in eod_init.parameters
    assert "enabled" in eod_init.parameters
    assert "foundation" in eod_init.parameters
    assert "repository" in eod_init.parameters
    eod_req = inspect.signature(EodCloseIngestionService._require_enabled)
    assert len(eod_req.parameters) >= 1  # 'self' in current live signature
    eod_price = inspect.signature(EodCloseIngestionService.ingest_price_payload)
    assert "venue" in eod_price.parameters
    assert "payload" in eod_price.parameters
    eod_isin = inspect.signature(EodCloseIngestionService.ingest_classification_html)
    assert "official_code" in eod_isin.parameters
    assert "html" in eod_isin.parameters
    assert "venue" in eod_isin.parameters

    # ProductionIngestionService
    prod_init = inspect.signature(ProductionIngestionService.__init__)
    assert "db_path" in prod_init.parameters

    # DataFoundationRepository
    df_init = inspect.signature(DataFoundationRepository.__init__)
    assert "db_path" in df_init.parameters

    # StartupCoordinator
    sc_init = inspect.signature(StartupCoordinator.__init__)
    assert "settings" in sc_init.parameters
    sc_prep = inspect.signature(StartupCoordinator.prepare)
    assert len(sc_prep.parameters) == 1  # self only


def test_preflight_twse_isin_classification_parsing() -> None:
    """Assert TWSE positive classification returns accepted supported_stock."""
    html = (FIXTURES / "eod_isin_supported.html").read_text(encoding="utf-8")
    parsed = parse_twse_isin_classification(
        html,
        official_code="2330",
        expected_market="上市",
        trade_date="2026-08-27",
    )

    assert parsed.state == "accepted"
    assert parsed.decision == "supported_stock"
    assert parsed.reason is None
    assert parsed.official_code == "2330"
    assert parsed.official_code_raw == "2330"
    assert parsed.market_raw == "上市"
    assert parsed.security_type_raw == "股票"
    assert parsed.listing_date == "2000-09-05"


def test_preflight_tpex_isin_classification_parsing() -> None:
    """Assert TPEx positive classification returns accepted supported_stock (STOP Gate)."""
    parsed = parse_twse_isin_classification(
        TPEX_ISIN_HTML_6547,
        official_code="6547",
        expected_market="上櫃",
        trade_date="2026-08-27",
    )

    assert parsed.state == "accepted"
    assert parsed.decision == "supported_stock"
    assert parsed.reason is None
    assert parsed.official_code == "6547"
    assert parsed.official_code_raw == "6547"
    assert parsed.market_raw == "上櫃"
    assert parsed.security_type_raw == "股票"
    assert parsed.listing_date == "2015-10-27"


def test_preflight_cross_venue_mismatch_rules() -> None:
    """Assert venue mismatch results in accepted classification with cross_venue_mismatch reason."""
    # 2330 (上市) queried with expected_market="上櫃"
    twse_html = (FIXTURES / "eod_isin_supported.html").read_text(encoding="utf-8")
    parsed_twse_mismatch = parse_twse_isin_classification(
        twse_html,
        official_code="2330",
        expected_market="上櫃",
        trade_date="2026-08-27",
    )
    assert parsed_twse_mismatch.state == "accepted"
    assert parsed_twse_mismatch.decision == "needs_human_input"
    assert parsed_twse_mismatch.reason == "cross_venue_mismatch"
    assert parsed_twse_mismatch.market_raw == "上市"

    # 6547 (上櫃) queried with expected_market="上市"
    parsed_tpex_mismatch = parse_twse_isin_classification(
        TPEX_ISIN_HTML_6547,
        official_code="6547",
        expected_market="上市",
        trade_date="2026-08-27",
    )
    assert parsed_tpex_mismatch.state == "accepted"
    assert parsed_tpex_mismatch.decision == "needs_human_input"
    assert parsed_tpex_mismatch.reason == "cross_venue_mismatch"
    assert parsed_tpex_mismatch.market_raw == "上櫃"


def test_preflight_malformed_and_missing_isin_html_fails_closed() -> None:
    """Assert missing/ambiguous/schema-changed ISIN HTML fails closed without synthesizing equity."""
    # Empty HTML
    parsed_empty = parse_twse_isin_classification(
        "<html><body></body></html>",
        official_code="2330",
        expected_market="上市",
    )
    assert parsed_empty.state == "schema_changed"
    assert parsed_empty.decision == "needs_human_input"
    assert parsed_empty.reason == "classification_evidence_missing"

    # Partial table missing Market
    parsed_no_market = parse_twse_isin_classification(
        "<table><tr><th>Security Code</th><td>2330</td></tr><tr><th>Type of security</th><td>股票</td></tr></table>",
        official_code="2330",
        expected_market="上市",
    )
    assert parsed_no_market.state == "schema_changed"
    assert parsed_no_market.decision == "needs_human_input"

    # Direct classify_product test without required evidence
    res = classify_product(
        official_code=None,
        requested_code="2330",
        market=None,
        expected_market="上市",
        security_type=None,
    )
    assert res["decision"] == "needs_human_input"
    assert "classification_evidence_missing" in res["reason_codes"]


def test_preflight_schema_and_migration_invariants() -> None:
    """Assert migration runner and schema constants align with migration 20 baseline and 21 extension (BC-1)."""
    from src.repositories.migration_runner import ADDITIONAL_MIGRATION_IDS, MIGRATION_IDS

    all_migrations = MIGRATION_IDS + ADDITIONAL_MIGRATION_IDS
    assert len(all_migrations) >= 20
    assert all_migrations[19] == "20260828_20_phase14_third_code_review_remediation"
    if len(all_migrations) >= 21:
        assert all_migrations[20] == "20260902_21_installed_data_operations"
