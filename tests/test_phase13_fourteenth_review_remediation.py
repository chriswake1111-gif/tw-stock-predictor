from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.repositories.universe_repository import UniverseRepository
from tests.test_phase13_sixth_review_remediation import (
    _ctx,
    _instrument_payload,
    _repo,
)


CUTOFF = "2026-08-21T00:05:00Z"


def _add_twse(
    repo: UniverseRepository,
    db,
    *,
    code: str,
    context,
    display_name: str | None = None,
    security_type: str = "stock",
    canonical_symbol: str | None = None,
):
    anchor = repo.allocate_instrument(
        venue="TWSE",
        official_code=code,
        source_identity=f"twse:{code}:v1",
        first_observed_at="2026-08-21T00:00:00Z",
        source_reference="fixture",
        context=context,
    )
    overrides: dict[str, Any] = {
        "display_name": display_name or f"Fixture {code}",
        "security_type": security_type,
    }
    if canonical_symbol is not None:
        overrides["canonical_symbol"] = canonical_symbol
    repo.add_revision(
        instrument_id=anchor["instrument_id"],
        resource_id="twse-universe-master",
        logical_revision_key=f"master-{code}",
        revision_number=1,
        payload=_instrument_payload(db, venue="TWSE", code=code, **overrides),
        context=context,
        idempotency_key=f"master-{code}",
    )
    return anchor


def _measure_sql_work(
    repo: UniverseRepository,
    action: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, int], list[int]]:
    original_connect = repo._connect
    steps = {"candidate": 0, "epoch": 0}
    epoch_group_sizes: list[int] = []
    current = [""]
    original_epoch_cte = repo._list_epoch_cte

    def measured_epoch_cte(*, cutoff, venue, candidate_groups):
        epoch_group_sizes.append(len(candidate_groups))
        return original_epoch_cte(
            cutoff=cutoff,
            venue=venue,
            candidate_groups=candidate_groups,
        )

    def measured_connect():
        connection = original_connect()

        def trace(statement: str):
            normalized = statement.upper()
            if "UNIVERSE_CANDIDATE_" in normalized:
                current[0] = "candidate"
            elif statement.lstrip().upper().startswith("WITH RECURSIVE"):
                current[0] = "epoch"
            else:
                current[0] = ""

        def progress() -> int:
            if current[0]:
                steps[current[0]] += 1
            return 0

        connection.set_trace_callback(trace)
        connection.set_progress_handler(progress, 25)
        return connection

    repo._connect = measured_connect  # type: ignore[method-assign]
    repo._list_epoch_cte = measured_epoch_cte  # type: ignore[method-assign]
    try:
        return action(), steps, epoch_group_sizes
    finally:
        repo._connect = original_connect  # type: ignore[method-assign]
        repo._list_epoch_cte = original_epoch_cte  # type: ignore[method-assign]


