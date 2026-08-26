from __future__ import annotations

import hashlib
import sqlite3

import pytest

from src.domain.data_foundation import PublicationVerificationMode, ResourcePublicationEvidence
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.universe_repository import UniverseRepository
from tests.test_phase13_sixth_review_remediation import (
    _add_tpex_anchor,
    _ctx,
    _instrument_payload,
    _repo,
    _resource_payload,
)


def _seed_corroborating_raw(db) -> tuple[str, str]:
    resource_id = "tpex-universe-corroborating"
    raw_id = "raw-phase13-tpex-universe-corroborating"
    raw_hash = hashlib.sha256(b"phase13:tpex-universe-corroborating").hexdigest()
    schema_hash = hashlib.sha256(b"schema:tpex-universe-corroborating").hexdigest()
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_resource_revisions VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                raw_id, "identity-tpex-corroborating", "tpex-universe-official", resource_id,
                "tpex.company.current", "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z",
                "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z", raw_hash, "1", schema_hash,
                "hash_only", None, "fresh", "eligible", None, "phase13 fixture provenance",
            ),
        )
    return raw_id, raw_hash


def _publication_evidence(db, *, logical_key: str):
    return DataFoundationRepository(str(db)).add_publication_evidence(
        ResourcePublicationEvidence(
            provider_id="tpex-universe-official",
            resource_id="tpex-universe-corroborating",
            logical_revision_key=logical_key,
            official_release_at="2026-08-21T00:01:00Z",
            source_reference="https://www.tpex.org.tw/company/otcSearch",
            source_identity=f"tpex:company:{logical_key}:v1",
            evidence_file_sha256="b" * 64,
            captured_at="2026-08-21T00:01:30Z",
            verification_mode=PublicationVerificationMode.MANUAL_OFFICIAL_SOURCE_REVIEW,
            verified_by="reviewer",
        ),
        ingested_at="2026-08-21T00:02:00Z",
    )


def _add_corroborating(
    repo: UniverseRepository, db, *, anchor: dict, context,
    key: str = "current-6488", revision_number: int = 1,
):
    raw_id, raw_hash = _seed_corroborating_raw(db)
    evidence = _publication_evidence(db, logical_key=key)
    payload = _instrument_payload(
        db, venue="TPEX", code="6488", canonical_symbol="6488.TWO",
        raw_resource_revision_id=raw_id, raw_payload_sha256=raw_hash,
        freshness_mode="event_observation", current_complete=True,
        publication_evidence_id=evidence["publication_evidence_id"],
    )
    return repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="tpex-universe-corroborating",
        logical_revision_key=key, revision_number=revision_number, payload=payload,
        context=context, idempotency_key=f"corroborating-{key}-{revision_number}",
    )


@pytest.mark.parametrize("claimed_mode", ["official_cadence_window", "licensed_reference"])
def test_registered_master_freshness_policy_cannot_be_promoted_by_payload(tmp_path, claimed_mode):
    db, repo = _repo(tmp_path)
    context = _ctx(f"freshness-{claimed_mode}")
    anchor = _add_tpex_anchor(repo, context, "6488")
    revision = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="tpex-universe-master",
        logical_revision_key="master-6488", revision_number=1,
        payload=_instrument_payload(
            db, venue="TPEX", code="6488", freshness_mode=claimed_mode,
            freshness_status="current", current_complete=True, coverage_complete=True,
        ),
        context=context, idempotency_key=f"master-{claimed_mode}",
    )

    assert revision["freshness_mode"] == "unknown_without_official_cadence"
    assert revision["freshness_status"] == "unknown"
    assert revision["current_complete"] == 0
    assert revision["coverage_complete"] == 1
    exact = repo.get_by_canonical("6488.TWO", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    listed = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:05:00Z", venue="TPEX", limit=25)
    assert exact["status"] == "partial"
    assert exact["operational_freshness"]["freshness"] == "unknown"
    assert listed["per_venue_status"]["TPEX"] == "partial"

    resource = repo.add_resource_revision(
        resource_id="tpex-universe-master", logical_revision_key="master-resource",
        revision_number=1,
        payload=_resource_payload(
            db, "tpex-universe-master", venue="TPEX", status="accepted",
            ingested_at="2026-08-21T00:06:00Z",
            freshness_mode=claimed_mode, freshness_status="current",
            current_complete=True, coverage_complete=True,
        ),
        context=context, idempotency_key=f"resource-{claimed_mode}",
    )
    assert resource["freshness_mode"] == "unknown_without_official_cadence"
    assert resource["freshness_status"] == "unknown"
    assert resource["current_complete"] == 0


def test_logical_feed_states_are_independent_and_blockers_are_not_masked(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("logical-feeds")

    history = repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="tpex.spendi.history",
        revision_number=1,
        payload=_resource_payload(
            db, "tpex-universe-operational", venue="TPEX", status="provider_error",
            ingested_at="2026-08-21T00:05:00Z", available_at=None,
        ),
        context=context, idempotency_key="history-error",
    )
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="tpex.cmode",
        revision_number=99,
        payload=_resource_payload(
            db, "tpex-universe-operational", venue="TPEX", status="accepted",
            ingested_at="2026-08-21T00:04:00Z",
        ),
        context=context, idempotency_key="cmode-accepted",
    )
    assert repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:06:00Z", venue="TPEX", limit=25,
    )["per_venue_status"]["TPEX"] == "partial"

    corrected_history = repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="tpex.spendi.history",
        revision_number=2,
        payload=_resource_payload(
            db, "tpex-universe-operational", venue="TPEX", status="accepted",
            ingested_at="2026-08-21T00:07:00Z",
            supersedes_revision_id=history["universe_revision_id"],
        ),
        context=context, idempotency_key="history-corrected",
    )
    assert corrected_history["revision_number"] == 2
    assert repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:08:00Z", venue="TPEX", limit=25,
    )["per_venue_status"]["TPEX"] == "insufficient_data"


