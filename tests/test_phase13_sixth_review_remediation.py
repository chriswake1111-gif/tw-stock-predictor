from __future__ import annotations

from src.repositories.universe_repository import UniverseRepository
from src.services.universe_write_guard import UniverseOperatorContext, UniverseWriteGuard
from tests.phase13_test_support import raw_for, seed_raw_provenance
from src.repositories.migration_runner import apply_valuation_migration


def _ctx(name: str) -> UniverseOperatorContext:
    return UniverseOperatorContext("operator", f"run-{name}", f"lock-{name}", f"audit-{name}")


def _repo(tmp_path):
    db = tmp_path / "universe.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    return db, UniverseRepository(str(db), guard=UniverseWriteGuard(True))


def _instrument_payload(db, *, venue: str, code: str, **overrides):
    resource_id = "twse-universe-master" if venue == "TWSE" else "tpex-universe-master"
    raw_id, raw_hash = raw_for(db, resource_id)
    value = {
        "venue": venue,
        "official_code": code,
        "canonical_symbol": f"{code}.TW" if venue == "TWSE" else f"{code}.TWO",
        "display_name": f"Fixture {code}",
        "security_type": "stock",
        "listing_status": "listed",
        "fetched_at": "2026-08-21T00:01:00Z",
        "received_at": "2026-08-21T00:01:00Z",
        "ingested_at": "2026-08-21T00:02:00Z",
        "available_at": "2026-08-21T00:01:00Z",
        "source_reference": "fixture",
        "status": "accepted",
        "freshness_status": "current",
        "freshness_mode": "official_cadence_window",
        "current_complete": True,
        "coverage_complete": True,
        "raw_resource_revision_id": raw_id,
        "raw_payload_sha256": raw_hash,
    }
    value.update(overrides)
    return value


def _resource_payload(db, resource_id: str, *, venue: str, status: str, ingested_at: str,
                      available_at: str | None = None, **overrides):
    raw_id, raw_hash = raw_for(db, resource_id)
    value = {
        "venue": venue,
        "fetched_at": ingested_at,
        "received_at": ingested_at,
        "ingested_at": ingested_at,
        "available_at": available_at or ingested_at,
        "source_reference": f"fixture:{resource_id}",
        "status": status,
        "reason": None if status in {"accepted", "partial"} else f"{status} fixture",
        "freshness_status": "current" if status == "accepted" else "blocked",
        "freshness_mode": "event_observation",
        "current_complete": status == "accepted",
        "coverage_complete": status == "accepted",
        "raw_resource_revision_id": raw_id,
        "raw_payload_sha256": raw_hash,
    }
    value.update(overrides)
    return value


def _add_tpex_anchor(repo: UniverseRepository, context: UniverseOperatorContext, code: str = "6488"):
    anchor = repo.allocate_instrument(
        venue="TPEX", official_code=code, source_identity=f"tpex:{code}:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    return anchor


def test_venue_health_composes_latest_state_per_resource_without_cross_resource_masking(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("resource-composition")
    anchor = _add_tpex_anchor(repo, context)
    master = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="tpex-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code="6488"),
        context=context, idempotency_key="master-accepted",
    )

    # A newer operational resource must not mask a blocked master resource.
    master_blocked = repo.add_resource_revision(
        resource_id="tpex-universe-master", logical_revision_key="master", revision_number=2,
        payload=_resource_payload(db, "tpex-universe-master", venue="TPEX", status="schema_changed",
                                  ingested_at="2026-08-21T00:05:00Z",
                                  supersedes_revision_id=master["universe_revision_id"]),
        context=context, idempotency_key="master-schema",
    )
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="cmode", revision_number=1,
        payload=_resource_payload(db, "tpex-universe-operational", venue="TPEX", status="accepted",
                                  ingested_at="2026-08-21T00:06:00Z"),
        context=context, idempotency_key="operational-accepted",
    )
    result = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:07:00Z", venue="TPEX", limit=25)
    assert result["per_venue_status"]["TPEX"] == "needs_human_input"
    assert master_blocked["status"] == "schema_changed"


