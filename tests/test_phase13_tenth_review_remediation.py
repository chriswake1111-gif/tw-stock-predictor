from __future__ import annotations

import sqlite3

import pytest

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
    code: str,
    logical_key: str,
    revision_number: int,
    ingested_at: str,
    available_at: str | None,
    status: str = "accepted",
    supersedes_revision_id: str | None = None,
    display_name: str | None = None,
    listing_status: str = "listed",
):
    payload = _instrument_payload(
        db,
        venue="TPEX",
        code=code,
        display_name=display_name or f"Fixture {code} r{revision_number}",
        fetched_at=ingested_at,
        received_at=ingested_at,
        ingested_at=ingested_at,
        available_at=available_at,
        status=status,
        reason=None if status == "accepted" else f"{status} fixture",
        current_complete=status == "accepted",
        coverage_complete=status == "accepted",
        listing_status=listing_status,
    )
    if supersedes_revision_id is not None:
        payload["supersedes_revision_id"] = supersedes_revision_id
    return repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key=logical_key,
        revision_number=revision_number,
        payload=payload,
        context=context,
        idempotency_key=f"{logical_key}-{revision_number}-{status}",
    )


def _corroborating(
    repo: UniverseRepository,
    db,
    *,
    anchor: dict,
    context,
    code: str,
    logical_key: str,
    revision_number: int,
    ingested_at: str,
    available_at: str | None,
    status: str = "accepted",
):
    raw_id, raw_hash = _seed_corroborating_raw(db)
    evidence = _publication_evidence(db, logical_key=logical_key)
    payload = _instrument_payload(
        db,
        venue="TPEX",
        code=code,
        canonical_symbol=f"{code}.TWO",
        fetched_at=ingested_at,
        received_at=ingested_at,
        ingested_at=ingested_at,
        available_at=available_at,
        status=status,
        reason=None if status == "accepted" else f"{status} fixture",
        current_complete=False,
        coverage_complete=status == "accepted",
        raw_resource_revision_id=raw_id,
        raw_payload_sha256=raw_hash,
        publication_evidence_id=evidence["publication_evidence_id"],
    )
    return repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-corroborating",
        logical_revision_key=logical_key,
        revision_number=revision_number,
        payload=payload,
        context=context,
        idempotency_key=f"{logical_key}-{revision_number}-{status}",
    )


