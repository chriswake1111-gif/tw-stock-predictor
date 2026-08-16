import sqlite3

import pytest

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.research_workflow import ReviewAcknowledgment
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.research_workflow_repository import ResearchWorkflowRepository


def _snapshot(symbol="2330.TW"):
    return AnalysisSnapshot(
        symbol=symbol, knowledge_cutoff_at="2026-08-01T00:00:00Z",
        capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
        model_version="2.0.0", used_rule_versions={}, source_resource_versions=[],
        manual_approval_ids=[], output={"status": "available", "symbol": symbol},
        created_at="2026-08-02T00:00:00Z",
    )


def test_add_duplicate_restore_and_unarchive_converge_without_losing_history(tmp_path):
    db = str(tmp_path / "workflow.db")
    repo = ResearchWorkflowRepository(db)
    first = repo.add_membership("2330")
    duplicate = repo.add_membership("2330.TW")
    assert first["created"] is True
    assert duplicate["watchlist_item_id"] == first["watchlist_item_id"]
    assert duplicate["created"] is False and duplicate["restored"] is False

    snap = AnalysisSnapshotRepository(db).add(_snapshot(), "snapshot-key")
    event = repo.append_review_event(ReviewAcknowledgment(
        first["watchlist_item_id"], snap["snapshot_id"],
        "2026-08-02T00:00:00Z", "review-key",
    ), reviewed_at="2026-08-03T00:00:00Z")
    repo.archive(first["watchlist_item_id"])
    restored = repo.add_membership("2330")
    assert restored["watchlist_item_id"] == first["watchlist_item_id"]
    assert restored["restored"] is True and restored["membership_state"] == "active"
    assert repo.add_membership("2330")["restored"] is False
    repo.archive(first["watchlist_item_id"])
    explicit = repo.unarchive(first["watchlist_item_id"])
    assert explicit["watchlist_item_id"] == restored["watchlist_item_id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_review_events").fetchone()[0] == 1
        assert conn.execute("SELECT review_event_id FROM research_review_events").fetchone()[0] == event["review_event_id"]


def test_review_event_is_append_only_and_idempotent(tmp_path):
    db = str(tmp_path / "events.db")
    repo = ResearchWorkflowRepository(db)
    item = repo.add_membership("2330")
    snap = AnalysisSnapshotRepository(db).add(_snapshot(), "snapshot-key")
    command = ReviewAcknowledgment(
        item["watchlist_item_id"], snap["snapshot_id"],
        "2026-08-02T00:00:00+00:00", "review-key",
    )
    first = repo.append_review_event(command, reviewed_at="2026-08-03T00:00:00Z")
    retry = repo.append_review_event(command, reviewed_at="2026-08-04T00:00:00Z")
    assert retry["created"] is False
    assert retry["review_event_id"] == first["review_event_id"]
    with pytest.raises(ValueError, match="review_idempotency_conflict"):
        repo.append_review_event(ReviewAcknowledgment(
            item["watchlist_item_id"], snap["snapshot_id"],
            "2026-08-03T00:00:00Z", "review-key",
        ), reviewed_at="2026-08-04T00:00:00Z")
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE research_review_events SET reviewed_at='x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM research_review_events")


def test_cross_symbol_acknowledgment_rejected_and_new_event_preserves_history(tmp_path):
    db = str(tmp_path / "cross-symbol.db")
    repo = ResearchWorkflowRepository(db)
    first_item = repo.add_membership("2330")
    second_item = repo.add_membership("2317")
    first_snapshot = AnalysisSnapshotRepository(db).add(_snapshot("2330.TW"), "first")
    second_snapshot = AnalysisSnapshotRepository(db).add(_snapshot("2317.TW"), "second")
    with pytest.raises(ValueError, match="acknowledged_snapshot_symbol_mismatch"):
        repo.append_review_event(ReviewAcknowledgment(
            first_item["watchlist_item_id"], second_snapshot["snapshot_id"],
            "2026-08-02T00:00:00Z", "cross-symbol",
        ), reviewed_at="2026-08-03T00:00:00Z")
    first = repo.append_review_event(ReviewAcknowledgment(
        first_item["watchlist_item_id"], first_snapshot["snapshot_id"],
        "2026-08-02T00:00:00Z", "event-one",
    ), reviewed_at="2026-08-03T00:00:00Z")
    later = repo.append_review_event(ReviewAcknowledgment(
        first_item["watchlist_item_id"], first_snapshot["snapshot_id"],
        "2026-08-04T00:00:00Z", "event-two",
    ), reviewed_at="2026-08-05T00:00:00Z")
    assert first["review_event_id"] != later["review_event_id"]
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        latest = repo.latest_review_events_with_connection(
            conn, [first_item["watchlist_item_id"], second_item["watchlist_item_id"]]
        )
        assert latest[first_item["watchlist_item_id"]]["review_event_id"] == later["review_event_id"]
        assert conn.execute("SELECT COUNT(*) FROM research_review_events").fetchone()[0] == 2


def test_archive_and_unarchive_are_idempotent(tmp_path):
    repo = ResearchWorkflowRepository(str(tmp_path / "membership-state.db"))
    item = repo.add_membership("2330")
    archived = repo.archive(item["watchlist_item_id"])
    archived_again = repo.archive(item["watchlist_item_id"])
    assert archived_again == archived
    active = repo.unarchive(item["watchlist_item_id"])
    active_again = repo.unarchive(item["watchlist_item_id"])
    assert active_again == active


def test_membership_ordering_is_neutral_deterministic_and_fail_closed(tmp_path):
    db = str(tmp_path / "ordering.db")
    repo = ResearchWorkflowRepository(db)
    items = [repo.add_membership(symbol) for symbol in ("3008", "1000", "2000")]
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        by_symbol = [item["symbol"] for item in repo.list_memberships_with_connection(conn)]
        assert by_symbol == ["1000.TW", "2000.TW", "3008.TW"]
        conn.execute(
            "UPDATE research_watchlist_items SET updated_at=? WHERE symbol=?",
            ("2026-08-17T03:00:00Z", "3008.TW"),
        )
        conn.execute(
            "UPDATE research_watchlist_items SET updated_at=? WHERE symbol IN (?,?)",
            ("2026-08-17T02:00:00Z", "1000.TW", "2000.TW"),
        )
        explicit_symbol = repo.list_memberships_with_connection(conn, order="symbol")
        updated = repo.list_memberships_with_connection(conn, order="updated_at")
        assert [item["watchlist_item_id"] for item in explicit_symbol] == [
            item["watchlist_item_id"] for item in sorted(items, key=lambda item: (
                item["symbol"], item["watchlist_item_id"]
            ))
        ]
        assert [item["symbol"] for item in updated] == [
            "3008.TW", "1000.TW", "2000.TW"
        ]
        for invalid in ("best", "priority", "opportunity"):
            with pytest.raises(ValueError, match="research_queue_order_invalid"):
                repo.list_memberships_with_connection(conn, order=invalid)
