from __future__ import annotations

import base64
import hashlib
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.eod_coverage import (
    CoverageCursor,
    CoverageCursorError,
    CoverageRequest,
    CoverageStatus,
    decode_cursor,
    eod_coverage_visibility_status_v1,
)
from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.eod_coverage_repository import EodCoverageRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseRepository
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from src.services.eod_coverage_service import EodCoverageService
from src.api.routes.v2_eod_coverage import router
from tests.test_phase14_eod_repository_service import (
    _classification,
    _db_with_identity,
    _identity_payload,
    _identity_revision_id,
    _observation,
    _raw,
    _source,
)
from tests.phase13_test_support import seed_raw_provenance


TARGET_DATE = "2026-08-27"
CUTOFF = "2026-08-28T00:00:00Z"
FORBIDDEN_KEYS = {
    "raw_payload",
    "raw_payload_sha256",
    "normalized_payload_sha256",
    "source_snapshot_id",
    "classification_evidence_id",
    "instrument_revision_id",
    "idempotency_key",
    "close_value",
    "volume_value",
    "price",
    "rank",
    "recommendation",
    "valuation",
    "signal",
    "model_score",
    "confidence",
}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _db_with_identity_at(tmp_path, first_observed_at: str):
    db = tmp_path / "phase14.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    universe = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    context = UniverseOperatorContext("operator", "identity", "identity-lock", "identity-audit")
    anchor = universe.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at=first_observed_at, source_reference="fixture",
        context=context,
    )
    universe.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="twse-universe-master",
        logical_revision_key="master",
        revision_number=1,
        payload=_identity_payload(
            first_observed_at=first_observed_at,
            fetched_at=first_observed_at,
            received_at=first_observed_at,
            ingested_at=first_observed_at,
            available_at=first_observed_at,
        ),
        context=context,
        idempotency_key="identity-revision",
    )
    return db, anchor