def test_null_canonical_lane_precedes_mapped_codes_and_cursor_matches_public_order(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("fourteenth-null-order")
    _add_twse(repo, db, code="1000", context=context)
    _add_twse(repo, db, code="1001", context=context)
    # Invalid caller mapping is preserved fail-closed as a safe reference with
    # canonical_symbol=NULL.  Its later official code must still sort first.
    _add_twse(
        repo,
        db,
        code="9999",
        context=context,
        canonical_symbol="NOT-A-CANONICAL-SYMBOL",
    )

    first = repo.list_instruments(
        knowledge_cutoff_at=CUTOFF,
        venue="TWSE",
        limit=1,
    )
    second = repo.list_instruments(
        knowledge_cutoff_at=CUTOFF,
        venue="TWSE",
        limit=1,
        cursor=first["next_cursor"],
    )
    complete = repo.list_instruments(
        knowledge_cutoff_at=CUTOFF,
        venue="TWSE",
        limit=100,
    )

    assert first["items"][0]["identity_reference"]["official_code"] == "9999"
    assert first["items"][0]["identity_reference"]["canonical_symbol"] is None
    assert second["items"][0]["identity_reference"]["canonical_symbol"] == "1000.TW"
    public_keys = [
        (
            item["identity_reference"]["venue"],
            item["identity_reference"]["canonical_symbol"] or "",
            item["identity_reference"]["instrument_id"],
        )
        for item in complete["items"]
    ]
    assert public_keys == sorted(public_keys)
    assert len(public_keys) == len(set(public_keys)) == 3
    assert complete["order"] == "venue ASC, canonical_symbol ASC, instrument_id ASC"


def test_no_match_query_never_runs_recursive_epoch_work_as_universe_grows(tmp_path):
    def measure(anchor_count: int) -> tuple[int, int]:
        db_dir = tmp_path / f"no-match-{anchor_count}"
        db_dir.mkdir()
        db, repo = _repo(db_dir)
        context = _ctx(f"fourteenth-no-match-{anchor_count}")
        for offset in range(anchor_count):
            _add_twse(repo, db, code=f"{2000 + offset:04d}", context=context)
        result, steps, group_sizes = _measure_sql_work(
            repo,
            lambda: repo.list_instruments(
                knowledge_cutoff_at=CUTOFF,
                venue="TWSE",
                query="NO-SUCH-DISPLAY-NAME",
                limit=1,
            ),
        )
        assert result["items"] == []
        assert steps["candidate"] > 0
        assert steps["epoch"] == 0
        assert group_sizes == []
        return steps["candidate"], steps["epoch"]

    small_candidate_work, small_epoch_work = measure(40)
    large_candidate_work, large_epoch_work = measure(400)
    assert small_candidate_work > 0
    assert large_candidate_work > 0
    assert small_epoch_work == large_epoch_work == 0


def test_sparse_late_display_name_keeps_recursive_work_page_bounded(tmp_path):
    def measure(anchor_count: int) -> tuple[int, list[int], str]:
        db_dir = tmp_path / f"late-name-{anchor_count}"
        db_dir.mkdir()
        db, repo = _repo(db_dir)
        context = _ctx(f"fourteenth-late-name-{anchor_count}")
        target_code = f"{3000 + anchor_count - 1:04d}"
        for offset in range(anchor_count):
            code = f"{3000 + offset:04d}"
            _add_twse(
                repo,
                db,
                code=code,
                context=context,
                display_name="Sparse Needle" if code == target_code else f"Fixture {code}",
            )
        result, steps, group_sizes = _measure_sql_work(
            repo,
            lambda: repo.list_instruments(
                knowledge_cutoff_at=CUTOFF,
                venue="TWSE",
                query="Sparse Needle",
                limit=1,
            ),
        )
        return (
            steps["epoch"],
            group_sizes,
            result["items"][0]["identity_reference"]["official_code"],
        )

    small_steps, small_groups, small_code = measure(40)
    large_steps, large_groups, large_code = measure(400)
    assert small_code == "3039"
    assert large_code == "3399"
    assert small_groups == large_groups == [1]
    assert large_steps <= max(1, small_steps) * 2


def test_sparse_security_and_lifecycle_listing_filters_resolve_only_candidates(tmp_path):
    db, repo = _repo(tmp_path)
    context = _ctx("fourteenth-sparse-filters")
    target = None
    for offset in range(80):
        code = f"{4000 + offset:04d}"
        anchor = _add_twse(
            repo,
            db,
            code=code,
            context=context,
            security_type="etf" if offset == 79 else "stock",
        )
        if offset == 79:
            target = anchor
    assert target is not None
    repo.add_lifecycle_event(
        instrument_id=target["instrument_id"],
        event_type="terminated",
        effective_at="2026-08-21T00:03:00Z",
        available_at="2026-08-21T00:03:00Z",
        ingested_at="2026-08-21T00:03:00Z",
        source_reference="fixture termination",
        reason="delisted",
        context=context,
    )

    security_result, security_steps, security_groups = _measure_sql_work(
        repo,
        lambda: repo.list_instruments(
            knowledge_cutoff_at=CUTOFF,
            venue="TWSE",
            security_type="etf",
            limit=1,
        ),
    )
    listing_result, listing_steps, listing_groups = _measure_sql_work(
        repo,
        lambda: repo.list_instruments(
            knowledge_cutoff_at=CUTOFF,
            venue="TWSE",
            listing_status="delisted",
            limit=1,
        ),
    )

    assert [
        item["identity_reference"]["official_code"]
        for item in security_result["items"]
    ] == ["4079"]
    assert [
        item["identity_reference"]["official_code"]
        for item in listing_result["items"]
    ] == ["4079"]
    assert security_groups == listing_groups == [1]
    assert security_steps["epoch"] > 0
    assert listing_steps["epoch"] > 0
    assert max(security_groups + listing_groups) <= 4
