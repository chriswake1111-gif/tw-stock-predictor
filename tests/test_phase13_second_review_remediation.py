from __future__ import annotations

import logging
import sqlite3

import pytest

from src.collectors.universe_collectors import UniverseCollector, UniverseSourceRejected, parse_universe_payload
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseRepository
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard


def _ctx(name: str = "second") -> UniverseOperatorContext:
    return UniverseOperatorContext("operator", f"run-{name}", f"lock-{name}", f"audit-{name}")


def _repo(tmp_path):
    db = tmp_path / "universe.sqlite"
    apply_valuation_migration(str(db))
    return db, UniverseRepository(str(db), guard=UniverseWriteGuard(True))


def _instrument_payload(**overrides):
    value = {
        "venue": "TWSE", "official_code": "2330", "canonical_symbol": "2330.TW",
        "fetched_at": "2026-08-21T00:01:00Z", "received_at": "2026-08-21T00:01:00Z",
        "ingested_at": "2026-08-21T00:02:00Z", "available_at": "2026-08-21T00:01:00Z",
        "source_reference": "fixture", "status": "accepted", "freshness_status": "current",
        "freshness_mode": "official_cadence_window", "current_complete": True, "coverage_complete": True,
    }
    value.update(overrides)
    return value


def _resource_payload(**overrides):
    value = {
        "fetched_at": "2026-08-21T00:03:00Z", "received_at": "2026-08-21T00:03:00Z",
        "ingested_at": "2026-08-21T00:04:00Z", "available_at": "2026-08-21T00:03:00Z",
        "source_reference": "fixture-resource", "status": "provider_error", "reason": "provider unavailable",
        "freshness_status": "blocked", "freshness_mode": "event_observation", "current_complete": False,
        "coverage_complete": False, "venue": "TWSE",
    }
    value.update(overrides)
    return value


def test_additive_remediation_migration_is_versioned_and_rerunnable(tmp_path):
    db = tmp_path / "migration-rerun" / "universe.sqlite"
    first = apply_valuation_migration(str(db))
    second = apply_valuation_migration(str(db))
    assert first["additive_migration_ids"] == ["20260821_17_phase13_second_review_remediation"]
    assert second["additive_migration_ids"] == first["additive_migration_ids"]
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT checksum_sha256 FROM additive_schema_migrations WHERE version_id=?",
            ("20260821_17_phase13_second_review_remediation",),
        ).fetchone()[0]


def _anchor(repo: UniverseRepository, context: UniverseOperatorContext):
    return repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )


def test_historical_reference_and_operational_state_are_independent(tmp_path):
    _, repo = _repo(tmp_path)
    context = _ctx("dual")
    anchor = _anchor(repo, context)
    first = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
        revision_number=1, payload=_instrument_payload(), context=context, idempotency_key="s1",
    )
    second = repo.add_resource_revision(
        resource_id="twse-universe-master", logical_revision_key="master", revision_number=2,
        payload=_resource_payload(ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z", supersedes_revision_id=first["universe_revision_id"]),
        context=context, idempotency_key="s2",
    )
    after = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    assert after["identity_reference"]["instrument_id"] == anchor["instrument_id"]
    assert after["provenance"]["universe_revision_id"] == first["universe_revision_id"]
    assert after["provenance"]["operational_revision_id"] == second["universe_revision_id"]
    assert after["operational_freshness"]["latest_visible_state"] == "provider_error"
    assert after["status"] == "partial"

    before = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:03:30Z")
    assert before["provenance"]["operational_revision_id"] == first["universe_revision_id"]
    assert before["status"] == "available"

    future = repo.add_resource_revision(
        resource_id="twse-universe-master", logical_revision_key="master", revision_number=3,
        payload=_resource_payload(status="schema_changed", reason="future schema", ingested_at="2026-08-21T00:06:00Z", available_at="2026-08-21T00:05:00Z", supersedes_revision_id=second["universe_revision_id"]),
        context=context, idempotency_key="s3",
    )
    cutoff_before_future = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:05:30Z", current=True)
    assert cutoff_before_future["provenance"]["operational_revision_id"] == second["universe_revision_id"]
    assert future["status"] == "schema_changed"
    resolved = repo.resolve(official_code="2330", venue="TWSE", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    listed = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", venue="TWSE", limit=25)
    listed_item = next(item for item in listed["items"] if item["identity_reference"]["instrument_id"] == anchor["instrument_id"])
    assert resolved["identity_reference"] == after["identity_reference"]
    assert resolved["provenance"]["operational_revision_id"] == after["provenance"]["operational_revision_id"]
    assert listed_item["provenance"]["operational_revision_id"] == after["provenance"]["operational_revision_id"]


@pytest.mark.parametrize("status", ["partial", "schema_changed"])
def test_resource_failure_without_instrument_row_is_visible_per_venue(tmp_path, status):
    _, repo = _repo(tmp_path)
    context = _ctx(status)
    anchor = _anchor(repo, context)
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
        revision_number=1, payload=_instrument_payload(), context=context, idempotency_key="twse-s1",
    )
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="tpex.spendi.history", revision_number=1,
        payload=_resource_payload(venue="TPEX", status=status, reason=f"{status} fixture"),
        context=context, idempotency_key=f"tpex-{status}",
    )
    result = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert result["per_venue_status"]["TPEX"] == ("needs_human_input" if status == "schema_changed" else "partial")
    assert result["items"]
    assert result["status"] == ("needs_human_input" if status == "schema_changed" else "partial")
    twse_only = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", venue="TWSE", limit=25)
    assert twse_only["per_venue_status"]["TWSE"] == "available"


