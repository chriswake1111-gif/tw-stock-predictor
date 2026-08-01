import sqlite3

import pytest

from src.domain.valuation import (
    ForwardEPSObservation,
    ForwardEPSSourceType,
)
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.repositories.migration_runner import MIGRATION_ID, apply_valuation_migration
from src.repositories import migration_runner


def observation(
    *,
    series: str = "2330-2027-broker-a",
    revision: int = 1,
    revision_of: str | None = None,
    eps: float = 50.0,
    available_at: str = "2026-08-01T09:00:00+08:00",
) -> ForwardEPSObservation:
    return ForwardEPSObservation(
        logical_series_id=series,
        revision_number=revision,
        revision_of=revision_of,
        symbol="2330.TW",
        fiscal_year=2027,
        eps_base=eps,
        source_name="Broker A",
        source_type=ForwardEPSSourceType.BROKER_REPORT,
        published_at="2026-08-01",
        available_at=available_at,
    )


def test_migration_is_versioned_transactional_and_rerunnable(tmp_path):
    db_path = str(tmp_path / "valuation.db")

    first = apply_valuation_migration(db_path)
    second = apply_valuation_migration(db_path)

    assert first["migration_id"] == MIGRATION_ID
    assert first["applied"] is True
    assert second["applied"] is False
    assert second["checksum_sha256"] == first["checksum_sha256"]
    with sqlite3.connect(db_path) as conn:
        migration_rows = conn.execute(
            "SELECT version_id, checksum_sha256 FROM schema_migrations"
        ).fetchall()
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert migration_rows == [(MIGRATION_ID, first["checksum_sha256"])]
    assert {"forward_eps_observations", "pe_scenarios"}.issubset(tables)


def test_migration_rolls_back_all_statements_on_failure(monkeypatch, tmp_path):
    db_path = str(tmp_path / "broken.db")
    broken = tmp_path / "broken.sql"
    broken.write_text(
        "CREATE TABLE must_rollback (id INTEGER);\nTHIS IS NOT VALID SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(migration_runner, "MIGRATION_FILE", broken)

    with pytest.raises(sqlite3.OperationalError):
        migration_runner.apply_valuation_migration(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "must_rollback" not in tables
    assert "schema_migrations" not in tables


def test_same_day_publication_is_hidden_before_exact_timestamp(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    repo.add_forward_eps(
        observation(),
        "same-day",
        ingested_at="2026-08-01T09:01:00+08:00",
    )

    before = repo.forward_eps_as_of("2330.TW", "2026-08-01T00:30:00Z")
    after_available_before_ingest = repo.forward_eps_as_of(
        "2330.TW", "2026-08-01T01:00:30Z"
    )
    after_ingest = repo.forward_eps_as_of("2330.TW", "2026-08-01T01:01:00Z")

    assert before == []
    assert after_available_before_ingest == []
    assert len(after_ingest) == 1


def test_asof_query_reconstructs_revision_state(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    first = repo.add_forward_eps(
        observation(available_at="2026-07-01T08:00:00Z"),
        "rev-1",
        ingested_at="2026-07-01T08:05:00Z",
    )
    second = repo.add_forward_eps(
        observation(
            revision=2,
            revision_of=first["id"],
            eps=55.0,
            available_at="2026-07-15T08:00:00Z",
        ),
        "rev-2",
        ingested_at="2026-07-16T08:00:00Z",
    )

    before_ingestion = repo.forward_eps_as_of(
        "2330.TW", "2026-07-15T12:00:00Z"
    )
    after_ingestion = repo.forward_eps_as_of(
        "2330.TW", "2026-07-16T08:00:00Z"
    )

    assert [row["id"] for row in before_ingestion] == [first["id"]]
    assert [row["id"] for row in after_ingestion] == [second["id"]]
    assert after_ingestion[0]["revision_number"] == 2


def test_repository_fingerprint_prevents_duplicate_payload(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))

    first = repo.add_forward_eps(observation(), "request-a")
    duplicate = repo.add_forward_eps(observation(), "request-b")

    assert first["id"] == duplicate["id"]
    assert first["created"] is True
    assert duplicate["created"] is False
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM forward_eps_observations").fetchone()[0] == 1
