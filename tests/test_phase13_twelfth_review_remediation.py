from __future__ import annotations

from src.domain.universe import ACTIONABLE_HUMAN_REASONS, universe_status_matrix_v1
from src.repositories.universe_repository import UniverseRepository
from tests.test_phase13_sixth_review_remediation import (
    _add_tpex_anchor,
    _ctx,
    _instrument_payload,
    _repo,
)
from tests.test_phase13_fifth_review_remediation import _add_instrument


AUTHORITATIVE_ACTIONABLE_REASONS = {
    "source_revision_awaiting_review",
    "manual_publication_evidence_required",
    "source_schema_review_required",
    "source_revision_revoked_without_corrected_revision",
    "canonical_mapping_unverified",
}


def _add_master(repo, db, *, anchor, context, code: str, key: str):
    return repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="tpex-universe-master",
        logical_revision_key=key,
        revision_number=1,
        payload=_instrument_payload(db, venue="TPEX", code=code),
        context=context,
        idempotency_key=key,
    )


def test_actionable_human_allowlist_matches_luk15_exactly():
    assert set(ACTIONABLE_HUMAN_REASONS) == AUTHORITATIVE_ACTIONABLE_REASONS
    assert universe_status_matrix_v1(
        {
            "identity_reference": {"instrument_id": "safe"},
            "operational_freshness": "current",
            "current_complete": True,
            "reasons": ["availability_unproven"],
        }
    )["status"] == "partial"


