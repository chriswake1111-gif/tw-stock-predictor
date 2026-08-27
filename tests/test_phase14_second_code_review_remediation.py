import concurrent.futures
import logging
import sqlite3
from pathlib import Path

import pytest

from src.domain.eod_close import classify_product
from src.collectors.twse_isin_classification_collector import (
    parse_twse_isin_classification,
)
from src.repositories.eod_close_repository import EodCloseRepository, EodEvidenceConflict
from src.repositories.migration_runner import apply_valuation_migration
from src.services.eod_close_service import EodCloseService
from src.services.eod_close_ingestion_service import EodCloseIngestionService
from tests.test_phase14_eod_repository_service import (
    _classification,
    _db_with_identity,
    _observation,
    _raw,
    _source,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _price_payload(*, close: str = "1005.00", volume: str = "123456") -> list[dict[str, str]]:
    return [{
        "Date": "115/08/27",
        "Code": "2330",
        "Name": "台積電",
        "TradeVolume": volume,
        "TradeValue": "123456789000",
        "OpeningPrice": "1000.00",
        "HighestPrice": "1010.00",
        "LowestPrice": "990.00",
        "ClosingPrice": close,
        "Change": "5.00",
        "Transaction": "123456",
    }]


def test_exact_non_stock_classifier_values_and_preferred_evidence() -> None:
    for security_type in ("認購權證", "認售權證", "臺灣存託憑證"):
        result = classify_product(
            official_code="700001",
            requested_code="700001",
            market="上市",
            expected_market="上市",
            security_type=security_type,
        )
        assert result["product_scope"] == "not_applicable"
        assert result["reason_codes"] == ["unsupported_security_type"]

    for kwargs in (
        {"cfi": "EPNRAR"},
        {"remarks": "特別股"},
        {"remarks": "Preferred Stocks"},
    ):
        result = classify_product(
            official_code="8349A",
            requested_code="8349A",
            market="上櫃",
            expected_market="上櫃",
            security_type="股票",
            **kwargs,
        )
        assert result["product_scope"] == "not_applicable"
        assert result["reason_codes"] == ["unsupported_security_type"]


def test_command_reservation_state_machine_rejects_direct_completion(tmp_path) -> None:
    db = tmp_path / "reservation-state.sqlite"
    apply_valuation_migration(str(db))
    repo = EodCloseRepository(str(db))
    repo.reserve_ingestion_command(
        idempotency_key="state-machine-key",
        payload_fingerprint="a" * 64,
        resource_id="twse.eod.stock_day_all",
        actor_id="test-operator",
        command_received_at="2026-08-27T05:00:00Z",
        source_published_at=None,
    )
    with pytest.raises(sqlite3.IntegrityError, match="invalid EOD ingestion command"):
        with repo.write_transaction() as conn:
            conn.execute(
                "UPDATE eod_ingestion_command_reservations SET status='completed', result_json='{}' "
                "WHERE idempotency_key='state-machine-key'"
            )
    assert repo.ingestion_command_reservation("state-machine-key")["status"] == "reserved"


def test_public_context_reapplies_preferred_cfi_classifier_evidence(tmp_path) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    classification = _classification(
        repo,
        db,
        at="2026-08-27T04:00:00Z",
        cfi_raw="EPNRAR",
    )
    raw, raw_hash = _raw(
        db,
        "twse.eod.stock_day_all",
        "twse-universe-official",
        "preferred-public-context",
        "2026-08-27T05:00:00Z",
    )
    source = _source(repo, raw, raw_hash, date="2026-08-27", at="2026-08-27T05:00:00Z")
    _observation(
        repo,
        raw=raw,
        raw_hash=raw_hash,
        source=source,
        classification=classification,
        anchor=anchor,
        at="2026-08-27T05:02:00Z",
    )

    result = EodCloseService(str(db)).as_of(
        "2330.TW", knowledge_cutoff_at="2026-08-28T00:00:00Z"
    )
    assert result["product_scope"] == "not_applicable"
    assert result["status"] == "not_applicable"
    assert result["close_value"] is None
    assert "unsupported_security_type" in result["reason_codes"]


def test_classification_command_reuses_durable_reservation_and_lineage(tmp_path) -> None:
    db = tmp_path / "classification-ingestion.sqlite"
    apply_valuation_migration(str(db))
    html = (FIXTURES / "eod_isin_supported.html").read_text(encoding="utf-8")
    service = EodCloseIngestionService(str(db), enabled=True)

    first = service.ingest_classification_html(
        "2330",
        html,
        venue="TWSE",
        trade_date="2026-08-27",
        received_at="2026-08-27T05:00:00Z",
        idempotency_key="classification-command-key",
    )
    second = service.ingest_classification_html(
        "2330",
        html,
        venue="TWSE",
        trade_date="2026-08-27",
        received_at="2026-08-27T06:00:00Z",
        idempotency_key="classification-command-key",
    )

    assert first["classification_decision"] == "supported_stock"
    assert second["created"] is False
    assert second["classification_evidence_id"] == first["classification_evidence_id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_resource_revisions WHERE resource_id=?", ("twse.isin.security_classification",)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM eod_product_classification_evidence").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM eod_ingestion_command_reservations").fetchone()[0] == "completed"
        assert conn.execute("SELECT COUNT(*) FROM ingestion_runs WHERE status='succeeded'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ingestion_resource_locks").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("fixture_name", "code", "expected_market", "expected_decision"),
    [
        ("eod_isin_warrant_exact.html", "700001", "上市", "not_applicable"),
        ("eod_isin_put_warrant_exact.html", "700002", "上市", "not_applicable"),
        ("eod_isin_tdr_exact.html", "910001", "上市", "not_applicable"),
        ("eod_isin_preferred_cfi.html", "8349A", "上櫃", "not_applicable"),
        ("eod_isin_preferred_remarks.html", "8349B", "上櫃", "not_applicable"),
    ],
)
def test_classifier_fixed_fixtures_use_exact_source_evidence(
    fixture_name: str, code: str, expected_market: str, expected_decision: str
) -> None:
    parsed = parse_twse_isin_classification(
        (FIXTURES / fixture_name).read_text(encoding="utf-8"),
        official_code=code,
        expected_market=expected_market,
        trade_date="2026-08-27",
    )
    assert parsed.state == "accepted"
    assert parsed.decision == expected_decision


