from __future__ import annotations

import sqlite3

from src.repositories.universe_repository import UniverseRepository
from tests.test_phase13_eighth_review_remediation import (
    _publication_evidence,
    _seed_corroborating_raw,
)
from tests.test_phase13_sixth_review_remediation import (
    _add_tpex_anchor,
    _ctx,
    _instrument_payload,
    _repo,
)


def _master(
    repo: UniverseRepository,
    db,
    *,
    anchor: dict,
    context,
    revision_number: int,
    available_at: str | None,
    ingested_at: str,
    status: str = "accepted",
    supersedes_revision_id: str | None = None,
):
    payload = _instrument_payload(
        db,
        venue="TPEX",
        code="6488",
        fetched_at=ingested_at,
        received_at=ingested_at,
        ingested_at=ingested_at,
        available_at=available_at,
        status=status,
        reason=None if status == "accepted" else f"{status} fixture",
        current_complete=status == "accepted",
        coverage_complete=status == "accepted",
    )
    if supersedes_revision_id is not None:
        payload["supersedes_revision_id"] = supersedes_revision_id
    return repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key="master-6488",
        revision_number=revision_number,
        payload=payload,
        context=context,
        idempotency_key=f"master-{revision_number}-{status}",
    )


def _corroborating(
    repo: UniverseRepository,
    db,
    *,
    anchor: dict,
    context,
    revision_number: int,
    available_at: str | None,
    ingested_at: str,
    key: str,
):
    raw_id, raw_hash = _seed_corroborating_raw(db)
    evidence = _publication_evidence(db, logical_key=key)
    payload = _instrument_payload(
        db,
        venue="TPEX",
        code="6488",
        canonical_symbol="6488.TWO",
        fetched_at=ingested_at,
        received_at=ingested_at,
        ingested_at=ingested_at,
        available_at=available_at,
        raw_resource_revision_id=raw_id,
        raw_payload_sha256=raw_hash,
        publication_evidence_id=evidence["publication_evidence_id"],
    )
    return repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-corroborating",
        logical_revision_key=key,
        revision_number=revision_number,
        payload=payload,
        context=context,
        idempotency_key=f"corroborating-{key}",
    )


def test_revocation_uses_instrument_revision_domain_and_fails_closed_across_lookups(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("revocation-domain")
    anchor = _add_tpex_anchor(repo, context, "6488")
    first = _master(
        repo, db, anchor=anchor, context=context, revision_number=1,
        available_at="2026-08-21T00:01:00Z", ingested_at="2026-08-21T00:02:00Z",
    )
    revoked = _master(
        repo, db, anchor=anchor, context=context, revision_number=2,
        available_at="2026-08-21T00:05:00Z", ingested_at="2026-08-21T00:06:00Z",
        status="revoked", supersedes_revision_id=first["universe_revision_id"],
    )

    before = repo.get_by_canonical("6488.TWO", knowledge_cutoff_at="2026-08-21T00:04:00Z")
    assert before["identity_reference"]["canonical_symbol"] == "6488.TWO"

    for result in (
        repo.get_by_canonical("6488.TWO", knowledge_cutoff_at="2026-08-21T00:07:00Z"),
        repo.resolve(official_code="6488", venue="TPEX", knowledge_cutoff_at="2026-08-21T00:07:00Z"),
    ):
        assert result["identity_reference"] is None
        assert result["status"] == "insufficient_data"
        assert "source_revision_revoked_without_corrected_revision" in result["reasons"]

    listed = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:07:00Z", venue="TPEX", limit=25,
    )
    assert listed["items"]
    assert listed["items"][0]["identity_reference"] is None
    assert listed["items"][0]["status"] == "insufficient_data"
    assert listed["per_venue_status"]["TPEX"] == "needs_human_input"
    assert revoked["status"] == "revoked"


