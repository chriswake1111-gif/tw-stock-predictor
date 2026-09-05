"""Small transactional SQLite migration runner with checksum verification."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.domain.valuation import utc_now_timestamp


MIGRATION_IDS = (
    "20260801_01_evidence_model_v2_valuation",
    "20260801_02_evidence_model_v2_approval_hardening",
    "20260801_03_pe_evidence_basis",
    "20260801_04_evidence_model_v2_liquidity",
    "20260809_05_evidence_model_v2_fibonacci",
    "20260809_06_evidence_model_v2_deployment",
    "20260809_07_evidence_model_v2_screening",
    "20260809_08_evidence_model_v2_synthesis_snapshot",
    "20260810_09_evidence_model_v2_performance_validation",
    "20260810_10_phase8_review_remediation",
    "20260811_11_evidence_model_v2_data_foundation",
    "20260811_12_raw_revision_metadata_identity",
    "20260811_13_phase10_first_review_remediation",
    "20260813_14_evidence_model_v2_research_review_queue",
    "20260813_15_phase12_first_review_remediation",
    "20260821_16_phase13_universe_foundation",
)
MIGRATION_ID = MIGRATION_IDS[-1]
MIGRATION_FILES = tuple(
    Path(__file__).resolve().parents[2] / "migrations" / f"{migration_id}.sql"
    for migration_id in MIGRATION_IDS
)

# Additive remediation migrations are tracked separately so historical callers
# that treat Phase 13's original migration as the public baseline remain
# compatible. They still have a durable version/checksum marker and execute in
# the same transaction after the ordered baseline migrations.
ADDITIONAL_MIGRATION_IDS = (
    "20260821_17_phase13_second_review_remediation",
    "20260827_18_phase14_eod_close_context",
    "20260827_19_phase14_second_code_review_remediation",
    "20260828_20_phase14_third_code_review_remediation",
    "20260902_21_installed_data_operations",
    "20260905_22_phase20_universe_short_name",
)
ADDITIONAL_MIGRATION_FILES = tuple(
    Path(__file__).resolve().parents[2] / "migrations" / f"{migration_id}.sql"
    for migration_id in ADDITIONAL_MIGRATION_IDS
)


@contextmanager
def _closed_sqlite_connection(db_path: str) -> Any:
    """Keep sqlite's transaction context semantics and close its handle."""

    connection = sqlite3.connect(db_path)
    try:
        try:
            yield connection
        except BaseException as exc:
            connection.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            connection.__exit__(None, None, None)
    finally:
        connection.close()


def migration_manifest(resource_root: str | Path | None = None) -> list[dict[str, str]]:
    """Return the immutable migration resource/checksum manifest.

    The migration IDs and SQL remain the Phase 1-14 contract.  ``resource_root``
    only changes where packaged resources are read from; it never changes the
    ordered IDs or their SQL content.
    """

    root = Path(resource_root).resolve(strict=False) if resource_root else Path(__file__).resolve().parents[2]
    records: list[dict[str, str]] = []
    if resource_root is None:
        # Preserve the existing test/tooling seam where callers temporarily
        # replace MIGRATION_IDS/MIGRATION_FILES to model a historical head or
        # a broken migration.  Packaged callers pass an explicit resource root
        # and therefore never read source-tree paths.
        baseline = tuple(zip(MIGRATION_IDS, MIGRATION_FILES))
        additive = tuple(zip(ADDITIONAL_MIGRATION_IDS, ADDITIONAL_MIGRATION_FILES))
    else:
        baseline = tuple(
            (migration_id, root / "migrations" / f"{migration_id}.sql")
            for migration_id in MIGRATION_IDS
        )
        additive = tuple(
            (migration_id, root / "migrations" / f"{migration_id}.sql")
            for migration_id in ADDITIONAL_MIGRATION_IDS
        )
    for migration_id, path_value in (*baseline, *additive):
        path = Path(path_value)
        sql = path.read_text(encoding="utf-8")
        records.append({
            "version_id": migration_id,
            "path": path.as_posix(),
            "checksum_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            "sql": sql,
        })
    return records