def _seed_coverage(
    tmp_path,
    *,
    source_kwargs: dict | None = None,
    first_observed_at: str | None = None,
    include_observation: bool = True,
    classification_kwargs: dict | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db, anchor = (
        _db_with_identity_at(tmp_path, first_observed_at)
        if first_observed_at is not None
        else _db_with_identity(tmp_path)
    )
    repo = EodCloseRepository(str(db))
    raw, raw_hash = _raw(
        db,
        "twse.eod.stock_day_all",
        "twse-universe-official",
        "coverage-source-2026-08-27",
        "2026-08-27T05:00:00Z",
    )
    source_options = {"date": TARGET_DATE}
    source_options.update(source_kwargs or {})
    source = _source(
        repo,
        raw,
        raw_hash,
        at="2026-08-27T05:00:00Z",
        **source_options,
    )
    classification_options = {"at": "2026-08-27T04:00:00Z"}
    classification_options.update(classification_kwargs or {})
    classification = _classification(repo, db, **classification_options)
    observation = None
    if include_observation:
        observation = _observation(
            repo,
            raw=raw,
            raw_hash=raw_hash,
            source=source,
            classification=classification,
            anchor=anchor,
            instrument_revision_id=_identity_revision_id(db, anchor["instrument_id"]),
            at="2026-08-27T05:02:00Z",
        )
    return db, repo, anchor, raw, raw_hash, source, classification, observation


def _insert_independent_classification_root(
    db,
    template: dict,
    *,
    market_raw: str,
    at: str = "2026-08-27T04:02:00Z",
) -> dict:
    """Insert an independent immutable root without invoking Phase 14 writes.

    Phase 14's existing write path keys a classification correction by code.
    This fixture represents a genuinely independent cross-venue root directly
    so the Phase 15 projection can test exact market-lane selection without
    broadening that prior-phase behavior.
    """

    raw, raw_hash = _raw(
        db,
        "twse.isin.security_classification",
        "twse-isin-official",
        f"independent-class:{market_raw}:{at}",
        at,
    )
    row = dict(template)
    row.pop("created", None)
    row.update({
        "classification_evidence_id": f"fixture-class-{raw_hash[:24]}",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "market_raw": market_raw,
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(
            f"independent-class-normalized:{market_raw}:{at}".encode()
        ).hexdigest(),
        "fetched_at": at,
        "received_at": at,
        "available_at": at,
        "ingested_at": at,
        "source_record_reference": (
            f"twse.isin.security_classification:2330:{market_raw}"
        ),
        "revision_number": 1,
        "supersedes_classification_evidence_id": None,
        "identity_fingerprint": hashlib.sha256(json.dumps({
            "resource_id": template["resource_id"],
            "official_code": template["official_code"],
            "raw_payload_sha256": raw_hash,
            "classification_state": template["classification_state"],
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    })
    with sqlite3.connect(db) as conn:
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO eod_product_classification_evidence ({columns}) "
            f"VALUES ({placeholders})",
            tuple(row.values()),
        )
        cursor = conn.execute(
            "SELECT * FROM eod_product_classification_evidence "
            "WHERE classification_evidence_id = ?",
            (row["classification_evidence_id"],),
        )
        inserted = cursor.fetchone()
        column_names = [description[0] for description in cursor.description]
    return dict(zip(column_names, inserted))


def _add_tpex_identity(db) -> dict:
    universe = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    context = UniverseOperatorContext(
        "operator", "tpex-identity", "tpex-identity-lock", "tpex-identity-audit",
    )
    anchor = universe.allocate_instrument(
        venue="TPEX", official_code="2330", source_identity="tpex:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="TPEX fixture",
        context=context,
    )
    universe.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key="master:tpex:2330",
        revision_number=1,
        payload=_identity_payload(
            venue="TPEX",
            canonical_symbol="2330.TWO",
            source_reference="TPEX fixture",
            raw_resource_revision_id="raw-phase13-tpex_universe_master",
            raw_payload_sha256=hashlib.sha256(
                b"phase13:tpex-universe-master"
            ).hexdigest(),
        ),
        context=context,
        idempotency_key="tpex-identity-revision",
    )
    return anchor


def _tpex_source(
    repo: EodCloseRepository,
    raw: dict,
    raw_hash: str,
    *,
    date: str = TARGET_DATE,
    at: str = "2026-08-27T05:00:00Z",
) -> dict:
    return repo.add_source_snapshot({
        "resource_id": "tpex.eod.daily_close_quotes",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "logical_revision_key": f"tpex.eod.daily_close_quotes:{date}",
        "source_trade_date": date,
        "source_trade_date_status": "valid",
        "status": "available",
        "coverage_state": "complete",
        "row_count": 1,
        "source_date_min": date,
        "source_date_max": date,
        "fetched_at": at,
        "received_at": at,
        "available_at": at,
        "ingested_at": at,
        "source_url": "https://www.tpex.org.tw/en-us/announce/market/regular.html",
        "contract_version": "eod_close_v1",
        "parser_version": "1",
        "schema_fingerprint": hashlib.sha256(b"tpex-eod-schema").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(
            ("tpex-normalized:" + date).encode()
        ).hexdigest(),
        "source_record_reference": f"tpex.eod.daily_close_quotes:{date}",
        "source_scope": "tpex_mainboard_daily_close_quotes_without_fixed_price",
    })


def _tpex_observation(
    repo: EodCloseRepository,
    *,
    raw: dict,
    raw_hash: str,
    source: dict,
    classification: dict,
    anchor: dict,
    instrument_revision_id: str,
    at: str = "2026-08-27T05:02:00Z",
) -> dict:
    payload = {
        "resource_id": "tpex.eod.daily_close_quotes",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "source_snapshot_id": source["source_snapshot_id"],
        "classification_evidence_id": classification["classification_evidence_id"],
        "instrument_id": anchor["instrument_id"],
        "instrument_revision_id": instrument_revision_id,
        "venue": "TPEX",
        "official_code": "2330",
        "trade_date": source["source_trade_date"],
        "trade_date_status": "valid",
        "raw_close_text": "1005",
        "close_value": "1005",
        "raw_volume_text": "123456",
        "volume_value": "123456",
        "raw_trade_indication_text": "123456789",
        "trade_indication_value": "123456789",
        "currency": "TWD",
        "unit": "TWD_per_share",
        "price_semantics_version": "official_reported_close_v1",
        "product_scope": "supported_stock",
        "observation_status": "available",
        "public_eligibility_status": "eligible",
        "quality_status": "fresh",
        "quality_flags_json": "[]",
        "row_fingerprint": hashlib.sha256(
            f"tpex-row:{source['source_snapshot_id']}".encode()
        ).hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(
            f"tpex-normalized-row:{source['source_snapshot_id']}".encode()
        ).hexdigest(),
        "source_trading_scope": "tpex_mainboard_daily_close_quotes_without_fixed_price",
        "available_at": at,
        "ingested_at": at,
        "source_record_reference": f"tpex.eod.daily_close_quotes:{TARGET_DATE}:2330",
    }
    return repo.add_observation(payload)


def _add_orphan(repo: EodCloseRepository, *, raw: dict, raw_hash: str, source: dict, code: str = "9999"):
    payload = {
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "source_snapshot_id": source["source_snapshot_id"],
        "venue": "TWSE",
        "official_code": code,
        "trade_date": TARGET_DATE,
        "trade_date_status": "valid",
        "raw_close_text": "200",
        "close_value": "200",
        "raw_volume_text": "123456",
        "volume_value": "123456",
        "currency": "TWD",
        "unit": "TWD_per_share",
        "product_scope": "supported_stock",
        "observation_status": "available",
        "public_eligibility_status": "eligible",
        "quality_status": "fresh",
        "quality_flags_json": "[]",
        "row_fingerprint": hashlib.sha256(f"orphan:{code}".encode()).hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(f"orphan-normalized:{code}".encode()).hexdigest(),
        "source_trading_scope": "twse_whole_market_daily_close",
        "available_at": "2026-08-27T05:02:00Z",
        "ingested_at": "2026-08-27T05:02:00Z",
        "source_record_reference": f"twse.eod.stock_day_all:{TARGET_DATE}:{code}",
    }
    return repo.add_observation(payload)


def _get(client: TestClient, **params):
    return client.get(
        "/api/v2/market-context/eod-close/coverage/as-of",
        params={
            "venue": "TWSE",
            "source_trade_date": TARGET_DATE,
            "knowledge_cutoff_at": CUTOFF,
            **params,
        },
    )


def _assert_no_forbidden_keys(value):
    if isinstance(value, dict):
        assert not FORBIDDEN_KEYS.intersection(value)
        for child in value.values():
            _assert_no_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_keys(child)


def _add_listing_evidence(db, anchor: dict, *, event_date: str | None = None) -> None:
    UniverseRepository(str(db), guard=UniverseWriteGuard(True)).add_lifecycle_event(
        instrument_id=anchor["instrument_id"],
        event_type="listed",
        available_at="2026-08-27T06:00:00Z",
        ingested_at="2026-08-27T06:00:00Z",
        effective_at=None if event_date else "2026-08-26T01:00:00Z",
        event_date=event_date,
        source_reference="coverage first-observed timing fixture",
        reason="D applicability fixture",
        context=UniverseOperatorContext(
            "operator", "coverage-event", "coverage-lock", "coverage-audit",
        ),
    )


def test_domain_request_status_and_cursor_validation() -> None:
    request = CoverageRequest(
        venue="twse",
        source_trade_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        limit=1,
    )
    cursor = CoverageCursor(
        context=request.cursor_context(),
        last_key=("TWSE", "2330", 1, "denominator_candidate", "instrument-1"),
    ).encode()
    assert decode_cursor(cursor, request=request).last_key[1] == "2330"

    with pytest.raises(CoverageCursorError, match="cursor_malformed"):
        decode_cursor("not-a-cursor", request=request)

    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    envelope = json.loads(raw.decode("utf-8"))
    envelope["checksum"] = "0" * 64
    corrupted = base64.urlsafe_b64encode(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    with pytest.raises(CoverageCursorError, match="cursor_checksum_mismatch"):
        decode_cursor(corrupted, request=request)

    with pytest.raises(CoverageCursorError, match="cursor_context_mismatch"):
        decode_cursor(cursor, request=CoverageRequest(
            venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF, limit=2,
        ))

    impossible = CoverageCursor(
        context=request.cursor_context(),
        last_key=("TWSE", "2330", None, "denominator_candidate", "instrument-1"),
    ).encode()
    with pytest.raises(CoverageCursorError, match="cursor_impossible_tuple"):
        decode_cursor(impossible, request=request)

    with pytest.raises(ValueError, match="target_trade_date_after_cutoff"):
        CoverageRequest(
            venue="TWSE",
            source_trade_date="2026-08-28",
            knowledge_cutoff_at="2026-08-27T00:00:00Z",
        )
    with pytest.raises(ValueError, match="timezone"):
        CoverageRequest(
            venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at="2026-08-28T00:00:00",
        )


def test_valid_coverage_is_get_only_safe_and_not_a_completeness_assertion(tmp_path, monkeypatch) -> None:
    db, _, _, _, _, _, _, _ = _seed_coverage(tmp_path)
    monkeypatch.setenv("EOD_DB_PATH", str(db))
    client = TestClient(_app())

    response = _get(client)
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "eod_coverage_visibility_v1"
    assert body["status"] == "available"
    assert body["aggregate_assertion_state"] == "not_proven"
    assert body["aggregate_completeness_proven"] is False
    assert body["source"]["coverage_state"] == "complete"
    assert body["source"]["partial_proof_present"] is False
    assert body["aggregate"]["denominator_expected_count"] == 1
    assert body["aggregate"]["denominator_excluded_count"] == 0
    assert body["aggregate"]["denominator_unresolved_count"] == 0
    assert body["items"][0]["coverage_status"] == CoverageStatus.OBSERVED_ELIGIBLE.value
    assert body["items"][0]["official_code"] == "2330"
    assert body["items"][0]["observed_status"] == "available"
    assert set(body["items"][0]) == {
        "item_kind", "venue", "official_code", "canonical_symbol", "identity_epoch",
        "denominator_membership", "coverage_status", "reason_codes", "listing_status",
        "trading_state", "classification_status", "product_scope", "observed_trade_date",
        "observed_status", "source_record_reference",
    }
    _assert_no_forbidden_keys(body)

    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(response.request.url).status_code == 405


@pytest.mark.parametrize("venue", ["TWSE", "TPEX"])
def test_valid_venues_and_request_errors_are_explicit(tmp_path, monkeypatch, venue) -> None:
    db = tmp_path / f"{venue}.sqlite"
    apply_valuation_migration(str(db))
    monkeypatch.setenv("EOD_DB_PATH", str(db))
    client = TestClient(_app())

    valid = client.get(
        "/api/v2/market-context/eod-close/coverage/as-of",
        params={
            "venue": venue,
            "source_trade_date": TARGET_DATE,
            "knowledge_cutoff_at": CUTOFF,
        },
    )
    assert valid.status_code == 200
    assert valid.json()["status"] == "insufficient_data"

    assert _get(client, venue="NASDAQ").status_code == 422
    assert _get(client, source_trade_date="2026-08-28", knowledge_cutoff_at="2026-08-27T00:00:00Z").status_code == 422
    assert _get(client, knowledge_cutoff_at="2026-08-28T00:00:00").status_code == 422
    assert _get(client, limit=0).status_code == 422
    assert _get(client, limit=101).status_code == 422
    assert _get(client, limit="not-a-number").status_code == 422


def test_missing_storage_is_503_without_creating_a_database(tmp_path, monkeypatch) -> None:
    missing_db = tmp_path / "does-not-exist.sqlite"
    monkeypatch.setenv("EOD_DB_PATH", str(missing_db))
    response = _get(TestClient(_app()))
    assert response.status_code == 503
    assert response.json()["detail"] == "eod_coverage_storage_unavailable"
    assert not missing_db.exists()


def test_full_counts_are_stable_across_keyset_pages_and_orphan_is_disjoint(tmp_path) -> None:
    db, repo, _, raw, raw_hash, source, _, _ = _seed_coverage(tmp_path)
    _add_orphan(repo, raw=raw, raw_hash=raw_hash, source=source)
    service = EodCoverageService(str(db))

    first = service.as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF, limit=1,
    )
    assert first["aggregate"]["denominator_expected_count"] == 1
    assert first["aggregate"]["source_observation_orphan_count"] == 1
    assert first["aggregate"]["denominator_unresolved_count"] == 0
    assert first["next_cursor"]
    assert first["items"][0]["item_kind"] == "denominator_candidate"

    second = service.as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
        limit=1, cursor=first["next_cursor"],
    )
    assert second["aggregate"] == first["aggregate"]
    assert second["next_cursor"] is None
    assert second["items"][0]["item_kind"] == "source_observation_orphan"
    assert second["items"][0]["denominator_membership"] is None
    assert first["items"][0]["official_code"] != second["items"][0]["official_code"]


def test_source_selection_is_exact_no_fallback_and_partial_requires_proof(tmp_path) -> None:
    db, repo, _, raw, raw_hash, _, _, _ = _seed_coverage(
        tmp_path,
        source_kwargs={"date": "2026-08-26"},
    )
    unknown = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert unknown["status"] == "unknown"
    assert unknown["aggregate_assertion_state"] == "unknown"
    assert unknown["source"]["source_trade_date"] is None

    partial_db, partial_repo, _, partial_raw, partial_hash, _, _, _ = _seed_coverage(
        tmp_path / "partial",
        source_kwargs={
            "status": "partial",
            "coverage_state": "partial",
            "coverage_proof_type": "explicit_row_bound",
            "coverage_proof_reference": "fixture-proof-1",
        },
    )
    partial = EodCoverageService(str(partial_db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert partial["status"] == "partial"
    assert partial["aggregate_assertion_state"] == "partial"
    assert partial["source"]["partial_proof_present"] is True
    assert partial_repo is not None and partial_raw is not None and partial_hash is not None


def test_partial_source_preserves_exact_observed_eligibility_and_membership_counts(tmp_path) -> None:
    db, _, _, _, _, _, _, _ = _seed_coverage(
        tmp_path,
        source_kwargs={
            "status": "partial",
            "coverage_state": "partial",
            "coverage_proof_type": "explicit_row_bound",
            "coverage_proof_reference": "fixture-proof-observed",
        },
    )

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = result["items"][0]
    assert result["status"] == "partial"
    assert result["aggregate_assertion_state"] == "partial"
    assert item["coverage_status"] == "observed_eligible"
    assert item["denominator_membership"] == "expected"
    assert result["aggregate"]["denominator_expected_count"] == 1
    assert result["aggregate"]["denominator_excluded_count"] == 0
    assert result["aggregate"]["denominator_unresolved_count"] == 0
    assert result["aggregate"]["source_observation_orphan_count"] == 0


def test_partial_source_marks_unobserved_expected_candidate_source_partial(tmp_path) -> None:
    db, _, _, _, _, _, _, _ = _seed_coverage(
        tmp_path,
        source_kwargs={
            "status": "partial",
            "coverage_state": "partial",
            "coverage_proof_type": "explicit_row_bound",
            "coverage_proof_reference": "fixture-proof-unobserved",
        },
        include_observation=False,
    )

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = result["items"][0]
    assert result["status"] == "partial"
    assert result["aggregate_assertion_state"] == "partial"
    assert item["coverage_status"] == "source_partial"
    assert item["denominator_membership"] == "expected"
    assert result["aggregate"]["denominator_expected_count"] == 1
    assert result["aggregate"]["denominator_excluded_count"] == 0
    assert result["aggregate"]["denominator_unresolved_count"] == 0
    assert result["aggregate"]["source_observation_orphan_count"] == 0


def test_unknown_source_does_not_mask_proven_product_exclusion(tmp_path) -> None:
    db, _, _, _, _, _, _, _ = _seed_coverage(
        tmp_path,
        source_kwargs={"date": "2026-08-26"},
        classification_kwargs={"security_type_raw": "ETF"},
    )

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = result["items"][0]
    assert result["status"] == "unknown"
    assert result["aggregate_assertion_state"] == "unknown"
    assert item["coverage_status"] == "excluded_by_product_scope"
    assert item["denominator_membership"] == "excluded"
    assert result["aggregate"]["denominator_expected_count"] == 0
    assert result["aggregate"]["denominator_excluded_count"] == 1
    assert result["aggregate"]["denominator_unresolved_count"] == 0
    assert result["aggregate"]["source_observation_orphan_count"] == 0


def test_blocked_source_does_not_mask_classification_unresolved(tmp_path) -> None:
    db, repo, _, _, _, _, classification, _ = _seed_coverage(
        tmp_path,
        source_kwargs={"status": "schema_changed"},
    )
    _classification(
        repo,
        db,
        at="2026-08-27T04:01:00Z",
        state="blocked",
        revision_number=2,
        supersedes_classification_evidence_id=classification["classification_evidence_id"],
    )

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = result["items"][0]
    assert result["status"] == "blocked"
    assert result["aggregate_assertion_state"] == "blocked"
    assert item["coverage_status"] == "classification_unresolved"
    assert item["denominator_membership"] == "unresolved"
    assert result["aggregate"]["denominator_expected_count"] == 0
    assert result["aggregate"]["denominator_excluded_count"] == 0
    assert result["aggregate"]["denominator_unresolved_count"] == 1
    assert result["aggregate"]["source_observation_orphan_count"] == 0


def test_blocked_source_does_not_mask_observation_identity_unresolved(tmp_path) -> None:
    db, repo, anchor, raw, raw_hash, source, classification, _ = _seed_coverage(
        tmp_path,
        source_kwargs={"status": "schema_changed"},
    )
    universe = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    wrong_anchor = universe.allocate_instrument(
        venue="TWSE", official_code="9999", source_identity="twse:9999:future",
        first_observed_at="2026-08-29T00:00:00Z", source_reference="fixture",
        context=UniverseOperatorContext("operator", "identity", "identity-lock", "identity-audit"),
    )
    _observation(
        repo,
        raw=raw,
        raw_hash=raw_hash,
        source=source,
        classification=classification,
        anchor=wrong_anchor,
        at="2026-08-27T05:03:00Z",
        close="1006",
        revision_number=2,
        supersedes_observation_id=_observation_id(db),
    )

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = next(item for item in result["items"] if item["official_code"] == anchor["official_code"])
    assert result["status"] == "blocked"
    assert result["aggregate_assertion_state"] == "blocked"
    assert item["coverage_status"] == "identity_unresolved"
    assert item["denominator_membership"] == "expected"
    assert result["aggregate"]["denominator_expected_count"] == 1
    assert result["aggregate"]["denominator_excluded_count"] == 0
    assert result["aggregate"]["denominator_unresolved_count"] == 0
    assert result["aggregate"]["source_observation_orphan_count"] == 0


def test_post_k_lifecycle_and_same_day_event_do_not_rewrite_target_date(tmp_path) -> None:
    db, _, anchor, _, _, _, _, _ = _seed_coverage(tmp_path)
    universe = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    context = UniverseOperatorContext("operator", "coverage-event", "coverage-lock", "coverage-audit")
    universe.add_lifecycle_event(
        instrument_id=anchor["instrument_id"], event_type="terminated",
        available_at="2026-08-27T06:00:00Z", ingested_at="2026-08-27T06:00:00Z",
        effective_at="2026-08-28T01:00:00Z", event_date="2026-08-28",
        source_reference="coverage fixture", reason="after target D", context=context,
    )
    service = EodCoverageService(str(db))
    before = service.as_of(venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF)
    assert before["items"][0]["coverage_status"] == "observed_eligible"
    assert "excluded_by_lifecycle" not in before["items"][0]["reason_codes"]

    db_same, _, anchor_same, _, _, _, _, _ = _seed_coverage(tmp_path / "same-day")
    universe_same = UniverseRepository(str(db_same), guard=UniverseWriteGuard(True))
    universe_same.add_lifecycle_event(
        instrument_id=anchor_same["instrument_id"], event_type="terminated",
        available_at="2026-08-27T06:00:00Z", ingested_at="2026-08-27T06:00:00Z",
        event_date=TARGET_DATE, source_reference="coverage fixture", reason="same day unresolved", context=context,
    )
    same_day = EodCoverageService(str(db_same)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert same_day["items"][0]["coverage_status"] == "identity_unresolved"
    assert same_day["items"][0]["denominator_membership"] == "unresolved"


def test_date_only_event_uses_taipei_cutoff_date_for_k_visibility(tmp_path) -> None:
    db, _, anchor, _, _, _, _, _ = _seed_coverage(tmp_path)
    universe = UniverseRepository(str(db), guard=UniverseWriteGuard(True))
    context = UniverseOperatorContext("operator", "coverage-event", "coverage-lock", "coverage-audit")
    universe.add_lifecycle_event(
        instrument_id=anchor["instrument_id"], event_type="terminated",
        available_at="2026-08-27T06:00:00Z", ingested_at="2026-08-27T06:00:00Z",
        event_date=TARGET_DATE, source_reference="coverage fixture", reason="same local K date", context=context,
    )
    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE,
        knowledge_cutoff_at="2026-08-27T08:00:00Z",
    )
    assert result["items"][0]["coverage_status"] == "identity_unresolved"
    assert result["items"][0]["denominator_membership"] == "unresolved"


def test_first_observed_after_d_but_visible_at_k_keeps_d_applicable_candidate(tmp_path) -> None:
    db, _, anchor, _, _, _, _, _ = _seed_coverage(
        tmp_path, first_observed_at="2026-08-27T23:00:00Z",
    )
    _add_listing_evidence(db, anchor)

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    assert sum(
        result["aggregate"][key]
        for key in (
            "denominator_expected_count",
            "denominator_excluded_count",
            "denominator_unresolved_count",
        )
    ) == 1
    assert result["aggregate"]["denominator_expected_count"] == 1
    assert result["aggregate"]["source_observation_orphan_count"] == 0
    assert result["items"][0]["denominator_membership"] == "expected"
    assert result["items"][0]["listing_status"] == "listed"


def test_first_observed_after_d_without_d_applicability_stays_unresolved(tmp_path) -> None:
    db, _, anchor, _, _, _, _, _ = _seed_coverage(
        tmp_path, first_observed_at="2026-08-27T23:00:00Z",
    )
    _add_listing_evidence(db, anchor, event_date=TARGET_DATE)

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    assert sum(
        result["aggregate"][key]
        for key in (
            "denominator_expected_count",
            "denominator_excluded_count",
            "denominator_unresolved_count",
        )
    ) == 1
    assert result["aggregate"]["denominator_unresolved_count"] == 1
    assert result["aggregate"]["source_observation_orphan_count"] == 0
    assert result["items"][0]["denominator_membership"] == "unresolved"
    assert result["items"][0]["coverage_status"] == "identity_unresolved"


def test_first_observed_after_d_does_not_orphan_exact_d_observation(tmp_path) -> None:
    db, _, anchor, _, _, _, _, _ = _seed_coverage(
        tmp_path, first_observed_at="2026-08-27T23:00:00Z",
    )
    _add_listing_evidence(db, anchor)

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = result["items"][0]
    assert item["item_kind"] == "denominator_candidate"
    assert item["coverage_status"] == "observed_eligible"
    assert item["observed_trade_date"] == TARGET_DATE
    assert result["aggregate"]["source_observation_orphan_count"] == 0


def test_operational_event_applicability_is_bounded_by_target_date(tmp_path) -> None:
    context = UniverseOperatorContext("operator", "coverage-event", "coverage-lock", "coverage-audit")

    before_db, _, before_anchor, _, _, _, _, _ = _seed_coverage(tmp_path / "before")
    UniverseRepository(str(before_db), guard=UniverseWriteGuard(True)).add_operational_event(
        instrument_id=before_anchor["instrument_id"], trading_state="suspended",
        available_at="2026-08-27T06:00:00Z", ingested_at="2026-08-27T06:00:00Z",
        effective_at="2026-08-26T01:00:00Z", source_reference="coverage fixture",
        reason="before target D", context=context,
    )
    before = EodCoverageService(str(before_db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert before["items"][0]["coverage_status"] == "excluded_by_operational_state"

    after_db, _, after_anchor, _, _, _, _, _ = _seed_coverage(tmp_path / "after")
    UniverseRepository(str(after_db), guard=UniverseWriteGuard(True)).add_operational_event(
        instrument_id=after_anchor["instrument_id"], trading_state="suspended",
        available_at="2026-08-27T06:00:00Z", ingested_at="2026-08-27T06:00:00Z",
        effective_at="2026-08-28T01:00:00Z", source_reference="coverage fixture",
        reason="after target D", context=context,
    )
    after = EodCoverageService(str(after_db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert after["items"][0]["coverage_status"] == "observed_eligible"

    same_db, _, same_anchor, _, _, _, _, _ = _seed_coverage(tmp_path / "same")
    UniverseRepository(str(same_db), guard=UniverseWriteGuard(True)).add_operational_event(
        instrument_id=same_anchor["instrument_id"], trading_state="suspended",
        available_at="2026-08-27T06:00:00Z", ingested_at="2026-08-27T06:00:00Z",
        effective_at="2026-08-27T01:00:00Z", source_reference="coverage fixture",
        reason="same target D", context=context,
    )
    same = EodCoverageService(str(same_db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert same["items"][0]["coverage_status"] == "identity_unresolved"
    assert same["items"][0]["denominator_membership"] == "unresolved"


def test_resumed_lifecycle_event_has_one_deterministic_operational_state(tmp_path) -> None:
    db, _, anchor, _, _, _, _, _ = _seed_coverage(tmp_path)
    UniverseRepository(str(db), guard=UniverseWriteGuard(True)).add_lifecycle_event(
        instrument_id=anchor["instrument_id"], event_type="resumed",
        available_at="2026-08-27T06:00:00Z", ingested_at="2026-08-27T06:00:00Z",
        effective_at="2026-08-26T01:00:00Z", source_reference="coverage fixture",
        reason="normal trading resumed", context=UniverseOperatorContext(
            "operator", "coverage-event", "coverage-lock", "coverage-audit",
        ),
    )
    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert result["items"][0]["trading_state"] == "normal"
    assert result["items"][0]["coverage_status"] == "observed_eligible"


@pytest.mark.parametrize(
    ("close", "volume", "expected_reason"),
    [("0", "123456", "close_unusable"), ("1005", "0", "volume_unusable"), ("1005", "not-a-number", "volume_unusable")],
)
def test_invalid_close_or_volume_is_observed_ineligible(tmp_path, close, volume, expected_reason) -> None:
    db, repo, anchor, raw, raw_hash, source, classification, _ = _seed_coverage(tmp_path)
    _observation(
        repo,
        raw=raw,
        raw_hash=raw_hash,
        source=source,
        classification=classification,
        anchor=anchor,
        instrument_revision_id=_identity_revision_id(db, anchor["instrument_id"]),
        at="2026-08-27T05:03:00Z",
        close=close,
        volume=volume,
        observation_status="available",
        public_eligibility_status="eligible",
        revision_number=2,
        supersedes_observation_id=_observation_id(db),
    )
    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    item = result["items"][0]
    assert item["coverage_status"] == "observed_ineligible"
    assert expected_reason in item["reason_codes"]


def _observation_id(db) -> str:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT close_observation_id FROM eod_close_observations ORDER BY close_observation_id LIMIT 1"
        ).fetchone()[0]


def test_classification_exclusion_and_foreign_currency_are_fail_closed(tmp_path) -> None:
    db, _, _, _, _, _, _, _ = _seed_coverage(
        tmp_path,
        classification_kwargs={"security_type_raw": "ETF"},
    )
    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert result["items"][0]["coverage_status"] == "excluded_by_product_scope"
    assert result["items"][0]["denominator_membership"] == "excluded"

    db2, repo2, _, _, _, _, foreign_classification, _ = _seed_coverage(tmp_path / "foreign")
    first_foreign = foreign_classification
    second_foreign = _classification(
        repo2,
        db2,
        at="2026-08-27T04:01:00Z",
        currency_raw="USD",
        revision_number=2,
        supersedes_classification_evidence_id=first_foreign["classification_evidence_id"],
    )
    foreign_result = EodCoverageService(str(db2)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert foreign_result["items"][0]["coverage_status"] == "classification_unresolved"
    assert foreign_result["items"][0]["denominator_membership"] == "unresolved"
    assert second_foreign["classification_evidence_id"]

    db3, repo3, _, _, _, _, late_classification, _ = _seed_coverage(tmp_path / "late-correction")
    late = _classification(
        repo3,
        db3,
        at="2026-08-28T00:00:00Z",
        security_type_raw="ETF",
        revision_number=2,
        supersedes_classification_evidence_id=late_classification["classification_evidence_id"],
    )
    late_result = EodCoverageService(str(db3)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert late_result["items"][0]["coverage_status"] == "classification_unresolved"
    assert "classification_d_applicability_unresolved" in late_result["items"][0]["reason_codes"]
    assert late["classification_evidence_id"]

    unresolved = eod_coverage_visibility_status_v1(
        source_state="usable", denominator_candidate_count=1,
        denominator_expected_count=0, denominator_excluded_count=0,
        denominator_unresolved_count=1,
    )
    assert unresolved["status"] == "insufficient_data"
    assert repo2 is not None and db2 is not None


@pytest.mark.parametrize(
    "correction_available_at",
    [
        "2026-08-26T04:01:00Z",
        "2026-08-27T04:01:00Z",
        "2026-08-27T16:01:00Z",
    ],
)
def test_material_classifier_correction_without_d_effective_semantics_is_unresolved(
    tmp_path, correction_available_at: str,
) -> None:
    db, repo, _, _, _, _, classification, _ = _seed_coverage(tmp_path)
    corrected = _classification(
        repo,
        db,
        at=correction_available_at,
        available_at=correction_available_at,
        security_type_raw="ETF",
        revision_number=2,
        supersedes_classification_evidence_id=classification["classification_evidence_id"],
    )

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = result["items"][0]
    assert item["coverage_status"] == "classification_unresolved"
    assert item["denominator_membership"] == "unresolved"
    assert "classification_d_applicability_unresolved" in item["reason_codes"]
    assert result["aggregate"]["denominator_expected_count"] == 0
    assert result["aggregate"]["denominator_excluded_count"] == 0
    assert result["aggregate"]["denominator_unresolved_count"] == 1
    assert corrected["classification_evidence_id"]


def test_non_material_classifier_correction_keeps_latest_k_visible_state(tmp_path) -> None:
    db, repo, _, _, _, _, classification, _ = _seed_coverage(tmp_path)
    corrected = _classification(
        repo,
        db,
        at="2026-08-27T16:01:00Z",
        available_at="2026-08-27T16:01:00Z",
        revision_number=2,
        supersedes_classification_evidence_id=classification["classification_evidence_id"],
    )

    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    item = result["items"][0]
    assert item["coverage_status"] == "observed_eligible"
    assert item["denominator_membership"] == "expected"
    assert result["aggregate"]["denominator_expected_count"] == 1
    assert result["aggregate"]["denominator_unresolved_count"] == 0
    assert corrected["classification_evidence_id"]


def test_same_code_classifier_selection_is_separate_for_twse_and_tpex(tmp_path) -> None:
    db, repo, _, _, _, _, classification, _ = _seed_coverage(tmp_path)
    tpex_anchor = _add_tpex_identity(db)
    tpex_raw, tpex_hash = _raw(
        db,
        "tpex.eod.daily_close_quotes",
        "tpex-universe-official",
        "tpex-coverage-2026-08-27",
        "2026-08-27T05:00:00Z",
    )
    tpex_source = _tpex_source(repo, tpex_raw, tpex_hash)
    tpex_classification = _insert_independent_classification_root(
        db,
        classification,
        market_raw="上櫃",
    )
    _tpex_observation(
        repo,
        raw=tpex_raw,
        raw_hash=tpex_hash,
        source=tpex_source,
        classification=tpex_classification,
        anchor=tpex_anchor,
        instrument_revision_id=_identity_revision_id(db, tpex_anchor["instrument_id"]),
    )

    service = EodCoverageService(str(db))
    twse = service.as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    tpex = service.as_of(
        venue="TPEX", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    for result in (twse, tpex):
        assert result["status"] == "available"
        assert result["aggregate"]["denominator_expected_count"] == 1
        assert result["aggregate"]["denominator_unresolved_count"] == 0
        assert result["items"][0]["official_code"] == "2330"
        assert result["items"][0]["coverage_status"] == "observed_eligible"
        assert result["items"][0]["denominator_membership"] == "expected"
    assert twse["items"][0]["venue"] == "TWSE"
    assert tpex["items"][0]["venue"] == "TPEX"


@pytest.mark.parametrize("child_state", ["accepted", "blocked", "revoked"])
def test_cross_market_supersedes_lineage_never_resurrects_parent(
    tmp_path, child_state: str,
) -> None:
    db, repo, _, _, _, _, parent, _ = _seed_coverage(tmp_path)
    child = _classification(
        repo,
        db,
        at="2026-08-27T04:01:00Z",
        state=child_state,
        market_raw="上櫃",
        revision_number=2,
        supersedes_classification_evidence_id=parent["classification_evidence_id"],
    )

    tpex_anchor = _add_tpex_identity(db)
    tpex_raw, tpex_hash = _raw(
        db,
        "tpex.eod.daily_close_quotes",
        "tpex-universe-official",
        "tpex-cross-market-lineage-2026-08-27",
        "2026-08-27T05:00:00Z",
    )
    tpex_source = _tpex_source(repo, tpex_raw, tpex_hash)
    _tpex_observation(
        repo,
        raw=tpex_raw,
        raw_hash=tpex_hash,
        source=tpex_source,
        classification=child,
        anchor=tpex_anchor,
        instrument_revision_id=_identity_revision_id(db, tpex_anchor["instrument_id"]),
    )

    service = EodCoverageService(str(db))
    twse = service.as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    tpex = service.as_of(
        venue="TPEX", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )

    for result in (twse, tpex):
        item = result["items"][0]
        assert result["status"] == "insufficient_data"
        assert result["aggregate"]["denominator_expected_count"] == 0
        assert result["aggregate"]["denominator_excluded_count"] == 0
        assert result["aggregate"]["denominator_unresolved_count"] == 1
        assert item["coverage_status"] == "classification_unresolved"
        assert item["denominator_membership"] == "unresolved"
    assert twse["items"][0]["venue"] == "TWSE"
    assert tpex["items"][0]["venue"] == "TPEX"
    assert child["classification_evidence_id"] != parent["classification_evidence_id"]


def test_bound_invalid_source_date_is_blocked_without_older_fallback(tmp_path) -> None:
    db, repo, _, raw, raw_hash, _, _, _ = _seed_coverage(
        tmp_path,
        source_kwargs={"date": "2026-08-26"},
    )
    repo.add_source_snapshot({
        "resource_id": "twse.eod.stock_day_all",
        "raw_resource_revision_id": raw["raw_resource_revision_id"],
        "logical_revision_key": "twse.eod.stock_day_all:invalid-bound",
        "source_trade_date": "2026-08-28",
        "source_trade_date_status": "invalid",
        "status": "available",
        "coverage_state": "complete",
        "row_count": 1,
        "source_date_min": None,
        "source_date_max": None,
        "fetched_at": "2026-08-27T05:00:00Z",
        "received_at": "2026-08-27T05:00:00Z",
        "available_at": "2026-08-27T05:00:00Z",
        "ingested_at": "2026-08-27T05:00:00Z",
        "source_url": "https://example.invalid/eod",
        "contract_version": "eod_close_v1",
        "parser_version": "1",
        "schema_fingerprint": hashlib.sha256(b"invalid-source-schema").hexdigest(),
        "raw_payload_sha256": raw_hash,
        "normalized_payload_sha256": hashlib.sha256(b"invalid-source-normalized").hexdigest(),
        "query_dimensions_json": json.dumps({"target_trade_date": TARGET_DATE}),
        "source_record_reference": "twse.eod.stock_day_all:invalid-bound",
        "source_scope": "twse_whole_market_daily_close",
    })
    result = EodCoverageService(str(db)).as_of(
        venue="TWSE", source_trade_date=TARGET_DATE, knowledge_cutoff_at=CUTOFF,
    )
    assert result["status"] == "blocked"
    assert result["aggregate_assertion_state"] == "blocked"
    assert result["source"]["source_status"] == "blocked"
    assert result["source"]["source_trade_date"] is None
    assert result["aggregate"]["item_status_counts"] == {"source_blocked": 1}