def test_venue_health_preserves_provider_error_and_waiting_review_across_resources(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("resource-precedence")
    anchor = _add_tpex_anchor(repo, context, "6490")
    master = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="tpex-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code="6490"),
        context=context, idempotency_key="master-6490",
    )
    repo.add_resource_revision(
        resource_id="tpex-universe-master", logical_revision_key="master", revision_number=2,
        payload=_resource_payload(db, "tpex-universe-master", venue="TPEX", status="provider_error",
                                  ingested_at="2026-08-21T00:05:00Z",
                                  supersedes_revision_id=master["universe_revision_id"]),
        context=context, idempotency_key="master-provider-error",
    )
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="cmode", revision_number=1,
        payload=_resource_payload(db, "tpex-universe-operational", venue="TPEX", status="accepted",
                                  ingested_at="2026-08-21T00:06:00Z"),
        context=context, idempotency_key="operational-6490",
    )
    partial = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:07:00Z", venue="TPEX", limit=25)
    assert partial["per_venue_status"]["TPEX"] == "partial"

    db2, repo2 = _repo(tmp_path / "awaiting")
    context2 = _ctx("resource-awaiting")
    anchor2 = _add_tpex_anchor(repo2, context2, "6491")
    repo2.add_revision(
        instrument_id=anchor2["instrument_id"], resource_id="tpex-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(db2, venue="TPEX", code="6491"),
        context=context2, idempotency_key="master-6491",
    )
    repo2.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="cmode", revision_number=1,
        payload=_resource_payload(db2, "tpex-universe-operational", venue="TPEX", status="awaiting_review",
                                  ingested_at="2026-08-21T00:06:00Z"),
        context=context2, idempotency_key="operational-awaiting",
    )
    waiting = repo2.list_instruments(knowledge_cutoff_at="2026-08-21T00:07:00Z", venue="TPEX", limit=25)
    assert waiting["per_venue_status"]["TPEX"] == "needs_human_input"


def test_corrected_latest_revision_of_same_resource_clears_its_blocker(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("resource-correction")
    first = repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="cmode", revision_number=1,
        payload=_resource_payload(db, "tpex-universe-operational", venue="TPEX", status="schema_changed",
                                  ingested_at="2026-08-21T00:05:00Z"),
        context=context, idempotency_key="operational-schema",
    )
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="cmode", revision_number=2,
        payload=_resource_payload(db, "tpex-universe-operational", venue="TPEX", status="accepted",
                                  ingested_at="2026-08-21T00:06:00Z",
                                  supersedes_revision_id=first["universe_revision_id"]),
        context=context, idempotency_key="operational-corrected",
    )
    result = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:07:00Z", venue="TPEX", limit=25)
    assert result["per_venue_status"]["TPEX"] == "available"


def test_list_composes_both_venues_and_honors_explicit_venue_filter(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("venue-filter")
    twse = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    tpex = _add_tpex_anchor(repo, context, "6488")
    for anchor, venue, code, key in (
        (twse, "TWSE", "2330", "twse-filter"),
        (tpex, "TPEX", "6488", "tpex-filter"),
    ):
        resource_id = "twse-universe-master" if venue == "TWSE" else "tpex-universe-master"
        repo.add_revision(
            instrument_id=anchor["instrument_id"], resource_id=resource_id,
            logical_revision_key=f"master-{code}", revision_number=1,
            payload=_instrument_payload(db, venue=venue, code=code),
            context=context, idempotency_key=key,
        )
    all_venues = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=100)
    assert {item["identity_reference"]["venue"] for item in all_venues["items"]} == {"TWSE", "TPEX"}
    tpex_only = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", venue="TPEX", limit=100)
    assert {item["identity_reference"]["venue"] for item in tpex_only["items"]} == {"TPEX"}
    assert tpex_only["per_venue_status"] == {"TPEX": "available"}


def test_list_filters_apply_after_latest_reference_selection(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("latest-filter")
    anchor = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330:v1",
        first_observed_at="2026-08-21T00:00:00Z", source_reference="fixture", context=context,
    )
    first = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=1,
        payload=_instrument_payload(
            db,
            venue="TWSE", code="2330", display_name="Old Matching Name",
            security_type="stock", listing_status="listed",
            ingested_at="2026-08-21T00:02:00Z", available_at="2026-08-21T00:01:00Z",
        ),
        context=context, idempotency_key="filter-old",
    )
    repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="twse-universe-master",
        logical_revision_key="master", revision_number=2,
        payload=_instrument_payload(
            db,
            venue="TWSE", code="2330", display_name="Current Name",
            security_type="etf", listing_status="delisted",
            ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z",
            supersedes_revision_id=first["universe_revision_id"],
        ),
        context=context, idempotency_key="filter-current",
    )
    cutoff = "2026-08-21T00:05:00Z"
    assert repo.list_instruments(knowledge_cutoff_at=cutoff, listing_status="listed", limit=100)["items"] == []
    assert repo.list_instruments(knowledge_cutoff_at=cutoff, security_type="stock", limit=100)["items"] == []
    assert repo.list_instruments(knowledge_cutoff_at=cutoff, query="Old Matching Name", limit=100)["items"] == []
    current = repo.list_instruments(knowledge_cutoff_at=cutoff, query="Current Name", limit=100)
    assert len(current["items"]) == 1
    assert current["items"][0]["identity_reference"]["display_name"] == "Current Name"
    assert repo.list_instruments(knowledge_cutoff_at=cutoff, listing_status="delisted", security_type="etf", limit=100)["items"]