# Phase 13 registry rows are part of the migration contract.  SQLite's
# ``ON CONFLICT DO NOTHING`` is safe only after these semantic checks: an
# incompatible same-ID row must abort the transaction rather than being
# silently retained.
_PHASE13_SEED_EXPECTATIONS = {
    "data_providers": {
        "twse-universe-official": {
            "display_name": "TWSE Universe Official", "authority_tier": "authoritative",
            "provider_type": "official", "base_identity": "https://www.twse.com.tw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "042ecef36a6ca8dd109b948f6a679973b31e69c759d7e9d1e408b1f83746cb3e",
        },
        "tpex-universe-official": {
            "display_name": "TPEx Universe Official", "authority_tier": "authoritative",
            "provider_type": "official", "base_identity": "https://www.tpex.org.tw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "add538b4692192633b1142339113d9a5009e68ab309c83d5de69f0657e7eb3bb",
        },
    },
    "data_resources": {
        "twse-universe-master": {
            "provider_id": "twse-universe-official", "logical_resource_key": "twse.t187ap03_L",
            "resource_type": "symbol_master", "market": "TWSE", "expected_frequency": "periodic",
            "freshness_policy": "unknown_without_official_cadence", "parser_id": "twse_universe_master",
            "parser_version": "1", "schema_version": "phase13", "storage_policy": "archive_raw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "be3665c8f7640f7031de4c8526eeace37873f34df8adebdff32792f94e8e808d",
        },
        "twse-universe-newlisting": {
            "provider_id": "twse-universe-official", "logical_resource_key": "twse.company.newlisting",
            "resource_type": "corporate_action", "market": "TWSE", "expected_frequency": "periodic",
            "freshness_policy": "event_observation", "parser_id": "twse_universe_newlisting",
            "parser_version": "1", "schema_version": "phase13", "storage_policy": "archive_raw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "7a521d559be9f20658c194b2b8bc5e11584b3b883c0550dd25148f9a2bf0bbbd",
        },
        "twse-universe-termination": {
            "provider_id": "twse-universe-official", "logical_resource_key": "twse.company.suspendListingCsvAndHtml",
            "resource_type": "corporate_action", "market": "TWSE", "expected_frequency": "periodic",
            "freshness_policy": "manual_publication_evidence_required", "parser_id": "twse_universe_termination",
            "parser_version": "1", "schema_version": "phase13", "storage_policy": "archive_raw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "c9f0bb3f8ec9c7bfdd40bb56a21bafc6d7d947e0e4b2cc4be0b9edb5b33e8502",
        },
        "tpex-universe-master": {
            "provider_id": "tpex-universe-official", "logical_resource_key": "tpex.mopsfin_t187ap03_O",
            "resource_type": "symbol_master", "market": "TPEX", "expected_frequency": "periodic",
            "freshness_policy": "unknown_without_official_cadence", "parser_id": "tpex_universe_master",
            "parser_version": "1", "schema_version": "phase13", "storage_policy": "archive_raw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "afeb2b0cd9e896bc8a3d0e3b77410557a0d3357ea3bdd94c7ede4e1274265d66",
        },
        "tpex-universe-lifecycle": {
            "provider_id": "tpex-universe-official", "logical_resource_key": "tpex.company.deListed",
            "resource_type": "corporate_action", "market": "TPEX", "expected_frequency": "manual",
            "freshness_policy": "manual_publication_evidence_required", "parser_id": "tpex_universe_lifecycle",
            "parser_version": "1", "schema_version": "phase13", "storage_policy": "archive_raw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "355e21480d1413692b2698e4fe82cd16c87b5de553b76ca0504f92378d738cb7",
        },
        "tpex-universe-operational": {
            "provider_id": "tpex-universe-official", "logical_resource_key": "tpex.spendi.cmode",
            "resource_type": "trading_calendar", "market": "TPEX", "expected_frequency": "periodic",
            "freshness_policy": "event_observation", "parser_id": "tpex_universe_operational",
            "parser_version": "1", "schema_version": "phase13", "storage_policy": "archive_raw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "53f96df666aa8c19fdf4b3e0254d9e1033ff617a9273c7789345e65a430d5884",
        },
        "tpex-universe-corroborating": {
            "provider_id": "tpex-universe-official", "logical_resource_key": "tpex.company.current",
            "resource_type": "symbol_master", "market": "TPEX", "expected_frequency": "manual",
            "freshness_policy": "manual_publication_evidence_required", "parser_id": "tpex_universe_company",
            "parser_version": "1", "schema_version": "phase13", "storage_policy": "archive_raw",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "payload_fingerprint": "aadfe1261421f17cb48341c4721d09575c7ac667b5c5a65b1645a14cf6b2f254",
        },
    },
    "universe_resource_policies": {
        "twse-universe-master": {
            "resource_role": "master_snapshot", "availability_mode": "conservative_first_observed",
            "freshness_mode": "unknown_without_official_cadence", "completeness_policy": "accepted_master_complete",
            "mapping_policy_version": "universe_symbol_mapping_v1", "source_scope": "TWSE t187ap03_L basic master",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "policy_fingerprint": "93588835b58c11ee96ec28266ae2820de103a9a78fdc8bb8572448f98b8cf9fd",
        },
        "twse-universe-newlisting": {
            "resource_role": "listing_lifecycle_event", "availability_mode": "conservative_first_observed",
            "freshness_mode": "event_observation", "completeness_policy": "explicit_listing_event",
            "mapping_policy_version": "universe_symbol_mapping_v1", "source_scope": "TWSE company/newlisting",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "policy_fingerprint": "6670de4e7d08da5ec3cf25aee65c9791f90f4882c7589632e40b09b51db351c7",
        },
        "twse-universe-termination": {
            "resource_role": "listing_lifecycle_event", "availability_mode": "manual_publication_evidence_required",
            "freshness_mode": "event_observation", "completeness_policy": "explicit_termination_only",
            "mapping_policy_version": "universe_symbol_mapping_v1", "source_scope": "TWSE company/suspendListingCsvAndHtml termination candidate",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "policy_fingerprint": "c9f0bb3f8ec9c7bfdd40bb56a21bafc6d7d947e0e4b2cc4be0b9edb5b33e8502",
        },
        "tpex-universe-master": {
            "resource_role": "master_snapshot", "availability_mode": "conservative_first_observed",
            "freshness_mode": "unknown_without_official_cadence", "completeness_policy": "accepted_master_complete",
            "mapping_policy_version": "universe_symbol_mapping_v1", "source_scope": "TPEx mopsfin_t187ap03_O",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "policy_fingerprint": "7fd75d94f0e867594ab7c2834cec5c0cd1422b0996198d94395d0b59791a4966",
        },
        "tpex-universe-lifecycle": {
            "resource_role": "listing_lifecycle_event", "availability_mode": "manual_publication_evidence_required",
            "freshness_mode": "event_observation", "completeness_policy": "explicit_termination_only",
            "mapping_policy_version": "universe_symbol_mapping_v1", "source_scope": "TPEx delisted/deListed manual publication",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "policy_fingerprint": "a8252cf02efa17cfb0ac44ca594014517096d9d98f28dbb0333496231b5430d3",
        },
        "tpex-universe-operational": {
            "resource_role": "trading_operational_event", "availability_mode": "conservative_first_observed",
            "freshness_mode": "event_observation", "completeness_policy": "event_only",
            "mapping_policy_version": "universe_symbol_mapping_v1", "source_scope": "TPEx spendi/cmode",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "policy_fingerprint": "a1261db49fdcfae33d97edc73a227ec37e669ee506a96b91c07c55106c402870",
        },
        "tpex-universe-corroborating": {
            "resource_role": "corroborating_identity_observation", "availability_mode": "manual_publication_evidence_required",
            "freshness_mode": "event_observation", "completeness_policy": "corroborating_only",
            "mapping_policy_version": "universe_symbol_mapping_v1", "source_scope": "TPEx company current page",
            "enabled": 1, "created_at": "2026-08-21T00:00:00Z",
            "policy_fingerprint": "d949c987016490aa4ba03b8110bde71588ee74f62b594f29802e3bfa61a97661",
        },
    },
}