@pytest.mark.parametrize(
    ("logical_key", "status", "expected"),
    [
        ("tpex.spendi.history", "provider_error", "partial"),
        ("tpex.spendi.today", "schema_changed", "needs_human_input"),
    ],
)
def test_each_logical_feed_status_survives_reverse_temporal_order(tmp_path, logical_key, status, expected):
    db, repo = _repo(tmp_path)
    context = _ctx(f"reverse-{logical_key}")
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key="tpex.cmode",
        revision_number=1,
        payload=_resource_payload(
            db, "tpex-universe-operational", venue="TPEX", status="accepted",
            ingested_at="2026-08-21T00:10:00Z",
        ),
        context=context, idempotency_key=f"cmode-{logical_key}",
    )
    repo.add_resource_revision(
        resource_id="tpex-universe-operational", logical_revision_key=logical_key,
        revision_number=1,
        payload=_resource_payload(
            db, "tpex-universe-operational", venue="TPEX", status=status,
            ingested_at="2026-08-21T00:09:00Z", available_at=None,
        ),
        context=context, idempotency_key=f"blocked-{logical_key}",
    )
    result = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:11:00Z", venue="TPEX", limit=25,
    )
    assert result["per_venue_status"]["TPEX"] == expected


def test_corroborating_only_observation_cannot_create_canonical_mapping(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("corroborating-only")
    anchor = _add_tpex_anchor(repo, context, "6488")
    revision = _add_corroborating(repo, db, anchor=anchor, context=context)

    with sqlite3.connect(db) as conn:
        stored = conn.execute(
            "SELECT canonical_symbol, reason FROM universe_instrument_revisions WHERE universe_revision_id = ?",
            (revision["universe_revision_id"],),
        ).fetchone()
    assert stored == (None, "canonical_mapping_unverified")
    assert repo.get_by_canonical(
        "6488.TWO", knowledge_cutoff_at="2026-08-21T00:05:00Z",
    )["identity_reference"] is None
    exact = repo.get_by_instrument_id(anchor["instrument_id"], knowledge_cutoff_at="2026-08-21T00:05:00Z")
    assert exact["identity_reference"]["canonical_symbol"] is None
    assert "canonical_mapping_unverified" in exact["reasons"]


def test_corroborating_observation_preserves_existing_master_mapping(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("corroborating-with-master")
    anchor = _add_tpex_anchor(repo, context, "6488")
    master = repo.add_revision(
        instrument_id=anchor["instrument_id"], resource_id="tpex-universe-master",
        logical_revision_key="master-6488", revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code="6488"),
        context=context, idempotency_key="master-for-corroborating",
    )
    corroborating = _add_corroborating(
        repo, db, anchor=anchor, context=context, revision_number=2,
    )

    with sqlite3.connect(db) as conn:
        mapped = conn.execute(
            "SELECT canonical_symbol FROM universe_instrument_revisions WHERE universe_revision_id = ?",
            (corroborating["universe_revision_id"],),
        ).fetchone()[0]
    assert mapped == "6488.TWO"
    result = repo.get_by_canonical("6488.TWO", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    assert result["identity_reference"]["canonical_symbol"] == "6488.TWO"
    assert result["identity_reference"]["instrument_id"] == anchor["instrument_id"]
