from __future__ import annotations

import logging
import hashlib
import sqlite3

import pytest

from src.collectors.universe_collectors import UniverseCollector, UniverseSourceRejected, parse_universe_payload
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseRepository
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import raw_for, seed_raw_provenance


def _ctx(name: str = "second") -> UniverseOperatorContext:
    return UniverseOperatorContext("operator", f"run-{name}", f"lock-{name}", f"audit-{name}")


def _repo(tmp_path):
    db = tmp_path / "universe.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    return db, UniverseRepository(str(db), guard=UniverseWriteGuard(True))


def _instrument_payload(**overrides):
    value = {
        "venue": "TWSE", "official_code": "2330", "canonical_symbol": "2330.TW",
        "fetched_at": "2026-08-21T00:01:00Z", "received_at": "2026-08-21T00:01:00Z",
        "ingested_at": "2026-08-21T00:02:00Z", "available_at": "2026-08-21T00:01:00Z",
        "source_reference": "fixture", "status": "accepted", "freshness_status": "current",
        "freshness_mode": "official_cadence_window", "current_complete": True, "coverage_complete": True,
        "raw_resource_revision_id": "raw-phase13-twse_universe_master",
        "raw_payload_sha256": hashlib.sha256(b"phase13:twse-universe-master").hexdigest(),
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
        "raw_resource_revision_id": "raw-phase13-twse_universe_master",
        "raw_payload_sha256": hashlib.sha256(b"phase13:twse-universe-master").hexdigest(),
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
    db, repo = _repo(tmp_path)
    context = _ctx(status)
    anchor = _anchor(repo, context)
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
        revision_number=1, payload=_instrument_payload(), context=context, idempotency_key="twse-s1",
    )
    raw_id, raw_hash = raw_for(db, "tpex-universe-operational")
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="tpex.spendi.history", revision_number=1,
        payload=_resource_payload(venue="TPEX", status=status, reason=f"{status} fixture",
                                 raw_resource_revision_id=raw_id, raw_payload_sha256=raw_hash),
        context=context, idempotency_key=f"tpex-{status}",
    )
    result = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert result["per_venue_status"]["TPEX"] == ("needs_human_input" if status == "schema_changed" else "partial")
    assert result["items"]
    assert result["status"] == ("needs_human_input" if status == "schema_changed" else "partial")
    twse_only = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", venue="TWSE", limit=25)
    assert twse_only["per_venue_status"]["TWSE"] == "available"


def test_reverse_venue_failure_does_not_hide_healthy_venue(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("reverse")
    tpex_anchor = repo.allocate_instrument(
        venue="TPEX", official_code="6488", source_identity="tpex:6488:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    repo.add_revision(
        instrument_id=tpex_anchor["instrument_id"], resource_id="tpex-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(
            venue="TPEX", official_code="6488", canonical_symbol="6488.TWO",
            raw_resource_revision_id=raw_for(db, "tpex-universe-master")[0],
            raw_payload_sha256=raw_for(db, "tpex-universe-master")[1],
        ),
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
    db, repo = _repo(tmp_path)
    context = _ctx("empty")
    twse_raw = raw_for(db, "twse-universe-master")
    tpex_raw = raw_for(db, "tpex-universe-master")
    for resource_id, venue in (("twse-universe-master", "TWSE"), ("tpex-universe-master", "TPEX")):
        raw_id, raw_hash = twse_raw if venue == "TWSE" else tpex_raw
        repo.add_resource_revision(
            resource_id=resource_id, logical_revision_key="master", revision_number=1,
            payload=_resource_payload(venue=venue, status="accepted", reason=None,
                                      freshness_status="current", current_complete=True,
                                      freshness_mode="official_cadence_window",
                                      coverage_complete=True, raw_resource_revision_id=raw_id,
                                      raw_payload_sha256=raw_hash),
            context=context, idempotency_key=f"empty-complete-{venue}",
        )
    complete = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert complete["items"] == []
    assert complete["status"] == "available"
    assert complete["per_venue_status"] == {"TWSE": "available", "TPEX": "available"}

    blocked_db, blocked_repo = _repo(tmp_path / "blocked")
    blocked_context = _ctx("blocked")
    twse_raw = raw_for(blocked_db, "twse-universe-master")
    tpex_raw = raw_for(blocked_db, "tpex-universe-master")
    for resource_id, venue in (("twse-universe-master", "TWSE"), ("tpex-universe-master", "TPEX")):
        raw_id, raw_hash = twse_raw if venue == "TWSE" else tpex_raw
        blocked_repo.add_resource_revision(
            resource_id=resource_id, logical_revision_key="master", revision_number=1,
            payload=_resource_payload(venue=venue, status="provider_error", reason="provider unavailable",
                                      raw_resource_revision_id=raw_id, raw_payload_sha256=raw_hash),
            context=blocked_context, idempotency_key=f"empty-blocked-{venue}",
        )
    blocked = blocked_repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=25)
    assert blocked["items"] == []
    assert blocked["status"] == "partial"
    assert blocked["per_venue_status"] == {"TWSE": "partial", "TPEX": "partial"}


