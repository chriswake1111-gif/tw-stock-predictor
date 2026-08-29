"""Reproducible Phase 16 NBMC benchmark over an isolated SQLite database.

The runner deliberately creates its database under a temporary directory and
uses only the existing migrations.  It never opens the configured repository
database, calls a provider, or writes benchmark state into the application.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from math import ceil
from pathlib import Path
from typing import Any, Iterable

# Running the tool as ``python tools/...`` puts ``tools`` (rather than the
# repository root) on sys.path; make the local package resolution explicit.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.neutral_batch_market_context_repository import (
    NeutralBatchMarketContextRepository,
)
from src.services.neutral_batch_market_context_service import (
    NeutralBatchMarketContextService,
)


CONTRACT_VERSION = "phase16_nbmc_benchmark_v1"
DEFAULT_VARIANTS = (
    "both_available",
    "twse_partial_tpex_available",
    "twse_available_tpex_blocked",
    "one_venue_no_exact_d_other_available",
    "one_venue_usable_other_unresolved_or_empty",
    "post_k_post_d_evidence",
    "same_d_date_only_ambiguity",
    "classification_ambiguity",
    "asymmetric_orphan_distribution",
    "first_page_and_continuation_page",
    "limit_100",
)
BASELINE_MIGRATION_VERSION = "20260827_18_phase14_eod_close_context"
D = "2026-08-28"
K = "2026-08-29T00:00:00Z"
PAGE_LIMIT = 100
TWSE_COUNT = 1200
TPEX_COUNT = 800
ORPHAN_COUNT = 40
ADDITIONAL_ROWS = 240


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in manifest.items() if key != "fixture_sha256"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    expected = str(manifest.get("fixture_sha256") or "").strip().lower()
    actual = manifest_sha256(manifest)
    if expected and expected != actual:
        raise ValueError("fixture_sha256_mismatch")
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("benchmark_contract_version_mismatch")
    if manifest.get("manifest_schema_version") != "phase16_nbmc_benchmark_manifest_v1":
        raise ValueError("benchmark_manifest_schema_mismatch")
    if manifest.get("market_date") != D or manifest.get("knowledge_cutoff_at") != K:
        raise ValueError("benchmark_d_k_mismatch")
    candidates = manifest.get("effective_denominator_candidates") or {}
    if candidates != {"TWSE": TWSE_COUNT, "TPEX": TPEX_COUNT, "total": TWSE_COUNT + TPEX_COUNT}:
        raise ValueError("benchmark_candidate_count_mismatch")
    if manifest.get("additional_identity_revision_event_rows") != ADDITIONAL_ROWS:
        raise ValueError("benchmark_additional_row_count_mismatch")
    if manifest.get("combined_source_observation_orphan_count") != ORPHAN_COUNT:
        raise ValueError("benchmark_orphan_count_mismatch")
    if tuple(manifest.get("variants") or ()) != DEFAULT_VARIANTS:
        raise ValueError("benchmark_variants_mismatch")
    samples = manifest.get("samples") or {}
    if samples.get("cold") != 30 or samples.get("warm") != 30 or samples.get("total") != 60:
        raise ValueError("benchmark_sample_count_mismatch")
    if samples.get("percentile") != "nearest_rank":
        raise ValueError("benchmark_percentile_mismatch")
    workload = manifest.get("workload") or {}
    if workload.get("network") is not False or workload.get("production_database") is not False:
        raise ValueError("benchmark_safety_manifest_mismatch")
    if workload.get("max_python_page_rows") != PAGE_LIMIT + 1:
        raise ValueError("benchmark_page_bound_mismatch")


def nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or not 0 < percentile <= 1:
        raise ValueError("percentile requires non-empty values and 0<p<=1")
    return ordered[ceil(percentile * len(ordered)) - 1]


def percentile_summary(values: list[float]) -> dict[str, float]:
    return {
        "p95_seconds": nearest_rank(values, 0.95),
        "p99_seconds": nearest_rank(values, 0.99),
        "min_seconds": min(values),
        "max_seconds": max(values),
        "sample_count": len(values),
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _code(venue: str, index: int) -> str:
    return f"{1000 + index:04d}" if venue == "TWSE" else f"{3000 + index:04d}"


def _raw_rows() -> list[tuple[Any, ...]]:
    rows = []
    for resource_id, provider_id in (
        ("twse-universe-master", "twse-universe-official"),
        ("tpex-universe-master", "tpex-universe-official"),
        ("twse.eod.stock_day_all", "twse-universe-official"),
        ("tpex.eod.daily_close_quotes", "tpex-universe-official"),
        ("twse.isin.security_classification", "twse-isin-official"),
    ):
        raw_id = f"raw-phase16-{resource_id.replace('.', '-')}"
        token = f"phase16:{resource_id}"
        rows.append(
            (
                raw_id,
                _sha("identity:" + token),
                provider_id,
                resource_id,
                token,
                None,
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
                _sha("payload:" + token),
                "1",
                _sha("schema:" + resource_id),
                "hash_only",
                None,
                "fresh",
                "eligible",
                None,
                None,
            )
        )
    return rows


def _insert_raw_provenance(conn: sqlite3.Connection) -> dict[str, str]:
    conn.executemany(
        """
        INSERT INTO raw_resource_revisions (
            raw_resource_revision_id, identity_fingerprint, provider_id,
            resource_id, logical_revision_key, source_published_at,
            available_at, received_at, ingested_at, raw_payload_sha256,
            parser_version, schema_fingerprint, storage_policy, storage_location,
            quality_status, eligibility_status, supersedes_revision_id, reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        _raw_rows(),
    )
    return {
        row[3]: row[0]
        for row in _raw_rows()
    }