_PHASE14_SEED_EXPECTATIONS = {
    "data_providers": {
        "twse-isin-official": {
            "display_name": "TWSE ISIN Official",
            "authority_tier": "authoritative",
            "provider_type": "official",
            "base_identity": "https://isin.twse.com.tw",
            "enabled": 1,
            "created_at": "2026-08-27T00:00:00Z",
            "payload_fingerprint": "c764668cbb0d7a03ee52ca92bf4e09fc13260ad460be425ccd427f4dabfc48fe",
        },
    },
    "data_resources": {
        "twse.eod.stock_day_all": {
            "provider_id": "twse-universe-official",
            "logical_resource_key": "twse.eod.stock_day_all",
            "resource_type": "eod_close",
            "market": "TWSE",
            "expected_frequency": "daily",
            "freshness_policy": "eod_currentness_policy_v1",
            "parser_id": "twse_eod_stock_day_all",
            "parser_version": "1",
            "schema_version": "eod_close_v1",
            "storage_policy": "archive_raw",
            "enabled": 1,
            "created_at": "2026-08-27T00:00:00Z",
            "payload_fingerprint": "164d60344ed21ceea58560744f4fbc7436b7dc1afede53b628afe58928c4171a",
        },
        "tpex.eod.daily_close_quotes": {
            "provider_id": "tpex-universe-official",
            "logical_resource_key": "tpex.eod.daily_close_quotes",
            "resource_type": "eod_close",
            "market": "TPEX",
            "expected_frequency": "daily",
            "freshness_policy": "eod_currentness_policy_v1",
            "parser_id": "tpex_eod_daily_close_quotes",
            "parser_version": "1",
            "schema_version": "eod_close_v1",
            "storage_policy": "archive_raw",
            "enabled": 1,
            "created_at": "2026-08-27T00:00:00Z",
            "payload_fingerprint": "e4acbd637e638eb7a78ec2fc487555689ffab58b7e53d09c85dae06fb59379c9",
        },
        "twse.isin.security_classification": {
            "provider_id": "twse-isin-official",
            "logical_resource_key": "twse.isin.security_classification",
            "resource_type": "product_classification",
            "market": "TWSE_ISIN",
            "expected_frequency": "manual",
            "freshness_policy": "eod_product_scope_v1",
            "parser_id": "twse_isin_security_classification",
            "parser_version": "1",
            "schema_version": "eod_product_scope_v1",
            "storage_policy": "archive_raw",
            "enabled": 1,
            "created_at": "2026-08-27T00:00:00Z",
            "payload_fingerprint": "74df7f85e702f72da6bd6428d57ceb01fe9a8e0d43cf4e5a1c3dd18df9a30b92",
        },
    },
    "eod_price_resource_policies": {
        "twse.eod.stock_day_all": {
            "source_trading_scope": "twse_whole_market_daily_close",
            "price_semantics_version": "official_reported_close_v1",
            "currency_unit_policy_version": "eod_currency_unit_scope_v1",
            "currency": "TWD",
            "unit": "TWD_per_share",
            "approved_unit_evidence_reference": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL#ClosingPrice",
            "enabled": 1,
            "write_enabled": 0,
            "created_at": "2026-08-27T00:00:00Z",
            "policy_fingerprint": "09a80ff6633ed265b972573abfada5c9c3261404c0ce6144f3aee3a043e8c126",
        },
        "tpex.eod.daily_close_quotes": {
            "source_trading_scope": "tpex_mainboard_daily_close_quotes_without_fixed_price",
            "price_semantics_version": "official_reported_close_v1",
            "currency_unit_policy_version": "eod_currency_unit_scope_v1",
            "currency": "TWD",
            "unit": "TWD_per_share",
            "approved_unit_evidence_reference": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes#Close",
            "enabled": 1,
            "write_enabled": 0,
            "created_at": "2026-08-27T00:00:00Z",
            "policy_fingerprint": "a336016523ca1039506053d296f1d99d97b41cfbee7112436722d5117742d1eb",
        },
    },
}


