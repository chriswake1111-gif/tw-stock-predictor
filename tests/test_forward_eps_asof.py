import sqlite3

import pytest

from src.domain.valuation import (
    ApprovalResourceType,
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    ValuationApproval,
)
from src.repositories import migration_runner
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.repositories.migration_runner import (
    MIGRATION_ID,
    MIGRATION_IDS,
    apply_valuation_migration,
)


def observation(**overrides) -> ForwardEPSObservation:
    values = {
        "logical_series_id": "2330-2027-broker-a",
        "revision_number": 1,
        "revision_of": None,
        "symbol": "2330.TW",
        "fiscal_year": 2027,
        "eps_base": 50.0,
        "source_name": "Broker A",
        "source_type": ForwardEPSSourceType.BROKER_REPORT,
        "published_at": "2026-08-01",
        "available_at": "2026-08-01T09:00:00+08:00",
        "unit": "TWD_per_share",
    }
    values.update(overrides)
    return ForwardEPSObservation(**values)


def approve_eps(
    repo: ForwardEPSRepository,
    resource_id: str,
    *,
    key: str,
    available_at: str,
    ingested_at: str,
    decision: ApprovalStatus = ApprovalStatus.APPROVED,
    evidence_level: str = "A",
    project_operationalization: bool = False,
):
    return repo.add_approval(
        ValuationApproval(
            approval_id=f"approval-{key}",
            resource_type=ApprovalResourceType.FORWARD_EPS,
            resource_id=resource_id,
            decision=decision,
            rule_id="VAL-02",
            evidence_level=evidence_level,
            project_operationalization=project_operationalization,
            approved_by="test-admin",
            rationale="verified source review",
            available_at=available_at,
        ),
        f"approval-key-{key}",
        ingested_at=ingested_at,
    )


def test_migration_is_versioned_transactional_and_rerunnable(tmp_path):
    db_path = str(tmp_path / "valuation.db")

    first = apply_valuation_migration(db_path)
    second = apply_valuation_migration(db_path)

    assert first["migration_id"] == MIGRATION_ID
    assert first["applied"] is True
    assert first["applied_migration_ids"] == list(MIGRATION_IDS)
    assert second["applied"] is False
    assert second["applied_migration_ids"] == []
    with sqlite3.connect(db_path) as conn:
        migration_rows = conn.execute(
            "SELECT version_id FROM schema_migrations ORDER BY version_id"
        ).fetchall()
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        pe_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(pe_scenarios)")
        }
    assert migration_rows == [(migration_id,) for migration_id in MIGRATION_IDS]
    assert {
        "forward_eps_observations",
        "pe_scenarios",
        "valuation_approvals",
        "valuation_idempotency_keys",
    }.issubset(tables)
    assert "evidence_basis_rule_id" in pe_columns


def test_migration_creates_missing_database_parent_directory(tmp_path):
    db_path = tmp_path / "missing" / "nested" / "valuation.db"

    result = apply_valuation_migration(str(db_path))

    assert result["applied"] is True
    assert db_path.is_file()