def _insert_universe(conn: sqlite3.Connection, raw: dict[str, str]) -> dict[tuple[str, str], tuple[str, str]]:
    anchors: dict[tuple[str, str], tuple[str, str]] = {}
    instrument_rows = []
    universe_rows = []
    revision_rows = []
    for venue, count in (("TWSE", TWSE_COUNT), ("TPEX", TPEX_COUNT)):
        master_raw = raw[f"{venue.lower()}-universe-master"]
        resource_id = f"{venue.lower()}-universe-master"
        for index in range(count):
            code = _code(venue, index)
            instrument_id = f"phase16-instrument-{venue.lower()}-{index:04d}"
            universe_revision_id = f"phase16-universe-revision-{venue.lower()}-{index:04d}"
            instrument_revision_id = f"phase16-instrument-revision-{venue.lower()}-{index:04d}"
            canonical = f"{code}.{'TW' if venue == 'TWSE' else 'TWO'}"
            payload_hash = _sha(f"universe:{venue}:{code}")
            binding = _sha(f"binding:{venue}:{code}:1")
            anchors[(venue, code)] = (instrument_id, instrument_revision_id)
            instrument_rows.append(
                (
                    instrument_id,
                    venue,
                    code,
                    1,
                    binding,
                    "2026-08-28T00:00:00Z",
                    f"phase16:{venue}:{code}",
                    f"phase16:{venue}:{code}",
                    f"Benchmark {venue} {code}",
                    "2026-08-28T00:00:00Z",
                )
            )
            universe_rows.append(
                (
                    universe_revision_id,
                    resource_id,
                    master_raw,
                    f"phase16:{venue}:{code}",
                    1,
                    None,
                    "2020-01-01",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "accepted",
                    None,
                    payload_hash,
                    _sha("schema:universe"),
                    "1",
                    f"phase16:{venue}:{code}",
                    None,
                    None,
                )
            )
            revision_rows.append(
                (
                    instrument_revision_id,
                    instrument_id,
                    universe_revision_id,
                    resource_id,
                    1,
                    venue,
                    code,
                    canonical,
                    "benchmark_fixture",
                    "股票",
                    f"Benchmark {venue} {code}",
                    "listed",
                    "normal",
                    "visible",
                    "2020-01-01",
                    None,
                    None,
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "conservative_first_observed",
                    "unknown_without_official_cadence",
                    "current",
                    1,
                    1,
                    "accepted",
                    None,
                    f"phase16:{venue}:{code}",
                    payload_hash,
                    _sha("schema:instrument"),
                    "1",
                    None,
                    None,
                    None,
                )
            )
    conn.executemany(
        """INSERT INTO universe_instruments (
            instrument_id, venue, official_code, identity_epoch,
            identity_binding_fingerprint, first_observed_at,
            first_source_reference, source_identity, display_name, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        instrument_rows,
    )
    conn.executemany(
        """INSERT INTO universe_revisions (
            universe_revision_id, resource_id, raw_resource_revision_id,
            logical_revision_key, revision_number, source_published_at,
            source_effective_date, fetched_at, received_at, first_observed_at,
            available_at, ingested_at, status, reason, payload_sha256,
            schema_fingerprint, parser_version, source_reference,
            publication_evidence_id, supersedes_revision_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        universe_rows,
    )
    revision_sql = """INSERT INTO universe_instrument_revisions (
            instrument_revision_id, instrument_id, universe_revision_id,
            resource_id, revision_number, venue, official_code, canonical_symbol,
            mapping_basis, security_type, display_name, listing_status,
            trading_state, membership_state, source_effective_date,
            source_effective_at, source_published_at, first_observed_at,
            received_at, fetched_at, available_at, ingested_at, availability_mode,
            freshness_mode, freshness_status, current_complete, coverage_complete,
            status, reason, source_reference, payload_sha256, schema_fingerprint,
            parser_version, effective_from, effective_to, supersedes_revision_id
        ) VALUES (""" + ",".join("?" for _ in range(36)) + ")"
    conn.executemany(
        revision_sql,
        revision_rows,
    )
    return anchors


def _insert_classification(conn: sqlite3.Connection, raw: dict[str, str]) -> dict[str, str]:
    rows = []
    classifications: dict[str, str] = {}
    for venue, count in (("TWSE", TWSE_COUNT), ("TPEX", TPEX_COUNT)):
        market = "上市" if venue == "TWSE" else "上櫃"
        for index in range(count):
            code = _code(venue, index)
            evidence_id = f"phase16-classification-{venue.lower()}-{index:04d}"
            classifications[f"{venue}:{code}"] = evidence_id
            payload_hash = _sha(f"classification:{venue}:{code}")
            rows.append(
                (
                    evidence_id,
                    "twse.isin.security_classification",
                    raw["twse.isin.security_classification"],
                    f"phase16:classification:{venue}:{code}",
                    code,
                    venue,
                    market,
                    "股票",
                    None,
                    "2000-01-01",
                    None,
                    "TWD",
                    None,
                    "[]",
                    "supported_stock",
                    "accepted",
                    None,
                    "eod_product_scope_v1",
                    1,
                    None,
                    None,
                    None,
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    "https://example.invalid/phase16-classification",
                    "1",
                    _sha("schema:classification"),
                    payload_hash,
                    _sha("normalized:" + payload_hash),
                    f"phase16:classification:{venue}:{code}",
                    _sha("class-identity:" + evidence_id),
                )
            )
    conn.executemany(
        """INSERT INTO eod_product_classification_evidence (
            classification_evidence_id, resource_id, raw_resource_revision_id,
            logical_revision_key, official_code, venue_raw, market_raw,
            security_type_raw, isin_raw, listing_date, cfi_raw, currency_raw,
            remarks_raw, raw_cells_json, classification_decision,
            classification_state, reason, contract_version, revision_number,
            supersedes_classification_evidence_id, revocation_reference,
            source_published_at, fetched_at, received_at, available_at,
            ingested_at, source_url, parser_version, schema_fingerprint,
            raw_payload_sha256, normalized_payload_sha256, source_record_reference,
            identity_fingerprint
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return classifications


def _source_row(
    *,
    venue: str,
    raw_id: str,
    status: str,
    source_date: str | None,
    row_count: int,
    proof: bool = False,
) -> tuple[Any, ...]:
    resource_id = "twse.eod.stock_day_all" if venue == "TWSE" else "tpex.eod.daily_close_quotes"
    scope = "twse_whole_market_daily_close" if venue == "TWSE" else "tpex_mainboard_daily_close_quotes_without_fixed_price"
    source_id = f"phase16-source-{venue.lower()}-{source_date or 'missing'}"
    coverage_state = "partial" if status == "partial" else "complete"
    return (
        source_id,
        resource_id,
        raw_id,
        f"phase16:{venue}:source:{source_date or 'missing'}",
        1,
        source_date,
        "valid" if source_date else "missing",
        status,
        coverage_state,
        "coverage_manifest" if proof else None,
        "phase16-proof" if proof else None,
        row_count,
        source_date,
        source_date,
        None,
        "2026-08-28T00:00:00Z",
        "2026-08-28T00:00:00Z",
        "2026-08-28T00:00:00Z",
        "2026-08-28T00:00:00Z",
        "https://example.invalid/phase16-source",
        "GET",
        "json",
        "eod_close_v1",
        "1",
        _sha("schema:eod:" + venue),
        _sha("source-payload:" + venue + ":" + (source_date or "missing")),
        _sha("source-normalized:" + venue + ":" + (source_date or "missing")),
        json.dumps({"market_date": D}, sort_keys=True),
        f"phase16:{venue}:source-record:{source_date or 'missing'}",
        scope,
        "benchmark source fixture",
        None,
        None,
        _sha("source-identity:" + source_id),
    )


def _insert_sources_and_observations(
    conn: sqlite3.Connection,
    raw: dict[str, str],
    anchors: dict[tuple[str, str], tuple[str, str]],
    classifications: dict[str, str],
    variant: str,
) -> None:
    source_rows = []
    source_ids: dict[str, str] = {}
    for venue, count in (("TWSE", TWSE_COUNT), ("TPEX", TPEX_COUNT)):
        if variant == "one_venue_usable_other_unresolved_or_empty" and venue == "TPEX":
            continue
        source_date = D
        status = "available"
        proof = False
        if variant == "twse_partial_tpex_available" and venue == "TWSE":
            status, proof = "partial", True
        if variant == "twse_available_tpex_blocked" and venue == "TPEX":
            status = "blocked"
        if variant == "one_venue_no_exact_d_other_available" and venue == "TPEX":
            source_date = "2026-08-27"
        orphan_count = (
            ORPHAN_COUNT
            if variant == "asymmetric_orphan_distribution" and venue == "TWSE"
            else ORPHAN_COUNT // 2
        )
        source_row = _source_row(
            venue=venue,
            raw_id=raw["twse.eod.stock_day_all" if venue == "TWSE" else "tpex.eod.daily_close_quotes"],
            status=status,
            source_date=source_date,
            row_count=count + orphan_count,
            proof=proof,
        )
        source_rows.append(source_row)
        source_ids[venue] = source_row[0]
    conn.executemany(
        """INSERT INTO eod_close_source_snapshots (
            source_snapshot_id, resource_id, raw_resource_revision_id,
            logical_revision_key, revision_number, source_trade_date,
            source_trade_date_status, status, coverage_state,
            coverage_proof_type, coverage_proof_reference, row_count,
            source_date_min, source_date_max, source_published_at, fetched_at,
            received_at, available_at, ingested_at, source_url, http_method,
            response_format, contract_version, parser_version, schema_fingerprint,
            raw_payload_sha256, normalized_payload_sha256, query_dimensions_json,
            source_record_reference, source_scope, reason, supersedes_source_snapshot_id,
            revocation_reference, identity_fingerprint
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        source_rows,
    )
    observation_rows = []
    for venue, count in (("TWSE", TWSE_COUNT), ("TPEX", TPEX_COUNT)):
        source_id = source_ids.get(venue)
        if source_id is None:
            continue
        source_date = D if not (variant == "one_venue_no_exact_d_other_available" and venue == "TPEX") else "2026-08-27"
        resource_id = "twse.eod.stock_day_all" if venue == "TWSE" else "tpex.eod.daily_close_quotes"
        scope = "twse_whole_market_daily_close" if venue == "TWSE" else "tpex_mainboard_daily_close_quotes_without_fixed_price"
        raw_id = raw[resource_id]
        for index in range(count):
            code = _code(venue, index)
            instrument_id, instrument_revision_id = anchors[(venue, code)]
            evidence_id = classifications[f"{venue}:{code}"]
            observation_id = f"phase16-observation-{venue.lower()}-{index:04d}"
            row_hash = _sha("observation:" + observation_id)
            observation_rows.append(
                (
                    observation_id,
                    resource_id,
                    raw_id,
                    source_id,
                    evidence_id,
                    instrument_id,
                    instrument_revision_id,
                    venue,
                    code,
                    source_date,
                    "valid",
                    1,
                    None,
                    "1005",
                    "1005",
                    "123456",
                    "123456",
                    None,
                    None,
                    "TWD",
                    "TWD_per_share",
                    "official_reported_close_v1",
                    "supported_stock",
                    "available",
                    "eligible",
                    "fresh",
                    "[]",
                    row_hash,
                    _sha("payload:" + observation_id),
                    _sha("normalized:" + observation_id),
                    scope,
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    f"phase16:{venue}:{source_date}:{code}",
                    None,
                    _sha("observation-identity:" + observation_id),
                )
            )
        orphan_count = ORPHAN_COUNT if variant != "asymmetric_orphan_distribution" else (ORPHAN_COUNT if venue == "TWSE" else 0)
        if variant == "asymmetric_orphan_distribution" and venue == "TWSE":
            orphan_count = ORPHAN_COUNT
        elif variant != "asymmetric_orphan_distribution":
            orphan_count = ORPHAN_COUNT // 2
        for index in range(orphan_count):
            code = f"{900000 + (0 if venue == 'TWSE' else 100) + index:06d}"
            observation_id = f"phase16-orphan-{venue.lower()}-{index:02d}"
            row_hash = _sha("orphan:" + observation_id)
            observation_rows.append(
                (
                    observation_id,
                    resource_id,
                    raw_id,
                    source_id,
                    None,
                    None,
                    None,
                    venue,
                    code,
                    source_date,
                    "valid",
                    1,
                    None,
                    "200",
                    "200",
                    "123456",
                    "123456",
                    None,
                    None,
                    "TWD",
                    "TWD_per_share",
                    "official_reported_close_v1",
                    "supported_stock",
                    "available",
                    "eligible",
                    "fresh",
                    "[]",
                    row_hash,
                    _sha("payload:" + observation_id),
                    _sha("normalized:" + observation_id),
                    scope,
                    "2026-08-28T00:00:00Z",
                    "2026-08-28T00:00:00Z",
                    f"phase16:{venue}:{source_date}:{code}",
                    None,
                    _sha("observation-identity:" + observation_id),
                )
            )
    conn.executemany(
        """INSERT INTO eod_close_observations (
            close_observation_id, resource_id, raw_resource_revision_id,
            source_snapshot_id, classification_evidence_id, instrument_id,
            instrument_revision_id, venue, official_code, trade_date,
            trade_date_status, revision_number, supersedes_observation_id,
            raw_close_text, close_value, raw_volume_text, volume_value,
            raw_trade_indication_text, trade_indication_value, currency, unit,
            price_semantics_version, product_scope, observation_status,
            public_eligibility_status, quality_status, quality_flags_json,
            row_fingerprint, raw_payload_sha256, normalized_payload_sha256,
            source_trading_scope, available_at, ingested_at,
            source_record_reference, source_note, identity_fingerprint
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        observation_rows,
    )


def _insert_additional_rows(
    conn: sqlite3.Connection,
    anchors: dict[tuple[str, str], tuple[str, str]],
    classifications: dict[str, str],
    raw: dict[str, str],
    variant: str,
) -> None:
    rows = []
    anchor_values = list(anchors.items())[:ADDITIONAL_ROWS]
    for index, ((venue, code), (instrument_id, _)) in enumerate(anchor_values):
        event_id = f"phase16-post-k-event-{index:03d}"
        rows.append(
            (
                event_id,
                instrument_id,
                "listed",
                "2026-08-28",
                None,
                "2026-08-30T00:00:00Z",
                "2026-08-30T00:00:00Z",
                f"phase16:post-k:{venue}:{code}",
                "accepted",
                "post-K benchmark evidence",
            )
        )
    conn.executemany(
        """INSERT INTO universe_lifecycle_events (
            lifecycle_event_id, instrument_id, event_type, event_date,
            effective_at, available_at, ingested_at, source_reference,
            status, reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    if variant == "same_d_date_only_ambiguity":
        venue, code = "TWSE", _code("TWSE", 0)
        instrument_id = anchors[(venue, code)][0]
        conn.execute(
            """INSERT INTO universe_lifecycle_events (
                lifecycle_event_id, instrument_id, event_type, event_date,
                effective_at, available_at, ingested_at, source_reference,
                status, reason
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "phase16-same-d-date-only",
                instrument_id,
                "terminated",
                D,
                None,
                "2026-08-28T01:00:00Z",
                "2026-08-28T01:00:00Z",
                "phase16:same-d-date-only",
                "accepted",
                "same-D date-only benchmark evidence",
            ),
        )
    if variant == "classification_ambiguity":
        venue, code = "TWSE", _code("TWSE", 0)
        evidence_id = "phase16-classification-ambiguity"
        conn.execute(
            """INSERT INTO eod_product_classification_evidence (
                classification_evidence_id, resource_id, raw_resource_revision_id,
                logical_revision_key, official_code, venue_raw, market_raw,
                security_type_raw, isin_raw, listing_date, cfi_raw, currency_raw,
                remarks_raw, raw_cells_json, classification_decision,
                classification_state, reason, contract_version, revision_number,
                supersedes_classification_evidence_id, revocation_reference,
                source_published_at, fetched_at, received_at, available_at,
                ingested_at, source_url, parser_version, schema_fingerprint,
                raw_payload_sha256, normalized_payload_sha256, source_record_reference,
                identity_fingerprint
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id,
                "twse.isin.security_classification",
                raw["twse.isin.security_classification"],
                "phase16:classification:ambiguity",
                code,
                venue,
                "上市",
                "股票",
                None,
                "2000-01-01",
                None,
                "TWD",
                "ambiguous benchmark fixture",
                "[]",
                "needs_human_input",
                "ambiguous",
                "same-D classification ambiguity",
                "eod_product_scope_v1",
                2,
                classifications[f"{venue}:{code}"],
                None,
                None,
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
                "2026-08-28T00:00:00Z",
                "https://example.invalid/phase16-classification-ambiguity",
                "1",
                _sha("schema:classification:ambiguity"),
                _sha("payload:classification:ambiguity"),
                _sha("normalized:classification:ambiguity"),
                "phase16:classification:ambiguity",
                _sha("identity:classification:ambiguity"),
            ),
        )


def build_database(path: Path, variant: str) -> None:
    apply_valuation_migration(str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        raw = _insert_raw_provenance(conn)
        anchors = _insert_universe(conn, raw)
        classifications = _insert_classification(conn, raw)
        _insert_sources_and_observations(conn, raw, anchors, classifications, variant)
        _insert_additional_rows(conn, anchors, classifications, raw, variant)


class CountingStorage:
    def __init__(self, db_path: Path):
        self.inner = EodCloseRepository(str(db_path))
        self.query_count = 0

    @contextmanager
    def read_transaction(self):
        with self.inner.read_transaction() as conn:
            count = 0

            def trace(statement: str) -> None:
                nonlocal count
                normalized = statement.lstrip().upper()
                if normalized.startswith(("WITH", "SELECT")):
                    count += 1

            conn.set_trace_callback(trace)
            try:
                yield conn
            finally:
                self.query_count += count


def run_request(service: NeutralBatchMarketContextService, storage: CountingStorage, *, limit: int) -> dict[str, Any]:
    before = storage.query_count
    started = time.perf_counter()
    first = service.as_of(
        market_date=D,
        knowledge_cutoff_at=K,
        venue_scope="TWSE_TPEX",
        limit=limit,
    )
    pages = [first]
    if first.get("next_cursor"):
        pages.append(
            service.as_of(
                market_date=D,
                knowledge_cutoff_at=K,
                venue_scope="TWSE_TPEX",
                limit=limit,
                cursor=first["next_cursor"],
            )
        )
    elapsed = time.perf_counter() - started
    rows = [len(page.get("items", [])) for page in pages]
    if any(row_count > limit for row_count in rows):
        raise RuntimeError("benchmark_page_limit_violation")
    return {
        "elapsed_seconds": elapsed,
        "query_count": storage.query_count - before,
        "page_rows": rows,
        "bounded_projection_rows": limit + 1,
        "status": first.get("status"),
    }


def environment_metadata() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "worker_count": 1,
    }


def run_benchmark(manifest: dict[str, Any], *, variants: tuple[str, ...], work_dir: Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for variant in variants:
        variant_root = work_dir / variant
        variant_root.mkdir(parents=True, exist_ok=True)
        database = variant_root / "benchmark.sqlite"
        build_database(database, variant)
        warm_service = None
        warm_storage = None
        cold_samples: list[float] = []
        warm_samples: list[float] = []
        samples: list[dict[str, Any]] = []
        for mode, count in (("cold", 30), ("warm", 30)):
            for sample_index in range(count):
                if mode == "cold":
                    storage = CountingStorage(database)
                    service = NeutralBatchMarketContextService(
                        repository=NeutralBatchMarketContextRepository(storage=storage)
                    )
                else:
                    if warm_service is None:
                        warm_storage = CountingStorage(database)
                        warm_service = NeutralBatchMarketContextService(
                            repository=NeutralBatchMarketContextRepository(storage=warm_storage)
                        )
                    storage = warm_storage
                    service = warm_service
                limit = PAGE_LIMIT if variant == "limit_100" else 25
                sample = run_request(service, storage, limit=limit)
                sample.update({"mode": mode, "sample_index": sample_index})
                samples.append(sample)
                (cold_samples if mode == "cold" else warm_samples).append(sample["elapsed_seconds"])
        results[variant] = {
            "cold": percentile_summary(cold_samples),
            "warm": percentile_summary(warm_samples),
            "samples": samples,
            "query_count_max": max(sample["query_count"] for sample in samples),
            "max_page_rows": max(max(sample["page_rows"]) for sample in samples),
        }
        del warm_service
        del warm_storage
        gc.collect()
        try:
            shutil.rmtree(variant_root)
        except PermissionError:
            # Windows can briefly retain the SQLite handle after the last
            # connection closes.  The directory is benchmark-only and is
            # reported as a cleanup note rather than changing the result.
            pass
    return {
        "contract_version": CONTRACT_VERSION,
        "fixture_sha256": manifest_sha256(manifest),
        "market_date": D,
        "knowledge_cutoff_at": K,
        "environment": environment_metadata(),
        "sample_method": {
            "cold": "new service and query-only connection per sample",
            "warm": "reused service object; each request retains one query-only connection",
            "sample_count_per_mode": 30,
            "total_per_variant": 60,
            "percentile": "nearest_rank",
        },
        "thresholds_seconds": manifest["thresholds_seconds"],
        "variants": results,
    }


def evaluate_gate(
    result: dict[str, Any],
    thresholds: dict[str, float],
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    passed = True
    for variant, summary in result["variants"].items():
        variant_checks = {
            "warm_p95": summary["warm"]["p95_seconds"] <= float(thresholds["warm_p95"]),
            "warm_p99": summary["warm"]["p99_seconds"] <= float(thresholds["warm_p99"]),
            "cold_p95": summary["cold"]["p95_seconds"] <= float(thresholds["cold_p95"]),
            "cold_p99": summary["cold"]["p99_seconds"] <= float(thresholds["cold_p99"]),
            "bounded_page": summary["max_page_rows"] <= PAGE_LIMIT,
        }
        checks[variant] = variant_checks
        passed = passed and all(variant_checks.values())
    return {
        "status": "pass" if passed else "hold",
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="tests/fixtures/phase16_nbmc_benchmark_v1.json")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--variant", action="append", dest="variants")
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="return a non-zero exit code when measured thresholds do not pass",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    selected = tuple(args.variants or DEFAULT_VARIANTS)
    unknown = sorted(set(selected) - set(DEFAULT_VARIANTS))
    if unknown:
        raise SystemExit(f"unknown benchmark variant: {', '.join(unknown)}")
    default_root = Path.cwd() / ".tmp_phase16_benchmark"
    work_dir = Path(args.work_dir) if args.work_dir else default_root
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_benchmark(manifest, variants=selected, work_dir=work_dir)
        result["acceptance_gate"] = evaluate_gate(
            result,
            manifest["thresholds_seconds"],
        )
        result["selected_variants"] = list(selected)
        result["manifest_path"] = str(Path(args.manifest).resolve())
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        if args.output:
            Path(args.output).write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0 if result["acceptance_gate"]["status"] == "pass" or not args.enforce_gate else 1
    finally:
        if work_dir == default_root and work_dir.exists():
            shutil.rmtree(work_dir)


if __name__ == "__main__":
    raise SystemExit(main())
