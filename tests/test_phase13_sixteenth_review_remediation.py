from __future__ import annotations

from typing import Any

from tests.test_phase13_eighth_review_remediation import (
    _publication_evidence,
    _seed_corroborating_raw,
)
from tests.test_phase13_fourteenth_review_remediation import (
    CUTOFF,
    _measure_sql_work,
)
from tests.test_phase13_sixth_review_remediation import (
    _add_tpex_anchor,
    _ctx,
    _instrument_payload,
    _repo,
)


def _add_tpex_cross_chain(
    repo,
    db,
    *,
    code: str,
    context,
    evidence_id: str,
    raw_id: str,
    raw_hash: str,
    security_type: str = "legacy_security",
):
    anchor = _add_tpex_anchor(repo, context, code)
    repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key=f"master-{code}",
        revision_number=2,
        payload=_instrument_payload(
            db,
            venue="TPEX",
            code=code,
            display_name="NEW NAME",
            security_type="stock",
        ),
        context=context,
        idempotency_key=f"master-{code}",
    )
    repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-corroborating",
        logical_revision_key=f"corroborating-{code}",
        revision_number=1,
        payload=_instrument_payload(
            db,
            venue="TPEX",
            code=code,
            display_name="OLD NAME",
            security_type=security_type,
            freshness_mode="event_observation",
            current_complete=True,
            raw_resource_revision_id=raw_id,
            raw_payload_sha256=raw_hash,
            publication_evidence_id=evidence_id,
        ),
        context=context,
        idempotency_key=f"corroborating-{code}",
    )


def _cross_chain_measure(tmp_path, count: int, **filters: Any):
    db_dir = tmp_path / f"cross-chain-{count}-{filters.get('security_type', 'query')}"
    db_dir.mkdir()
    db, repo = _repo(db_dir)
    context = _ctx(f"sixteenth-cross-chain-{count}-{filters.get('security_type', 'query')}")
    raw_id, raw_hash = _seed_corroborating_raw(db)
    evidence = _publication_evidence(db, logical_key="cross-chain")
    for offset in range(count):
        _add_tpex_cross_chain(
            repo,
            db,
            code=f"{6000 + offset:04d}",
            context=context,
            evidence_id=evidence["publication_evidence_id"],
            raw_id=raw_id,
            raw_hash=raw_hash,
        )
    return _measure_sql_work(
        repo,
        lambda: repo.list_instruments(
            knowledge_cutoff_at=CUTOFF,
            venue="TPEX",
            limit=1,
            **filters,
        ),
    )


def test_cross_chain_old_name_is_filtered_before_recursive_epoch_work(tmp_path):
    small, small_steps, small_groups = _cross_chain_measure(tmp_path, 40, query="OLD NAME")
    large, large_steps, large_groups = _cross_chain_measure(tmp_path, 400, query="OLD NAME")

    assert small["items"] == []
    assert large["items"] == []
    assert small_groups == large_groups == []
    assert small_steps["epoch"] == large_steps["epoch"] == 0


def test_cross_chain_old_security_type_is_filtered_before_recursive_epoch_work(tmp_path):
    result, steps, groups = _cross_chain_measure(
        tmp_path,
        40,
        security_type="legacy_security",
    )

    assert result["items"] == []
    assert groups == []
    assert steps["epoch"] == 0


def _lifecycle_overlay_measure(tmp_path, count: int, *, revision_status: str, event_type: str):
    db_dir = tmp_path / f"lifecycle-overlay-{revision_status}-{event_type}-{count}"
    db_dir.mkdir()
    db, repo = _repo(db_dir)
    context = _ctx(f"sixteenth-lifecycle-{revision_status}-{event_type}-{count}")
    for offset in range(count):
        code = f"{7000 + offset:04d}"
        anchor = repo.allocate_instrument(
            venue="TWSE",
            official_code=code,
            source_identity=f"twse:{code}:v1",
            first_observed_at="2026-08-21T00:00:00Z",
            source_reference="fixture",
            context=context,
        )
        repo.add_revision(
            instrument_id=anchor["instrument_id"],
            resource_id="twse-universe-master",
            logical_revision_key=f"master-{code}",
            revision_number=1,
            payload=_instrument_payload(
                db,
                venue="TWSE",
                code=code,
                listing_status=revision_status,
            ),
            context=context,
            idempotency_key=f"master-{code}",
        )
        repo.add_lifecycle_event(
            instrument_id=anchor["instrument_id"],
            event_type=event_type,
            effective_at="2026-08-21T00:03:00Z",
            available_at="2026-08-21T00:03:00Z",
            ingested_at="2026-08-21T00:03:00Z",
            source_reference="fixture lifecycle overlay",
            reason="sixteenth review regression",
            context=context,
        )
    return _measure_sql_work(
        repo,
        lambda: repo.list_instruments(
            knowledge_cutoff_at=CUTOFF,
            venue="TWSE",
            listing_status="delisted" if event_type == "listed" else "listed",
            limit=1,
        ),
    )


def test_latest_listed_event_blocks_delisted_revision_before_recursive_work(tmp_path):
    small, small_steps, small_groups = _lifecycle_overlay_measure(
        tmp_path, 40, revision_status="delisted", event_type="listed"
    )
    large, large_steps, large_groups = _lifecycle_overlay_measure(
        tmp_path, 400, revision_status="delisted", event_type="listed"
    )

    assert small["items"] == []
    assert large["items"] == []
    assert small_groups == large_groups == []
    assert small_steps["epoch"] == large_steps["epoch"] == 0


def test_latest_terminated_event_blocks_listed_revision_before_recursive_work(tmp_path):
    result, steps, groups = _lifecycle_overlay_measure(
        tmp_path, 40, revision_status="listed", event_type="terminated"
    )

    assert result["items"] == []
    assert groups == []
    assert steps["epoch"] == 0
