from __future__ import annotations

from typing import Any

from src.repositories.universe_repository import UniverseRepository
from tests.test_phase13_fourteenth_review_remediation import _measure_sql_work
from tests.test_phase13_sixth_review_remediation import _ctx, _instrument_payload, _repo


CUTOFF = "2026-08-21T00:05:00Z"


def _add_obsolete_history(
    repo: UniverseRepository,
    db,
    *,
    code: str,
    context,
    revision_status: str = "accepted",
) -> dict[str, Any]:
    anchor = repo.allocate_instrument(
        venue="TWSE",
        official_code=code,
        source_identity=f"twse:{code}:v1",
        first_observed_at="2026-08-21T00:00:00Z",
        source_reference="fixture",
        context=context,
    )
    first = repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="twse-universe-master",
        logical_revision_key=f"master-{code}",
        revision_number=1,
        payload=_instrument_payload(
            db,
            venue="TWSE",
            code=code,
            display_name="OLD NAME",
            security_type="legacy_security",
            listing_status="delisted",
        ),
        context=context,
        idempotency_key=f"master-{code}-r1",
    )
    repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="twse-universe-master",
        logical_revision_key=f"master-{code}",
        revision_number=2,
        payload=_instrument_payload(
            db,
            venue="TWSE",
            code=code,
            display_name="CURRENT NAME",
            security_type="stock",
            listing_status="listed",
            status=revision_status,
            reason=None if revision_status == "accepted" else "fixture revoked correction",
            supersedes_revision_id=first["universe_revision_id"],
        ),
        context=context,
        idempotency_key=f"master-{code}-r2",
    )
    # The old terminated event is also intentionally followed by a newer
    # listed event.  A listing prefilter must not admit the old event when the
    # effective lifecycle state is already listed.
    repo.add_lifecycle_event(
        instrument_id=anchor["instrument_id"],
        event_type="terminated",
        effective_at="2026-08-21T00:03:00Z",
        available_at="2026-08-21T00:03:00Z",
        ingested_at="2026-08-21T00:03:00Z",
        source_reference="fixture old termination",
        reason="obsolete history",
        context=context,
    )
    repo.add_lifecycle_event(
        instrument_id=anchor["instrument_id"],
        event_type="listed",
        effective_at="2026-08-21T00:04:00Z",
        available_at="2026-08-21T00:04:00Z",
        ingested_at="2026-08-21T00:04:00Z",
        source_reference="fixture current listing",
        reason="effective current state",
        context=context,
    )
    return anchor


def test_obsolete_query_security_and_listing_history_does_not_drive_recursive_work(tmp_path):
    def measure(anchor_count: int) -> dict[str, int]:
        db_dir = tmp_path / f"obsolete-history-{anchor_count}"
        db_dir.mkdir()
        db, repo = _repo(db_dir)
        context = _ctx(f"fifteenth-obsolete-{anchor_count}")
        for offset in range(anchor_count):
            _add_obsolete_history(
                repo,
                db,
                code=f"{5000 + offset:04d}",
                context=context,
                revision_status="revoked" if offset == 0 else "accepted",
            )

        measurements: list[dict[str, int]] = []
        for kwargs in (
            {"query": "OLD NAME"},
            {"security_type": "legacy_security"},
            {"listing_status": "delisted"},
        ):
            result, steps, group_sizes = _measure_sql_work(
                repo,
                lambda kwargs=kwargs: repo.list_instruments(
                    knowledge_cutoff_at=CUTOFF,
                    venue="TWSE",
                    limit=1,
                    **kwargs,
                ),
            )
            assert result["items"] == []
            assert steps["candidate"] > 0
            assert steps["epoch"] == 0
            assert group_sizes == []
            measurements.append(steps)
        return {
            "candidate": sum(entry["candidate"] for entry in measurements),
            "epoch": sum(entry["epoch"] for entry in measurements),
        }

    small = measure(40)
    large = measure(400)
    assert small["epoch"] == large["epoch"] == 0
