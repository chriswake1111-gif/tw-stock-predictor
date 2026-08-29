from __future__ import annotations

import base64
import json
import socket
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.neutral_batch_market_context import (
    NeutralBatchMarketContextCursor,
    NeutralBatchMarketContextCursorError,
    NeutralBatchMarketContextRequest,
    decode_neutral_batch_cursor,
    neutral_batch_context_assembly_v1,
)
from src.repositories.eod_close_repository import EodCloseRepository
from src.repositories.neutral_batch_market_context_repository import (
    NeutralBatchMarketContextRepository,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.services.neutral_batch_market_context_service import (
    NeutralBatchMarketContextService,
)
from src.api.routes.v2_market_context import router
from tests.test_phase15_eod_coverage import (
    TARGET_DATE,
    _add_orphan,
    _add_source_revision,
    _observation,
    _raw,
    _seed_coverage,
)


CUTOFF = "2026-08-28T00:00:00Z"
FORBIDDEN_KEYS = {
    "instrument_id",
    "universe_revision_id",
    "instrument_revision_id",
    "source_snapshot_id",
    "classification_evidence_id",
    "raw_payload",
    "raw_payload_sha256",
    "normalized_payload_sha256",
    "idempotency_key",
    "audit_actor",
    "lock",
    "recommendation",
    "ranking",
    "score",
    "return",
    "target",
    "confidence",
    "signal",
    "valuation",
    "volume",
}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _get(client: TestClient, **params):
    return client.get(
        "/api/v2/market-context/batch/as-of",
        params={
            "market_date": TARGET_DATE,
            "knowledge_cutoff_at": CUTOFF,
            "venue_scope": "TWSE_TPEX",
            **params,
        },
    )


def _assert_no_forbidden_keys(value, *, allow_close_value: bool = False):
    forbidden = FORBIDDEN_KEYS - ({"close_value"} if allow_close_value else set())
    if isinstance(value, dict):
        assert not forbidden.intersection(value)
        for child in value.values():
            _assert_no_forbidden_keys(child, allow_close_value=allow_close_value)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_keys(child, allow_close_value=allow_close_value)


class CountingReadStorage:
    def __init__(self, db_path):
        self.inner = EodCloseRepository(str(db_path))
        self.statements: list[str] = []

    @contextmanager
    def read_transaction(self):
        with self.inner.read_transaction() as conn:
            conn.set_trace_callback(self.statements.append)
            yield conn


class ReadOnlyGuardStorage(CountingReadStorage):
    @contextmanager
    def read_transaction(self):
        with self.inner.read_transaction() as conn:
            conn.set_authorizer(self._authorizer)
            yield conn

    @staticmethod
    def _authorizer(action, *_args):
        writes = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_VIEW,
        }
        return sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK


def test_request_mapping_cursor_and_assembly_matrix_are_locked() -> None:
    request = NeutralBatchMarketContextRequest(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="twse_tpex",
        limit=2,
    )
    assert request.venues == ("TWSE", "TPEX")
    assert request.knowledge_cutoff_at == "2026-08-28T00:00:00.000000Z"
    assert request.venue_mappings[0].resource_id == "twse.eod.stock_day_all"
    assert request.venue_mappings[1].resource_id == "tpex.eod.daily_close_quotes"

    cursor = NeutralBatchMarketContextCursor(
        context=request.cursor_context(),
        last_key=(0, "TWSE", 0, "0010", 0, 1, 0, "stable"),
    ).encode()
    assert decode_neutral_batch_cursor(cursor, request=request).last_key[3] == "0010"
    with pytest.raises(NeutralBatchMarketContextCursorError, match="cursor_malformed"):
        decode_neutral_batch_cursor("not-a-token", request=request)

    with pytest.raises(ValueError, match="market_date_after_cutoff"):
        NeutralBatchMarketContextRequest(
            market_date="2026-08-29",
            knowledge_cutoff_at="2026-08-28T00:00:00Z",
        )
    with pytest.raises(ValueError, match="timezone"):
        NeutralBatchMarketContextRequest(
            market_date=TARGET_DATE,
            knowledge_cutoff_at="2026-08-28T00:00:00",
        )

    for projection_state, expected in (
        ("blocked", "blocked"),
        ("empty", "insufficient_data"),
        ("entirely_unresolved", "insufficient_data"),
        ("usable", "available"),
    ):
        result = neutral_batch_context_assembly_v1(
            source_state="usable",
            denominator_projection_state=projection_state,
            aggregate={
                "denominator_candidate_count": 1,
                "denominator_unresolved_count": 0,
            },
        )
        assert result["status"] == expected
    partial = neutral_batch_context_assembly_v1(
        source_state="partial",
        denominator_projection_state="usable",
        partial_proof_present=True,
        aggregate={"denominator_candidate_count": 1, "denominator_unresolved_count": 0},
    )
    assert partial["status"] == "partial"