def test_collector_uses_distinct_operational_contracts_and_fails_closed_on_drift():
    assert parse_universe_payload("tpex.spendi.history", [{
        "Date": "1150821", "Serial": "1", "SecuritiesCompanyCode": "6488", "CompanyName": "fixture",
        "DateOfSuspendedTrading": "1150821", "TimeOfSuspendedTrading": "080000",
        "DateOfResumedTrading": "", "TimeOfResumedTrading": "",
    }])
    assert parse_universe_payload("tpex.spendi.today", [{
        "Date": "20260821", "SecuritiesCompanyCode": "6488", "CompanyName": "fixture",
        "DateOfSuspendedTrading": "20260821", "TimeOfSuspendedTrading": "080000",
        "DateOfResumedTrading": "", "TimeOfResumedTrading": "",
    }])
    assert parse_universe_payload("tpex.cmode", [{
        "Date": "1150821", "SecuritiesCompanyCode": "6488", "CompanyName": "fixture",
        "AlteredTrading": "Y", "PeriodicTrading": "", "ManagedStock": "",
        "MatchingFrequency": "", "SuspensionOfTrading": "", " FinancialAnnouncements": "",
    }])
    with pytest.raises(UniverseSourceRejected, match="legacy_source_contract_not_for_ingestion"):
        parse_universe_payload("tpex.spendi.cmode", [{"Date": "1150821"}])
    malformed = UniverseCollector(lambda *_args, **_kwargs: type("Response", (), {"status_code": 200, "json": lambda self: {"rows": [{"code": "2330"}]}})()).fetch_official(
        "tpex.spendi.history", url="https://www.tpex.org.tw/fixture",
    )
    assert malformed.status == "schema_changed"
    assert malformed.reason == "source_schema_changed"


@pytest.mark.parametrize(
    ("resource_key", "payload"),
    [
        ("twse.t187ap03_L", [{"出表日期": "1150820", "公司代號": "2330", "公司名稱": "台積電"}]),
        ("twse.company.newlisting", [{
            "Code": "7855", "Company": "和運租車", "ApplicationDate": "1150414", "Chairman": "劉源森",
            "AmountofCapital ": "1925279", "CommitteeDate": "1150522", "ApprovedDate": "1150616",
            "AgreementDate": "1150624", "ListingDate": "", "ApprovedListingDate": "1150811",
            "Underwriter": "台新", "UnderwritingPrice": "42.00", "Note": "",
        }]),
        ("twse.company.suspendListingCsvAndHtml", [{"DelistingDate": "115/06/23", "Company": "森崴能源", "Code": "2330"}]),
        ("tpex.mopsfin_t187ap03_O", [{"Date": "1150821", "SecuritiesCompanyCode": "6488", "CompanyName": "元大"}]),
        ("tpex.company.deListed", {"tables": [{
            "fields": ["股票代號", "公司名稱", "終止上櫃日期", "終止上櫃原因", "公司資料網址"],
            "data": [["6488", "元大", "115-01-01", "終止上櫃", "https://www.tpex.org.tw/" ]],
        }]}),
        ("tpex.spendi.history", [{
            "Date": "1150821", "Serial": "1", "SecuritiesCompanyCode": "6488", "CompanyName": "元大",
            "DateOfSuspendedTrading": "1150821", "TimeOfSuspendedTrading": "080000",
            "DateOfResumedTrading": "", "TimeOfResumedTrading": "",
        }]),
        ("tpex.spendi.today", [{
            "Date": "20260821", "SecuritiesCompanyCode": "6488", "CompanyName": "元大",
            "DateOfSuspendedTrading": "20260821", "TimeOfSuspendedTrading": "080000",
            "DateOfResumedTrading": "", "TimeOfResumedTrading": "",
        }]),
        ("tpex.cmode", [{
            "Date": "1150821", "SecuritiesCompanyCode": "6488", "CompanyName": "元大",
            "AlteredTrading": "Y", "PeriodicTrading": "", "ManagedStock": "",
            "MatchingFrequency": "", "SuspensionOfTrading": "", " FinancialAnnouncements": "",
        }]),
    ],
)
def test_every_approved_resource_has_a_fixed_contract(resource_key, payload):
    parsed = parse_universe_payload(resource_key, payload)
    assert parsed
    with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
        parse_universe_payload(resource_key, {"rows": []})


def test_tpex_company_current_is_manual_only_until_machine_contract_is_verified():
    with pytest.raises(UniverseSourceRejected, match="manual_source_contract_not_for_ingestion"):
        parse_universe_payload(
            "tpex.company.current",
            [{"Date": "1150821", "SecuritiesCompanyCode": "6488", "CompanyName": "元大"}],
        )


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
    raw_id, raw_hash = raw_for(db, "twse-universe-master")
    row = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master", logical_revision_key="master",
        revision_number=1, payload=_instrument_payload(
            raw_resource_revision_id=raw_id, raw_payload_sha256=raw_hash,
            query_dimensions={"scope": "master"}, source_record_reference="row-1",
        ), context=context, idempotency_key="provenance-key",
    )
    with sqlite3.connect(db) as conn:
        stored = conn.execute(
            "SELECT raw_payload_sha256, normalized_payload_sha256, query_dimensions_json, source_record_reference, raw_resource_revision_id FROM universe_revisions WHERE universe_revision_id=?",
            (row["universe_revision_id"],),
        ).fetchone()
    assert stored[0] == raw_hash
    assert stored[1] == row["payload_sha256"]
    assert '"scope":"master"' in stored[2]
    assert stored[3] == "row-1"
    dto = repo.get_by_canonical("2330.TW", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    assert "payload_sha256" not in str(dto)
    assert "idempotency_key" not in str(dto)