@pytest.mark.parametrize(
    ("scenario", "day", "close", "volume", "product_scope", "observation_status", "eligibility", "classification_kind"),
    [
        ("ordinary", 10, "1005", "123456", "supported_stock", "available", "eligible", "accepted_stock"),
        ("zero_volume", 11, "1005", "0", "supported_stock", "insufficient_data", "ineligible", "accepted_stock"),
        ("unsupported_product", 12, "1005", "123456", "not_applicable", "not_applicable", "ineligible", "unsupported"),
        ("missing_classifier", 13, "1005", "123456", "needs_human_input", "needs_human_input", "awaiting_review", None),
        ("blocked_classifier", 14, "1005", "123456", "needs_human_input", "needs_human_input", "awaiting_review", "blocked"),
        ("close_unusable", 15, "---", "123456", "needs_human_input", "insufficient_data", "ineligible", "accepted_stock"),
        ("volume_unusable", 16, "1005", "---", "needs_human_input", "insufficient_data", "ineligible", "accepted_stock"),
    ],
)
def test_source_observed_is_independent_from_public_outcome(
    tmp_path,
    scenario: str,
    day: int,
    close: str,
    volume: str,
    product_scope: str,
    observation_status: str,
    eligibility: str,
    classification_kind: str | None,
) -> None:
    db, anchor = _db_with_identity(tmp_path)
    repo = EodCloseRepository(str(db))
    date = f"2026-08-{day:02d}"
    raw, raw_hash = _raw(
        db,
        "twse.eod.stock_day_all",
        "twse-universe-official",
        f"source-observed-{scenario}",
        f"{date}T05:00:00Z",
    )
    source = _source(repo, raw, raw_hash, date=date, at=f"{date}T05:00:00Z")
    classification = None
    if classification_kind == "accepted_stock":
        classification = _classification(repo, db, at=f"{date}T04:00:00Z")
    elif classification_kind == "unsupported":
        classification = _classification(
            repo,
            db,
            at=f"{date}T04:00:00Z",
            security_type_raw="認購權證",
            classification_decision="not_applicable",
        )
    elif classification_kind == "blocked":
        classification = _classification(
            repo, db, at=f"{date}T04:00:00Z", state="blocked"
        )
    observation = _observation(
        repo,
        raw=raw,
        raw_hash=raw_hash,
        source=source,
        classification=classification,
        anchor=anchor,
        at=f"{date}T05:02:00Z",
        close=close,
        volume=volume,
        product_scope=product_scope,
        observation_status=observation_status,
        public_eligibility_status=eligibility,
    )

    with repo.read_transaction() as conn:
        row = conn.execute(
            """SELECT source_observation_state, observation_status,
                      public_eligibility_status, close_value
               FROM eod_close_observations WHERE close_observation_id=?""",
            (observation["close_observation_id"],),
        ).fetchone()
    assert row[0] == "source_observed"
    assert row[1:] == (observation_status, eligibility, close)