def test_empty_combined_route_is_safe_and_get_only(tmp_path, monkeypatch) -> None:
    db = tmp_path / "empty.sqlite"
    apply_valuation_migration(str(db))
    monkeypatch.setenv("EOD_DB_PATH", str(db))
    response = _get(TestClient(_app()))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "contract_version", "mode", "request", "status", "per_venue",
        "aggregate", "items", "limit", "next_cursor",
    }
    assert body["status"] == "insufficient_data"
    assert set(body["request"]) == {
        "market_date", "knowledge_cutoff_at", "venue_scope",
        "d_k_policy_version", "order_version",
    }
    assert set(body["per_venue"]) == {"TWSE", "TPEX"}
    assert set(body["aggregate"]) == {
        "denominator_candidate_count", "denominator_expected_count",
        "denominator_excluded_count", "denominator_unresolved_count",
        "item_status_counts", "source_observation_orphan_count",
        "aggregate_completeness_proven",
    }
    assert body["aggregate"]["aggregate_completeness_proven"] is False
    for venue in ("TWSE", "TPEX"):
        assert set(body["per_venue"][venue]) == {"source", "assembly_status", "aggregate"}
        assert body["per_venue"][venue]["source"]["state"] == "unknown"
        assert body["per_venue"][venue]["source"]["source_scope_completeness_proven"] is False
        assert body["per_venue"][venue]["aggregate"]["aggregate_completeness_proven"] is False
    _assert_no_forbidden_keys(body)

    for method in ("post", "put", "patch", "delete"):
        assert getattr(TestClient(_app()), method)(response.request.url).status_code == 405

    assert _get(TestClient(_app()), venue_scope="NASDAQ").status_code == 422
    assert _get(TestClient(_app()), market_date="2026-08-29", knowledge_cutoff_at="2026-08-28T00:00:00Z").status_code == 422
    assert _get(TestClient(_app()), limit=101).status_code == 422


def test_combined_projection_preserves_source_scope_counts_and_bounded_shared_cursor(tmp_path) -> None:
    db, repo, _, raw, raw_hash, source, _, _ = _seed_coverage(tmp_path)
    _add_orphan(repo, raw=raw, raw_hash=raw_hash, source=source)
    counting = CountingReadStorage(db)
    service = NeutralBatchMarketContextService(
        repository=NeutralBatchMarketContextRepository(storage=counting)
    )

    first = service.as_of(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="TWSE_TPEX",
        limit=1,
    )
    assert first["status"] == "partial"
    assert first["per_venue"]["TWSE"]["source"]["state"] == "usable"
    assert first["per_venue"]["TPEX"]["assembly_status"] == "insufficient_data"
    assert first["aggregate"]["denominator_candidate_count"] == 1
    assert first["aggregate"]["source_observation_orphan_count"] == 1
    assert len(first["items"]) == 1
    assert first["items"][0]["item_kind"] == "denominator_candidate"
    assert first["items"][0]["eod_close"]["close_value"] == "1005"
    assert first["items"][0]["eod_close"]["currency"] == "TWD"
    assert "volume" not in first["items"][0]["eod_close"]
    _assert_no_forbidden_keys(first, allow_close_value=True)
    assert first["next_cursor"]
    first_statements = [
        statement for statement in counting.statements
        if statement.lstrip().upper().startswith(("WITH", "SELECT"))
    ]
    assert len(first_statements) <= 6
    counting.statements.clear()

    second = service.as_of(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="TWSE_TPEX",
        limit=1,
        cursor=first["next_cursor"],
    )
    assert second["items"][0]["item_kind"] == "source_observation_orphan"
    assert second["items"][0]["denominator_membership"] is None
    assert second["items"][0]["item_state"] == "source_observation_unmapped"
    assert second["items"][0]["coverage_status"] == "source_observation_unmapped"
    assert second["items"][0]["eod_close"] is None
    assert second["next_cursor"] is None
    assert second["aggregate"] == first["aggregate"]

    # The second request is another fixed-size read plan, not a query per
    # item.
    assert len([
        statement
        for statement in counting.statements
        if statement.lstrip().upper().startswith(("WITH", "SELECT"))
    ]) <= 6


