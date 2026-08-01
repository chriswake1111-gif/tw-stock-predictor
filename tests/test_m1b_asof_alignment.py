import sqlite3
import json

from src.collectors.cbc_collector import CBCCollector
from src.domain.liquidity import M1BMonthlyObservation
from src.repositories.liquidity_repository import LiquidityRepository


def m1b(period, value, available_at, revision=1, status="available"):
    return M1BMonthlyObservation(
        period=period,
        value_raw=value,
        raw_unit="TWD_million",
        data_date=f"{period}-28",
        available_at=available_at,
        fetched_at="2026-08-01T02:00:00Z",
        source="CBC",
        source_dataset="CBC EF15M01",
        revision=revision,
        status=status,
    )


def test_m1b_unit_revision_and_knowledge_cutoff(tmp_path):
    repo = LiquidityRepository(str(tmp_path / "nested" / "liquidity.db"))
    repo.add_m1b(m1b("2026-05", 30000, "2026-06-25T08:00:00Z"),
                 ingested_at="2026-06-25T08:01:00Z")
    repo.add_m1b(m1b("2026-05", 31000, "2026-07-01T08:00:00Z", revision=2),
                 ingested_at="2026-07-01T08:01:00Z")
    before = repo.latest_m1b_as_of("2026-06-30T23:59:59Z")
    after = repo.latest_m1b_as_of("2026-07-02T00:00:00Z")
    assert before["value_twd"] == 30_000_000_000
    assert before["revision"] == 1
    assert after["value_twd"] == 31_000_000_000
    assert after["revision"] == 2
    with sqlite3.connect(tmp_path / "nested" / "liquidity.db") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version_id LIKE '%liquidity'"
        ).fetchone()[0] == 1


def test_revoked_latest_revision_does_not_fall_back(tmp_path):
    repo = LiquidityRepository(str(tmp_path / "liquidity.db"))
    repo.add_m1b(m1b("2026-05", 30000, "2026-06-25T08:00:00Z"),
                 ingested_at="2026-06-25T08:01:00Z")
    repo.add_m1b(m1b("2026-05", 30000, "2026-07-01T08:00:00Z", 2, "revoked"),
                 ingested_at="2026-07-01T08:01:00Z")
    assert repo.latest_m1b_as_of("2026-07-02T00:00:00Z") is None


def test_cbc_parser_requires_explicit_official_release_timestamp():
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    missing = CBCCollector.parse_official_m1b(payload, {}, "2026-08-01T00:00:00Z")
    assert missing["status"] == "needs_human_input"
    parsed = CBCCollector.parse_official_m1b(
        payload, {"2026-05": "2026-06-25T16:00:00+08:00"},
        "2026-08-01T00:00:00Z",
    )
    record = parsed["observations"][0].canonical_payload()
    assert record["raw_unit"] == "TWD_million"
    assert record["value_twd"] == 30_000_000_000
    assert record["available_at"] == "2026-06-25T08:00:00.000000Z"