def test_lifecycle_awaiting_review_is_actionable_across_exact_resolve_and_list(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("twelfth-lifecycle-awaiting")
    anchor = _add_tpex_anchor(repo, context, "7010")
    _add_master(repo, db, anchor=anchor, context=context, code="7010", key="master-7010")
    repo.add_lifecycle_event(
        instrument_id=anchor["instrument_id"], event_type="terminated",
        effective_at="2026-08-21T00:03:00Z", available_at="2026-08-21T00:03:00Z",
        ingested_at="2026-08-21T00:03:00Z", source_reference="fixture",
        reason="pending review", status="awaiting_review", context=context,
    )
    cutoff = "2026-08-21T00:05:00Z"
    exact = repo.get_by_canonical("7010.TWO", knowledge_cutoff_at=cutoff)
    resolved = repo.resolve(official_code="7010", venue="TPEX", knowledge_cutoff_at=cutoff)
    listed = repo.list_instruments(knowledge_cutoff_at=cutoff, venue="TPEX", limit=10)

    assert exact["status"] == resolved["status"] == "needs_human_input"
    assert listed["items"][0]["status"] == "needs_human_input"
    assert "source_revision_awaiting_review" in exact["reasons"]
    assert exact["reasons"] == resolved["reasons"] == listed["items"][0]["reasons"]
    assert exact["status"] != "available"


def test_operational_awaiting_review_is_actionable_without_changing_identity(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("twelfth-operational-awaiting")
    anchor = _add_tpex_anchor(repo, context, "7011")
    _add_master(repo, db, anchor=anchor, context=context, code="7011", key="master-7011")
    repo.add_operational_event(
        instrument_id=anchor["instrument_id"], trading_state="suspended",
        effective_at="2026-08-21T00:03:00Z", available_at="2026-08-21T00:03:00Z",
        ingested_at="2026-08-21T00:03:00Z", source_reference="fixture",
        reason="pending review", status="awaiting_review", context=context,
    )
    result = repo.resolve(
        official_code="7011", venue="TPEX", knowledge_cutoff_at="2026-08-21T00:05:00Z",
    )
    assert result["status"] == "needs_human_input"
    assert result["identity_reference"]["official_code"] == "7011"
    assert result["identity_reference"]["trading_state"] == "unknown"
    assert "source_revision_awaiting_review" in result["reasons"]


def test_non_actionable_event_blocker_is_partial_and_accepted_event_is_neutral():
    blocked = UniverseRepository._compose_event_state(
        [{
            "event_type": "terminated", "status": "unknown", "available_at": "2026-08-21T00:01:00Z",
            "ingested_at": "2026-08-21T00:01:00Z", "event_date": None, "effective_at": "2026-08-21T00:01:00Z",
            "lifecycle_event_id": "blocked",
        }], [], cutoff="2026-08-21T00:05:00Z",
    )
    assert blocked["event_state_blocked"] is True
    assert blocked["event_reasons"] == ["event_state_blocked"]
    status = universe_status_matrix_v1({
        "identity_reference": {"instrument_id": "safe"},
        "operational_freshness": "current", "current_complete": True,
        "reasons": blocked["event_reasons"],
    })
    assert status["status"] == "partial"

    neutral = UniverseRepository._compose_event_state(
        [{
            "event_type": "listed", "status": "accepted", "available_at": "2026-08-21T00:01:00Z",
            "ingested_at": "2026-08-21T00:01:00Z", "event_date": None, "effective_at": "2026-08-21T00:01:00Z",
            "lifecycle_event_id": "accepted",
        }], [], cutoff="2026-08-21T00:05:00Z",
    )
    assert not neutral.get("event_state_blocked")
    assert "event_reasons" not in neutral


def test_list_epoch_selection_does_not_materialize_python_universe(tmp_path, monkeypatch):
    db, repo = _repo(tmp_path)
    context = _ctx("twelfth-bounded-epoch")
    for offset in range(40):
        _add_instrument(repo, db, code=f"{7200 + offset}", context=context)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("list must not materialize all effective epoch IDs in Python")

    monkeypatch.setattr(repo, "_effective_epoch_instrument_ids", fail_if_called)
    page = repo.list_instruments(
        knowledge_cutoff_at="2026-08-21T00:05:00Z", limit=1,
    )
    assert len(page["items"]) == 1
    assert page["next_cursor"]


def test_epoch_page_work_stays_bounded_as_universe_grows(tmp_path):
    """Measure candidate/epoch VM work, not merely SQL statement count.

    The venue-health query is intentionally independent and may inspect its
    resource feeds.  This regression isolates the candidate DISTINCT window
    and the recursive epoch query and proves their SQLite VM work does not
    grow with the number of unrelated Universe anchors for a fixed limit.
    """

    def measure(anchor_count: int) -> tuple[int, list[str]]:
        db_dir = tmp_path / f"bounded-{anchor_count}"
        db_dir.mkdir()
        db, repo = _repo(db_dir)
        context = _ctx(f"bounded-growth-{anchor_count}")
        for offset in range(anchor_count):
            _add_instrument(repo, db, code=f"{7400 + offset:04d}", context=context)

        original_connect = repo._connect
        observations: list[list[object]] = []
        current: list[object | None] = [None]

        def measured_connect():
            connection = original_connect()

            def trace(statement: str):
                normalized = statement.lstrip().upper()
                if "UNIVERSE_CANDIDATE_NULL_V2" in normalized:
                    entry: list[object] = ["candidate_null", 0, statement]
                    observations.append(entry)
                    current[0] = entry
                elif "UNIVERSE_CANDIDATE_MAPPED_V2" in normalized:
                    entry = ["candidate_mapped", 0, statement]
                    observations.append(entry)
                    current[0] = entry
                elif normalized.startswith("WITH RECURSIVE"):
                    entry = ["epoch", 0, statement]
                    observations.append(entry)
                    current[0] = entry
                else:
                    current[0] = None

            def progress() -> int:
                if current[0] is not None:
                    current[0][1] = int(current[0][1]) + 1  # type: ignore[index]
                return 0

            connection.set_trace_callback(trace)
            connection.set_progress_handler(progress, 100)
            return connection

        repo._connect = measured_connect  # type: ignore[method-assign]
        page = repo.list_instruments(
            knowledge_cutoff_at="2026-08-21T00:05:00Z", venue="TWSE", limit=1,
        )
        assert len(page["items"]) == 1
        assert page["next_cursor"]
        kinds = [str(entry[0]) for entry in observations]
        assert kinds == ["candidate_null", "candidate_mapped", "epoch"]
        assert "LIMIT 4" in str(observations[1][2]).upper()
        bounded_steps = sum(
            int(entry[1])
            for entry in observations
            if entry[0] in {"candidate_mapped", "epoch"}
        )
        return bounded_steps, kinds

    small_steps, _ = measure(40)
    large_steps, _ = measure(400)
    assert large_steps <= small_steps * 2