def test_reverse_venue_failure_does_not_hide_healthy_venue(tmp_path):
    _, repo = _repo(tmp_path)
    context = _ctx("reverse")
    tpex_anchor = repo.allocate_instrument(
        venue="TPEX", official_code="6488", source_identity="tpex:6488:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    repo.add_revision(
        instrument_id=tpex_anchor["instrument_id"], resource_id="tpex-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(venue="TPEX", official_code="6488", canonical_symbol="6488.TWO"),
        context=context, idempotency_key="reverse-tpex",
    )
    repo.add_resource_revision(
        resource_id="twse-universe-master", logical_revision_key="master", revision_number=1,
        payload=_resource_payload(venue="TWSE", status="provider_error", reason="twse offline"),
        context=context, idempotency_key="reverse-twse",
    )
    result = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert result["per_venue_status"] == {"TWSE": "partial", "TPEX": "available"}
    assert result["status"] == "partial"


def test_empty_complete_and_empty_blocked_lists_are_distinct(tmp_path):
    _, repo = _repo(tmp_path)
    context = _ctx("empty")
    for resource_id, venue in (("twse-universe-master", "TWSE"), ("tpex-universe-master", "TPEX")):
        repo.add_resource_revision(
            resource_id=resource_id, logical_revision_key="master", revision_number=1,
            payload=_resource_payload(venue=venue, status="accepted", reason=None,
                                      freshness_status="current", current_complete=True,
                                      coverage_complete=True),
            context=context, idempotency_key=f"empty-complete-{venue}",
        )
    complete = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert complete["items"] == []
    assert complete["status"] == "available"
    assert complete["per_venue_status"] == {"TWSE": "available", "TPEX": "available"}

    _, blocked_repo = _repo(tmp_path / "blocked")
    blocked_context = _ctx("blocked")
    for resource_id, venue in (("twse-universe-master", "TWSE"), ("tpex-universe-master", "TPEX")):
        blocked_repo.add_resource_revision(
            resource_id=resource_id, logical_revision_key="master", revision_number=1,
            payload=_resource_payload(venue=venue, status="provider_error", reason="provider unavailable"),
            context=blocked_context, idempotency_key=f"empty-blocked-{venue}",
        )
    blocked = blocked_repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert blocked["items"] == []
    assert blocked["status"] == "partial"
    assert blocked["per_venue_status"] == {"TWSE": "partial", "TPEX": "partial"}


def test_collector_uses_distinct_operational_contracts_and_fails_closed_on_drift():
    for key in ("tpex.spendi.history", "tpex.spendi.today", "tpex.cmode"):
        assert parse_universe_payload(key, {"data": [{"date": "2026-08-21", "status": "normal"}]})
    malformed = UniverseCollector(lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "json": lambda self: {"rows": [{"code": "2330"}]}})()).fetch_official(
        "tpex.spendi.history", url="https://www.tpex.org.tw/fixture",
    )
    assert malformed.status == "schema_changed"
    assert malformed.reason == "source_schema_changed"


@pytest.mark.parametrize(
    ("resource_key", "row"),
    [
        ("twse.t187ap03_L", {"code": "2330"}),
        ("twse.company.newlisting", {"code": "2330", "effective_date": "2026-01-01"}),
        ("twse.company.suspendListingCsvAndHtml", {"code": "2330", "reason": "suspended"}),
        ("tpex.mopsfin_t187ap03_O", {"code": "6488"}),
        ("tpex.company.deListed", {"code": "6488", "year": "2026", "reason": "terminated"}),
        ("tpex.spendi.cmode", {"status": "normal"}),
        ("tpex.spendi.history", {"date": "2026-08-21"}),
        ("tpex.spendi.today", {"status": "normal"}),
        ("tpex.cmode", {"date": "2026-08-21"}),
        ("tpex.company.current", {"code": "6488"}),
    ],
)
def test_every_approved_resource_has_a_fixed_contract(resource_key, row):
    parsed = parse_universe_payload(resource_key, {"data": [row]})
    assert parsed
    with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
        parse_universe_payload(resource_key, {"rows": [row]})


def test_audit_log_records_operator_dimensions_without_secret_payload(tmp_path, caplog):
    _, repo = _repo(tmp_path)
    context = _ctx("audit")
    caplog.set_level(logging.INFO, logger="tw_stock_predictor.universe_audit")
    repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="fixture-secret-source",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "audit-audit" in text and "run-audit" in text and "lock-audit" in text
    assert "fixture-secret-source" not in text
    assert "idempotency_key" not in text and "payload_fingerprint" not in text


def test_provenance_columns_round_trip_and_public_dto_remains_safe(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("provenance")
    anchor = _anchor(repo, context)
    row = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
        revision_number=1, payload=_instrument_payload(
            raw_payload_sha256="a" * 64, query_dimensions={"scope": "master"}, source_record_reference="row-1",
        ), context=context, idempotency_key="provenance-key",
    )
    with sqlite3.connect(db) as conn:
        stored = conn.execute(
            "SELECT raw_payload_sha256, normalized_payload_sha256, query_dimensions_json, source_record_reference, raw_resource_revision_id FROM universe_revisions WHERE universe_revision_id=?",
            (row["universe_revision_id"],),
        ).fetchone()
    assert stored[0] == "a" * 64
    assert stored[1] == row["payload_sha256"]
    assert '"scope":"master"' in stored[2]
    assert stored[3] == "row-1"
    dto = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    assert "payload_sha256" not in str(dto)
    assert "idempotency_key" not in str(dto)