def test_migration_rolls_back_all_statements_on_failure(monkeypatch, tmp_path):
    db_path = str(tmp_path / "broken.db")
    broken = tmp_path / "broken.sql"
    broken.write_text(
        "CREATE TABLE must_rollback (id INTEGER);\nTHIS IS NOT VALID SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(migration_runner, "MIGRATION_IDS", (MIGRATION_ID,))
    monkeypatch.setattr(migration_runner, "MIGRATION_FILES", (broken,))

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
    resource = repo.add_forward_eps(
        observation(), "same-day", ingested_at="2026-08-01T09:01:00+08:00"
    )
    approve_eps(
        repo,
        resource["id"],
        key="same-day",
        available_at="2026-08-01T09:01:00+08:00",
        ingested_at="2026-08-01T09:01:00+08:00",
    )

    before = repo.forward_eps_as_of("2330.TW", "2026-08-01T00:30:00Z")
    after = repo.forward_eps_as_of("2330.TW", "2026-08-01T01:01:00Z")

    assert before == []
    assert len(after) == 1
    assert after[0]["verified_approval_id"] == "approval-same-day"


def test_asof_query_reconstructs_revision_and_approval_state(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    first = repo.add_forward_eps(
        observation(published_at="2026-07-01", available_at="2026-07-01T08:00:00Z"),
        "rev-1",
        ingested_at="2026-07-01T08:05:00Z",
    )
    approve_eps(
        repo, first["id"], key="rev-1",
        available_at="2026-07-01T08:06:00Z",
        ingested_at="2026-07-01T08:06:00Z",
    )
    second = repo.add_forward_eps(
        observation(
            revision_number=2,
            revision_of=first["id"],
            eps_base=55.0,
            published_at="2026-07-15",
            available_at="2026-07-15T08:00:00Z",
        ),
        "rev-2",
        ingested_at="2026-07-16T08:00:00Z",
    )
    approve_eps(
        repo, second["id"], key="rev-2",
        available_at="2026-07-16T08:01:00Z",
        ingested_at="2026-07-16T08:01:00Z",
    )

    before_ingestion = repo.forward_eps_as_of(
        "2330.TW", "2026-07-15T12:00:00Z"
    )
    after_ingestion = repo.forward_eps_as_of(
        "2330.TW", "2026-07-16T08:01:00Z"
    )

    assert [row["id"] for row in before_ingestion] == [first["id"]]
    assert [row["id"] for row in after_ingestion] == [second["id"]]


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("symbol", "2454.TW"),
        ("fiscal_year", 2028),
        ("source_name", "Broker B"),
        ("source_type", ForwardEPSSourceType.MANUAL),
        ("unit", "USD_per_share"),
    ],
)
def test_forward_eps_revision_rejects_identity_changes(tmp_path, field, changed):
    repo = ForwardEPSRepository(str(tmp_path / f"{field}.db"))
    first = repo.add_forward_eps(observation(), f"first-{field}")
    changes = {
        "revision_number": 2,
        "revision_of": first["id"],
        "eps_base": 51.0,
        field: changed,
    }

    with pytest.raises(ValueError, match="revision cannot change identity field"):
        repo.add_forward_eps(observation(**changes), f"second-{field}")


def test_revoked_forward_eps_is_removed_after_revocation_cutoff(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    resource = repo.add_forward_eps(
        observation(), "eps", ingested_at="2026-08-01T01:00:00Z"
    )
    approve_eps(
        repo, resource["id"], key="approved",
        available_at="2026-08-01T02:00:00Z",
        ingested_at="2026-08-01T02:00:00Z",
    )
    approve_eps(
        repo, resource["id"], key="revoked",
        decision=ApprovalStatus.REVOKED,
        available_at="2026-08-02T02:00:00Z",
        ingested_at="2026-08-02T02:00:00Z",
    )

    assert len(repo.forward_eps_as_of("2330.TW", "2026-08-01T12:00:00Z")) == 1
    assert repo.forward_eps_as_of("2330.TW", "2026-08-02T12:00:00Z") == []


def test_backdated_revocation_is_rejected(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    resource = repo.add_forward_eps(
        observation(), "eps", ingested_at="2026-08-01T01:00:00Z"
    )
    approve_eps(
        repo, resource["id"], key="approved",
        available_at="2026-08-02T02:00:00Z",
        ingested_at="2026-08-02T02:00:00Z",
    )
    with pytest.raises(ValueError, match="cannot precede"):
        approve_eps(
            repo, resource["id"], key="backdated-revoke",
            decision=ApprovalStatus.REVOKED,
            available_at="2026-08-01T02:00:00Z",
            ingested_at="2026-08-03T02:00:00Z",
        )


def test_idempotency_ledger_binds_every_key_to_fingerprint(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    first = repo.add_forward_eps(observation(), "key-a")
    duplicate = repo.add_forward_eps(observation(), "key-b")

    with pytest.raises(ValueError, match="different payload"):
        repo.add_forward_eps(observation(eps_base=51.0), "key-b")

    assert first["id"] == duplicate["id"]
    with sqlite3.connect(repo.db_path) as conn:
        bindings = conn.execute(
            "SELECT idempotency_key, resource_id FROM valuation_idempotency_keys "
            "WHERE resource_type='forward_eps' ORDER BY idempotency_key"
        ).fetchall()
    assert bindings == [("key-a", first["id"]), ("key-b", first["id"])]


def test_published_at_cannot_be_later_than_available_date():
    with pytest.raises(ValueError, match="published_at cannot be later"):
        observation(
            published_at="2026-08-02",
            available_at="2026-08-01T23:00:00+08:00",
        ).validated()