def _assert_failure_rolls_back_and_retry_reconstructs_one_lineage(
    tmp_path, failure_point: str
) -> None:
    db = tmp_path / f"failure-{failure_point}.sqlite"
    apply_valuation_migration(str(db))
    payload = _price_payload()

    def inject(point: str) -> None:
        if point == failure_point:
            raise RuntimeError(f"injected:{point}")

    failing = EodCloseIngestionService(
        str(db), enabled=True, failure_injector=inject
    )
    with pytest.raises(RuntimeError, match="injected"):
        failing.ingest_price_payload(
            "TWSE",
            payload,
            received_at="2026-08-27T05:00:00Z",
            idempotency_key=f"failure-{failure_point}",
        )

    with failing.repository.read_transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_resource_revisions WHERE resource_id=?", ("twse.eod.stock_day_all",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM eod_close_source_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM eod_close_observations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM eod_ingestion_idempotency").fetchone()[0] == 0
        reservation = conn.execute(
            "SELECT status, last_error FROM eod_ingestion_command_reservations WHERE idempotency_key=?",
            (f"failure-{failure_point}",),
        ).fetchone()
        assert reservation[0] == "reserved"
        assert reservation[1].startswith("operator_command_failed:RuntimeError")
        assert [row["status"] for row in conn.execute("SELECT status FROM ingestion_runs").fetchall()] == ["failed"]
        assert conn.execute("SELECT COUNT(*) FROM ingestion_resource_locks").fetchone()[0] == 0

    retried = EodCloseIngestionService(str(db), enabled=True).ingest_price_payload(
        "TWSE",
        payload,
        received_at="2026-08-27T06:00:00Z",
        idempotency_key=f"failure-{failure_point}",
    )
    completed_retry = EodCloseIngestionService(str(db), enabled=True).ingest_price_payload(
        "TWSE",
        payload,
        received_at="2026-08-27T07:00:00Z",
        idempotency_key=f"failure-{failure_point}",
    )
    assert retried["created"] is True
    assert completed_retry["created"] is False
    assert completed_retry["raw_resource_revision_id"] == retried["raw_resource_revision_id"]
    assert completed_retry["source_snapshot_id"] == retried["source_snapshot_id"]
    assert completed_retry["observation_ids"] == retried["observation_ids"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM eod_close_observations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM eod_ingestion_idempotency").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM eod_ingestion_command_reservations").fetchone()[0] == "completed"
        assert conn.execute("SELECT COUNT(*) FROM ingestion_runs WHERE status='succeeded'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ingestion_resource_locks").fetchone()[0] == 0


@pytest.mark.parametrize("failure_point", ["after_raw_revision", "after_source_snapshot", "after_observations"])
def test_failure_injection_points_are_transactional(tmp_path, failure_point: str) -> None:
    _assert_failure_rolls_back_and_retry_reconstructs_one_lineage(
        tmp_path, failure_point
    )


def test_same_key_concurrent_commands_converge_and_keep_governance_evidence(tmp_path, caplog) -> None:
    db = tmp_path / "concurrent.sqlite"
    apply_valuation_migration(str(db))
    payload = _price_payload()
    services = [EodCloseIngestionService(str(db), enabled=True) for _ in range(2)]
    caplog.set_level(logging.INFO, logger="tw_stock_predictor.universe_audit")

    def run(service: EodCloseIngestionService) -> dict:
        return service.ingest_price_payload(
            "TWSE",
            payload,
            received_at="2026-08-27T05:00:00Z",
            idempotency_key="concurrent-same-key",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, services))

    assert {result["created"] for result in results} == {True, False}
    assert results[0]["observation_ids"] == results[1]["observation_ids"]
    assert all(result["governance"]["run_id"] == results[0]["governance"]["run_id"] for result in results)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM eod_ingestion_command_reservations").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM eod_ingestion_command_reservations").fetchone()[0] == "completed"
        assert conn.execute("SELECT COUNT(*) FROM ingestion_runs WHERE status='succeeded'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ingestion_run_items WHERE ingestion_run_id=?", (results[0]["governance"]["run_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ingestion_resource_locks").fetchone()[0] == 0
    messages = [record.getMessage() for record in caplog.records]
    assert any("'outcome': 'started'" in message for message in messages)
    assert any("'outcome': 'succeeded'" in message for message in messages)


def test_same_key_different_payload_fails_closed_without_new_evidence(tmp_path) -> None:
    db = tmp_path / "key-reuse.sqlite"
    apply_valuation_migration(str(db))
    service = EodCloseIngestionService(str(db), enabled=True)
    service.ingest_price_payload(
        "TWSE", _price_payload(), received_at="2026-08-27T05:00:00Z",
        idempotency_key="same-key-different-payload",
    )
    with pytest.raises(EodEvidenceConflict, match="idempotency_key_reused"):
        service.ingest_price_payload(
            "TWSE", _price_payload(close="1006.00"), received_at="2026-08-27T06:00:00Z",
            idempotency_key="same-key-different-payload",
        )
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM eod_close_observations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM eod_ingestion_idempotency").fetchone()[0] == 1