def test_corrected_master_restores_mapping_only_after_cutoff_visibility(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("revocation-correction")
    anchor = _add_tpex_anchor(repo, context, "6488")
    first = _master(
        repo, db, anchor=anchor, context=context, revision_number=1,
        available_at="2026-08-21T00:01:00Z", ingested_at="2026-08-21T00:02:00Z",
    )
    revoked = _master(
        repo, db, anchor=anchor, context=context, revision_number=2,
        available_at="2026-08-21T00:05:00Z", ingested_at="2026-08-21T00:06:00Z",
        status="revoked", supersedes_revision_id=first["universe_revision_id"],
    )
    corrected = _master(
        repo, db, anchor=anchor, context=context, revision_number=3,
        available_at="2026-08-21T00:09:00Z", ingested_at="2026-08-21T00:10:00Z",
        supersedes_revision_id=revoked["universe_revision_id"],
    )

    before = repo.get_by_canonical("6488.TWO", knowledge_cutoff_at="2026-08-21T00:08:00Z")
    assert before["identity_reference"] is None
    assert "source_revision_revoked_without_corrected_revision" in before["reasons"]

    cutoff = "2026-08-21T00:11:00Z"
    exact = repo.get_by_canonical("6488.TWO", knowledge_cutoff_at=cutoff)
    resolved = repo.resolve(official_code="6488", venue="TPEX", knowledge_cutoff_at=cutoff)
    listed = repo.list_instruments(knowledge_cutoff_at=cutoff, venue="TPEX", limit=25)
    assert exact["identity_reference"]["canonical_symbol"] == "6488.TWO"
    assert resolved["identity_reference"] == exact["identity_reference"]
    assert listed["items"][0]["identity_reference"] == exact["identity_reference"]
    assert corrected["revision_number"] == 3


def test_master_mapping_carry_forward_requires_safe_cutoff_and_publication(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("mapping-cutoff")
    anchor = _add_tpex_anchor(repo, context, "6488")

    unavailable_master = _master(
        repo, db, anchor=anchor, context=context, revision_number=1,
        available_at=None, ingested_at="2026-08-21T00:02:00Z",
    )
    corroborating = _corroborating(
        repo, db, anchor=anchor, context=context, revision_number=2,
        available_at="2026-08-21T00:05:00Z", ingested_at="2026-08-21T00:06:00Z",
        key="mapping-no-available-master",
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT canonical_symbol FROM universe_instrument_revisions WHERE universe_revision_id=?",
            (corroborating["universe_revision_id"],),
        ).fetchone()[0] is None
    assert repo.get_by_instrument_id(
        anchor["instrument_id"], knowledge_cutoff_at="2026-08-21T00:07:00Z",
    )["identity_reference"]["canonical_symbol"] is None
    assert unavailable_master["available_at"] is None


def test_master_available_after_observation_is_not_inherited_early(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("mapping-future")
    anchor = _add_tpex_anchor(repo, context, "6488")
    corroborating = _corroborating(
        repo, db, anchor=anchor, context=context, revision_number=1,
        available_at="2026-08-21T00:05:00Z", ingested_at="2026-08-21T00:06:00Z",
        key="mapping-before-master",
    )
    master = _master(
        repo, db, anchor=anchor, context=context, revision_number=2,
        available_at="2026-08-21T00:10:00Z", ingested_at="2026-08-21T00:11:00Z",
    )

    early = repo.get_by_instrument_id(
        anchor["instrument_id"], knowledge_cutoff_at="2026-08-21T00:07:00Z",
    )
    assert early["identity_reference"]["canonical_symbol"] is None
    assert repo.get_by_canonical(
        "6488.TWO", knowledge_cutoff_at="2026-08-21T00:07:00Z",
    )["identity_reference"] is None

    later = repo.get_by_instrument_id(
        anchor["instrument_id"], knowledge_cutoff_at="2026-08-21T00:12:00Z",
    )
    assert later["identity_reference"]["canonical_symbol"] == "6488.TWO"
    assert master["revision_number"] == 2
