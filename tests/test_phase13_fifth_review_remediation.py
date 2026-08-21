from __future__ import annotations

import hashlib

import pytest

from src.collectors.universe_collectors import (
    UniverseCollector,
    UniverseSourceRejected,
    parse_universe_payload,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseRepository
from src.services.universe_registry import UniverseResourceRegistry
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import seed_raw_provenance


def _ctx(name: str) -> UniverseOperatorContext:
    return UniverseOperatorContext("operator", f"run-{name}", f"lock-{name}", f"audit-{name}")


def _repo(tmp_path):
    db = tmp_path / "universe.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    return db, UniverseRepository(str(db), guard=UniverseWriteGuard(True))


def _add_instrument(repo: UniverseRepository, db, *, code: str, context: UniverseOperatorContext):
    anchor = repo.allocate_instrument(
        venue="TWSE", official_code=code, source_identity=f"twse:{code}:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    raw_hash = hashlib.sha256(b"phase13:twse-universe-master").hexdigest()
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key=f"master-{code}", revision_number=1,
        payload={
            "venue": "TWSE", "official_code": code, "canonical_symbol": f"{code}.TW",
            "display_name": f"Fixture {code}", "fetched_at": "2026-08-21T00:01:00Z",
            "received_at": "2026-08-21T00:01:00Z", "ingested_at": "2026-08-21T00:02:00Z",
            "available_at": "2026-08-21T00:01:00Z", "source_reference": "fixture",
            "status": "accepted", "freshness_status": "current",
            "freshness_mode": "official_cadence_window", "current_complete": True,
            "coverage_complete": True, "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash,
        },
        context=context, idempotency_key=f"revision-{code}",
    )
    return anchor


def test_tpex_company_current_is_manual_documentation_only():
    payload = [{"Date": "1150821", "SecuritiesCompanyCode": "6488", "CompanyName": "元大"}]
    with pytest.raises(UniverseSourceRejected, match="manual_source_contract_not_for_ingestion"):
        parse_universe_payload("tpex.company.current", payload)
    with pytest.raises(ValueError, match="manual_source_contract_not_for_ingestion"):
        UniverseResourceRegistry.validate_resource_key("tpex.company.current")
    with pytest.raises(UniverseSourceRejected, match="manual_source_contract_not_for_ingestion"):
        UniverseCollector(lambda *_args, **_kwargs: None).fetch_official(
            "tpex.company.current", url="https://www.tpex.org.tw/company/otcSearch",
        )


def test_list_uses_bounded_set_queries_and_stable_cursor(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("bounded")
    for offset in range(6):
        _add_instrument(repo, db, code=f"{2000 + offset}", context=context)

    original_connect = repo._connect
    observed_sql: list[str] = []

    def counted_connect():
        connection = original_connect()
        connection.set_trace_callback(
            lambda statement: observed_sql.append(statement)
            if statement.lstrip().upper().startswith(("SELECT", "WITH")) else None
        )
        return connection

    repo._connect = counted_connect  # type: ignore[method-assign]
    first = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=1)
    first_query_count = len(observed_sql)
    assert len(first["items"]) == 1
    assert first["next_cursor"]

    observed_sql.clear()
    second = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=1, cursor=first["next_cursor"],
    )
    assert len(second["items"]) == 1
    assert second["items"][0]["identity_reference"]["canonical_symbol"] > first["items"][0]["identity_reference"]["canonical_symbol"]

    typical = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=3)
    typical_next = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=3, cursor=typical["next_cursor"],
    )
    assert [item["identity_reference"]["canonical_symbol"] for item in typical["items"]] == [
        "2000.TW", "2001.TW", "2002.TW",
    ]
    assert [item["identity_reference"]["canonical_symbol"] for item in typical_next["items"]] == [
        "2003.TW", "2004.TW", "2005.TW",
    ]

    observed_sql.clear()
    page = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=100)
    large_page_query_count = len(observed_sql)
    assert len(page["items"]) == 6
    assert first_query_count <= 10
    assert large_page_query_count <= first_query_count + 1