def test_instrument_supersession_targets_exact_parent_not_global_latest(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("tenth-interleaved")
    anchor = _add_tpex_anchor(repo, context, "6488")
    master_one = _master(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="master-6488",
        revision_number=1, ingested_at="2026-08-21T00:02:00Z", available_at="2026-08-21T00:01:00Z",
    )
    corroborating = _corroborating(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="corr-6488",
        revision_number=2, ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z",
    )
    master_two = _master(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="master-6488",
        revision_number=3, ingested_at="2026-08-21T00:06:00Z", available_at="2026-08-21T00:05:00Z",
        supersedes_revision_id=master_one["universe_revision_id"],
    )

    with sqlite3.connect(db) as conn:
        master_target = conn.execute(
            "SELECT supersedes_revision_id FROM universe_instrument_revisions WHERE universe_revision_id=?",
            (master_two["universe_revision_id"],),
        ).fetchone()[0]
        corroborating_target = conn.execute(
            "SELECT supersedes_revision_id FROM universe_instrument_revisions WHERE universe_revision_id=?",
            (corroborating["universe_revision_id"],),
        ).fetchone()[0]
        master_one_id = conn.execute(
            "SELECT instrument_revision_id FROM universe_instrument_revisions WHERE universe_revision_id=?",
            (master_one["universe_revision_id"],),
        ).fetchone()[0]

    assert master_target == master_one_id
    assert corroborating_target is None


def test_first_new_chain_revocation_cannot_revoke_interleaved_master(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("tenth-new-chain")
    anchor = _add_tpex_anchor(repo, context, "6488")
    master = _master(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="master-6488",
        revision_number=1, ingested_at="2026-08-21T00:02:00Z", available_at="2026-08-21T00:01:00Z",
    )
    revoked_corroborating = _corroborating(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="corr-revoked",
        revision_number=2, ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z",
        status="revoked",
    )

    result = repo.get_by_canonical("6488.TWO", knowledge_cutoff_at="2026-08-21T00:05:00Z")
    assert result["identity_reference"]["canonical_symbol"] == "6488.TWO"
    with sqlite3.connect(db) as conn:
        target = conn.execute(
            "SELECT supersedes_revision_id FROM universe_instrument_revisions WHERE universe_revision_id=?",
            (revoked_corroborating["universe_revision_id"],),
        ).fetchone()[0]
    assert target is None
    assert master["status"] == "accepted"


def test_cross_instrument_parent_is_rejected(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("tenth-cross-instrument")
    first_anchor = _add_tpex_anchor(repo, context, "6488")
    second_anchor = _add_tpex_anchor(repo, context, "6489")
    first = _master(
        repo, db, anchor=first_anchor, context=context, code="6488", logical_key="shared-master",
        revision_number=1, ingested_at="2026-08-21T00:02:00Z", available_at="2026-08-21T00:01:00Z",
    )

    with pytest.raises(ValueError, match="another instrument"):
        _master(
            repo, db, anchor=second_anchor, context=context, code="6489", logical_key="shared-master",
            revision_number=2, ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z",
            supersedes_revision_id=first["universe_revision_id"],
        )


def test_transitive_correction_and_revoke_never_resurrects_old_reference(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("tenth-transitive")
    anchor = _add_tpex_anchor(repo, context, "6488")
    first = _master(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="master-6488",
        revision_number=1, ingested_at="2026-08-21T00:02:00Z", available_at="2026-08-21T00:01:00Z",
        display_name="R1",
    )
    second = _master(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="master-6488",
        revision_number=2, ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z",
        supersedes_revision_id=first["universe_revision_id"], display_name="R2",
    )
    third = _master(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="master-6488",
        revision_number=3, ingested_at="2026-08-21T00:06:00Z", available_at="2026-08-21T00:05:00Z",
        status="revoked", supersedes_revision_id=second["universe_revision_id"],
    )

    before_second = repo.resolve(official_code="6488", venue="TPEX", knowledge_cutoff_at="2026-08-21T00:02:30Z")
    between = repo.resolve(official_code="6488", venue="TPEX", knowledge_cutoff_at="2026-08-21T00:04:30Z")
    after_revoke = repo.resolve(official_code="6488", venue="TPEX", knowledge_cutoff_at="2026-08-21T00:07:00Z")

    assert before_second["identity_reference"]["display_name"] == "R1"
    assert between["identity_reference"]["display_name"] == "R2"
    assert after_revoke["identity_reference"] is None
    assert "source_revision_revoked_without_corrected_revision" in after_revoke["reasons"]

    corrected = _master(
        repo, db, anchor=anchor, context=context, code="6488", logical_key="master-6488",
        revision_number=4, ingested_at="2026-08-21T00:09:00Z", available_at="2026-08-21T00:08:00Z",
        supersedes_revision_id=third["universe_revision_id"], display_name="R4",
    )
    resumed = repo.get_by_canonical("6488.TWO", knowledge_cutoff_at="2026-08-21T00:10:00Z")
    assert resumed["identity_reference"]["display_name"] == "R4"
    assert corrected["revision_number"] == 4


def test_list_paginates_over_effective_references_not_revoked_rows(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("tenth-pagination")
    first_anchor = _add_tpex_anchor(repo, context, "1000")
    second_anchor = _add_tpex_anchor(repo, context, "1001")
    first = _master(
        repo, db, anchor=first_anchor, context=context, code="1000", logical_key="master-1000",
        revision_number=1, ingested_at="2026-08-21T00:02:00Z", available_at="2026-08-21T00:01:00Z",
    )
    _master(
        repo, db, anchor=second_anchor, context=context, code="1001", logical_key="master-1001",
        revision_number=1, ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z",
    )
    _master(
        repo, db, anchor=first_anchor, context=context, code="1000", logical_key="master-1000",
        revision_number=2, ingested_at="2026-08-21T00:06:00Z", available_at="2026-08-21T00:05:00Z",
        status="revoked", supersedes_revision_id=first["universe_revision_id"],
    )

    page = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:07:00Z", venue="TPEX", limit=1)
    assert [item["identity_reference"]["official_code"] for item in page["items"]] == ["1001"]
    assert page["next_cursor"] is None


def test_list_order_and_cursor_follow_mapping_recovery(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("tenth-mapping-cursor")
    first_anchor = _add_tpex_anchor(repo, context, "1000")
    second_anchor = _add_tpex_anchor(repo, context, "1001")
    first = _master(
        repo, db, anchor=first_anchor, context=context, code="1000", logical_key="master-1000",
        revision_number=1, ingested_at="2026-08-21T00:02:00Z", available_at="2026-08-21T00:01:00Z",
    )
    _master(
        repo, db, anchor=second_anchor, context=context, code="1001", logical_key="master-1001",
        revision_number=1, ingested_at="2026-08-21T00:04:00Z", available_at="2026-08-21T00:03:00Z",
    )
    revoked = _master(
        repo, db, anchor=first_anchor, context=context, code="1000", logical_key="master-1000",
        revision_number=2, ingested_at="2026-08-21T00:06:00Z", available_at="2026-08-21T00:05:00Z",
        status="revoked", supersedes_revision_id=first["universe_revision_id"],
    )
    _master(
        repo, db, anchor=first_anchor, context=context, code="1000", logical_key="master-1000",
        revision_number=3, ingested_at="2026-08-21T00:09:00Z", available_at="2026-08-21T00:08:00Z",
        supersedes_revision_id=revoked["universe_revision_id"],
    )

    page = repo.list_instruments(knowledge_cutoff_at="2026-08-21T00:10:00Z", venue="TPEX", limit=1)
    assert page["items"][0]["identity_reference"]["canonical_symbol"] == "1000.TWO"
    assert page["next_cursor"]
    next_page = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:10:00Z", venue="TPEX", limit=1,
        cursor=page["next_cursor"],
    )
    assert [item["identity_reference"]["canonical_symbol"] for item in next_page["items"]] == ["1001.TWO"]