def _validate_phase13_seed_compatibility(conn: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for table, seeds in _PHASE13_SEED_EXPECTATIONS.items():
        if table not in tables:
            continue
        identity_column = "provider_id" if table == "data_providers" else "resource_id"
        for identity, expected in seeds.items():
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {identity_column} = ?", (identity,)
            ).fetchone()
            if row is None:
                continue
            for field, value in expected.items():
                if row[field] != value:
                    raise RuntimeError(
                        f"phase13 seed conflict: {table}:{identity}:{field}"
                    )


def _validate_phase14_seed_compatibility(conn: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for table, seeds in _PHASE14_SEED_EXPECTATIONS.items():
        if table not in tables:
            continue
        identity_column = (
            "provider_id"
            if table == "data_providers"
            else "resource_id"
        )
        for identity, expected in seeds.items():
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {identity_column} = ?",
                (identity,),
            ).fetchone()
            if row is None:
                continue
            for field, value in expected.items():
                if row[field] != value:
                    raise RuntimeError(
                        f"phase14 seed conflict: {table}:{identity}:{field}"
                    )


def _statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("migration contains an incomplete SQL statement")
    return statements


def apply_valuation_migration(
    db_path: str,
    *,
    resource_root: str | Path | None = None,
) -> dict[str, Any]:
    database_path = Path(db_path)
    if db_path != ":memory:":
        database_path.resolve().parent.mkdir(parents=True, exist_ok=True)
    migration_records = migration_manifest(resource_root)
    migrations = [
        (record["version_id"], record["sql"], record["checksum_sha256"])
        for record in migration_records
        if record["version_id"] in MIGRATION_IDS
    ]
    additional_migrations = [
        (record["version_id"], record["sql"], record["checksum_sha256"])
        for record in migration_records
        if record["version_id"] in ADDITIONAL_MIGRATION_IDS
    ]
    applied_ids: list[str] = []
    with _closed_sqlite_connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Rebuild migrations run with FK enforcement paused, then fail closed on
        # explicit checks of the rebuilt table and its dependants before commit.
        # This prevents ALTER TABLE from rewriting child references to a
        # temporary table name while tolerating unrelated legacy FK debt.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version_id TEXT PRIMARY KEY,
                    checksum_sha256 TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            for migration_id, sql, checksum in migrations:
                existing = conn.execute(
                    "SELECT checksum_sha256 FROM schema_migrations WHERE version_id = ?",
                    (migration_id,),
                ).fetchone()
                if existing:
                    if existing[0] != checksum:
                        raise RuntimeError(
                            f"migration checksum mismatch for {migration_id}"
                        )
                    continue
                if migration_id == "20260821_16_phase13_universe_foundation":
                    _validate_phase13_seed_compatibility(conn)
                for statement in _statements(sql):
                    conn.execute(statement)
                if migration_id == "20260821_16_phase13_universe_foundation":
                    _validate_phase13_seed_compatibility(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version_id, checksum_sha256, applied_at) VALUES (?, ?, ?)",
                    (migration_id, checksum, utc_now_timestamp()),
                )
                applied_ids.append(migration_id)
            # The Phase 13 remediation is additive to the Phase 13 schema.
            # Older compatibility tests and callers may intentionally stop at
            # an earlier migration; do not attempt ALTER TABLE against a
            # database that has not created its target tables yet. A later
            # full invocation will apply the marker after Phase 13 itself.
            phase13_tables_exist = all(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table_name,),
                ).fetchone()
                for table_name in ("universe_revisions", "universe_instrument_revisions")
            )
            if phase13_tables_exist:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS additive_schema_migrations (
                        version_id TEXT PRIMARY KEY,
                        checksum_sha256 TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )"""
                )
                for migration_id, sql, checksum in additional_migrations:
                    existing = conn.execute(
                        "SELECT checksum_sha256 FROM additive_schema_migrations WHERE version_id = ?",
                        (migration_id,),
                    ).fetchone()
                    if existing:
                        if existing[0] != checksum:
                            raise RuntimeError(f"migration checksum mismatch for {migration_id}")
                        continue
                    if migration_id == "20260827_18_phase14_eod_close_context":
                        _validate_phase14_seed_compatibility(conn)
                    for statement in _statements(sql):
                        conn.execute(statement)
                    if migration_id == "20260827_18_phase14_eod_close_context":
                        _validate_phase14_seed_compatibility(conn)
                    conn.execute(
                        "INSERT INTO additive_schema_migrations(version_id, checksum_sha256, applied_at) VALUES (?, ?, ?)",
                        (migration_id, checksum, utc_now_timestamp()),
                    )
            checked_tables = (
                "raw_resource_revisions",
                "data_quality_issues",
                "trading_calendar_revisions",
                "resource_publication_evidence",
                "ingestion_lock_recovery_events",
                "ingestion_resource_locks",
                "cbc_m1b_monthly",
                "research_watchlist_items",
                "research_review_events",
                "universe_instruments",
                "universe_revisions",
                "universe_instrument_revisions",
                "universe_identity_alias_events",
                "universe_lifecycle_events",
                "universe_operational_state_events",
                "universe_ingestion_idempotency",
                "eod_price_resource_policies",
                "eod_close_source_snapshots",
                "eod_product_classification_evidence",
                "eod_close_observations",
                "eod_ingestion_idempotency",
                "eod_ingestion_command_reservations",
            )
            existing_tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            violations = [
                row
                for table in checked_tables
                if table in existing_tables
                for row in conn.execute(f"PRAGMA foreign_key_check({table})").fetchall()
            ]
            if violations:
                raise sqlite3.IntegrityError(
                    f"migration foreign key check failed: {violations[:3]}"
                )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            additive_rows = [
                row[0]
                for row in conn.execute(
                    "SELECT version_id FROM additive_schema_migrations ORDER BY version_id"
                ).fetchall()
            ] if phase13_tables_exist else []
            return {
                "migration_id": MIGRATION_ID,
                "checksum_sha256": migrations[-1][2],
                "applied": bool(applied_ids),
                "applied_migration_ids": applied_ids,
                # Keep the Phase 13 compatibility field stable for callers
                # that use it as the last pre-Phase-14 additive marker.  The
                # complete additive history is exposed separately.
                "additive_migration_ids": [
                    migration_id for migration_id in additive_rows
                    if migration_id != "20260827_18_phase14_eod_close_context"
                ],
                "applied_additive_migration_ids": additive_rows,
            }
        except Exception:
            conn.rollback()
            raise