def test_source_lineage_blocks_request_bound_child_without_resurrecting_parent(tmp_path) -> None:
    db, repo, _, raw, raw_hash, source, _, _ = _seed_coverage(tmp_path)
    _add_source_revision(
        repo,
        db,
        source,
        source_trade_date="2026-08-28",
        source_trade_date_status="invalid",
        query_dimensions={"target_trade_date": TARGET_DATE},
        at="2026-08-27T06:00:00Z",
    )
    body = NeutralBatchMarketContextService(str(db)).as_of(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="TWSE",
    )
    venue = body["per_venue"]["TWSE"]
    assert venue["source"]["state"] == "blocked"
    assert venue["source"]["reason_codes"] == ["source_date_in_future_or_invalid"]
    assert body["items"][0]["coverage_status"] == "source_blocked"
    assert body["items"][0]["item_state"] == "blocked"
    assert body["items"][0]["eod_close"] is None
    assert body["status"] == "blocked"


def test_partial_source_and_observed_ineligible_keep_distinct_typed_states(tmp_path) -> None:
    db, repo, anchor, raw, raw_hash, source, classification, _ = _seed_coverage(
        tmp_path,
        source_kwargs={
            "status": "partial",
            "coverage_state": "partial",
            "coverage_proof_type": "coverage_manifest",
            "coverage_proof_reference": "fixture-proof",
        },
        include_observation=False,
    )
    partial = NeutralBatchMarketContextService(str(db)).as_of(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="TWSE",
    )
    assert partial["per_venue"]["TWSE"]["source"]["state"] == "partial"
    assert partial["per_venue"]["TWSE"]["assembly_status"] == "partial"
    assert partial["items"][0]["coverage_status"] == "source_partial"
    assert partial["items"][0]["item_state"] == "partial"
    assert partial["items"][0]["eod_close"] is None

    ineligible_raw, ineligible_hash = _raw(
        db,
        "twse.eod.stock_day_all",
        "twse-universe-official",
        "ineligible-row",
        "2026-08-27T05:03:00Z",
    )
    # The observation helper accepts an existing source/classification and
    # writes a distinct immutable row identity for this regression fixture.
    _observation(
        repo,
        raw=ineligible_raw,
        raw_hash=ineligible_hash,
        source=source,
        classification=classification,
        anchor=anchor,
        at="2026-08-27T05:03:00Z",
        public_eligibility_status="ineligible",
    )
    ineligible = NeutralBatchMarketContextService(str(db)).as_of(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="TWSE",
    )
    assert ineligible["items"][0]["coverage_status"] == "observed_ineligible"
    assert ineligible["items"][0]["item_state"] == "unknown"
    assert ineligible["per_venue"]["TWSE"]["assembly_status"] == "partial"
    assert ineligible["items"][0]["eod_close"] is None


def test_read_path_rejects_writes_and_does_not_open_network(tmp_path, monkeypatch) -> None:
    db, *_ = _seed_coverage(tmp_path)
    storage = ReadOnlyGuardStorage(db)
    service = NeutralBatchMarketContextService(
        repository=NeutralBatchMarketContextRepository(storage=storage)
    )

    def fail_network(*_args, **_kwargs):
        raise AssertionError("network access is forbidden in Phase 16 read path")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    body = service.as_of(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="TWSE",
    )
    assert body["status"] == "available"


def test_cursor_integrity_and_context_binding_are_422(tmp_path) -> None:
    db, repo, _, raw, raw_hash, source, _, _ = _seed_coverage(tmp_path)
    _add_orphan(repo, raw=raw, raw_hash=raw_hash, source=source)
    service = NeutralBatchMarketContextService(str(db))
    first = service.as_of(
        market_date=TARGET_DATE,
        knowledge_cutoff_at=CUTOFF,
        venue_scope="TWSE",
        limit=1,
    )
    token = first["next_cursor"]
    assert token
    raw_token = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    envelope = json.loads(raw_token.decode("utf-8"))
    envelope["checksum"] = "0" * 64
    corrupted = base64.urlsafe_b64encode(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    with pytest.raises(NeutralBatchMarketContextCursorError, match="cursor_checksum_mismatch"):
        decode_neutral_batch_cursor(
            corrupted,
            request=NeutralBatchMarketContextRequest(
                market_date=TARGET_DATE,
                knowledge_cutoff_at=CUTOFF,
                venue_scope="TWSE",
                limit=1,
            ),
        )
    with pytest.raises(NeutralBatchMarketContextCursorError, match="cursor_context_mismatch"):
        decode_neutral_batch_cursor(
            token,
            request=NeutralBatchMarketContextRequest(
                market_date=TARGET_DATE,
                knowledge_cutoff_at=CUTOFF,
                venue_scope="TPEX",
                limit=1,
            ),
        )
