import sqlite3

import requests

from src.domain.analysis_snapshot import AnalysisSnapshot, CaptureMode
from src.domain.valuation import (
    ApprovalResourceType,
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    ValuationApproval,
)
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.services.data_freshness_service import DataFreshnessService
from src.services.production_ingestion_service import ProductionIngestionService


class Response:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.status_code = 200

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


def eps(revision=1, revision_of=None, available_at="2026-07-01T08:00:00Z"):
    return ForwardEPSObservation(
        logical_series_id="2330-2027-broker-a",
        revision_number=revision,
        revision_of=revision_of,
        symbol="2330.TW",
        fiscal_year=2027,
        eps_base=50 + revision,
        source_name="Broker A",
        source_type=ForwardEPSSourceType.BROKER_REPORT,
        published_at=available_at[:10],
        available_at=available_at,
    )


def approval(repo, resource_id, name, when, decision=ApprovalStatus.APPROVED):
    return repo.add_approval(
        ValuationApproval(
            approval_id=f"approval-{name}",
            resource_type=ApprovalResourceType.FORWARD_EPS,
            resource_id=resource_id,
            decision=decision,
            rule_id="VAL-02",
            evidence_level="A",
            project_operationalization=False,
            approved_by="test-admin",
            rationale="source reviewed",
            available_at=when,
        ),
        f"approval-key-{name}",
        ingested_at=when,
    )


def snapshot(repo, resource, approval_id, suffix="a"):
    return repo.add(
        AnalysisSnapshot(
            symbol="2330.TW",
            knowledge_cutoff_at="2026-07-02T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={"VAL-02": "2.0.0"},
            source_resource_versions=[{
                "section": "valuation",
                "resource_type": "forward_eps_revision",
                "resource_id": resource["id"],
                "logical_resource_id": resource["logical_series_id"],
                "revision_number": resource["revision_number"],
                "available_at": resource["available_at"],
                "ingested_at": resource["ingested_at"],
                "approval_ids": [approval_id],
            }],
            manual_approval_ids=[approval_id],
            output={"status": "available", "symbol": "2330.TW"},
            created_at="2026-07-02T01:00:00Z",
        ),
        f"snapshot-{suffix}",
    )


def test_unapproved_candidate_does_not_make_snapshot_stale(tmp_path):
    db_path = str(tmp_path / "freshness.db")
    repo = ForwardEPSRepository(db_path)
    first = repo.add_forward_eps(eps(), "eps-1", ingested_at="2026-07-01T08:00:00Z")
    approved = approval(repo, first["id"], "eps-1", "2026-07-01T08:01:00Z")
    stored = snapshot(AnalysisSnapshotRepository(db_path), first, approved["approval_id"])
    repo.add_forward_eps(
        eps(2, first["id"], "2026-07-03T08:00:00Z"),
        "eps-2", ingested_at="2026-07-03T08:00:00Z",
    )

    result = DataFreshnessService(db_path).snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-04T00:00:00Z"
    )
    assert result["freshness_status"] == "current"
    assert result["reasons"] == []
    assert result["checked_dependencies"][0]["candidate_awaiting_review"] is True
    assert result["historical_snapshot_validity"] == "unchanged"


def test_new_eligible_revision_is_stale_and_revoked_approval_is_blocked(tmp_path):
    db_path = str(tmp_path / "freshness.db")
    repo = ForwardEPSRepository(db_path)
    first = repo.add_forward_eps(eps(), "eps-1", ingested_at="2026-07-01T08:00:00Z")
    approved = approval(repo, first["id"], "eps-1", "2026-07-01T08:01:00Z")
    stored = snapshot(AnalysisSnapshotRepository(db_path), first, approved["approval_id"])
    second = repo.add_forward_eps(
        eps(2, first["id"], "2026-07-03T08:00:00Z"),
        "eps-2", ingested_at="2026-07-03T08:00:00Z",
    )
    approval(repo, second["id"], "eps-2", "2026-07-03T08:01:00Z")
    service = DataFreshnessService(db_path)
    stale = service.snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-04T00:00:00Z"
    )
    assert stale["freshness_status"] == "stale"
    assert stale["reasons"] == ["newer_eligible_forward_eps_revision"]

    approval(
        repo, first["id"], "eps-1-revoked", "2026-07-04T01:00:00Z",
        ApprovalStatus.REVOKED,
    )
    blocked = service.snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-05T00:00:00Z"
    )
    assert blocked["freshness_status"] == "blocked"
    assert "approval_revoked" in blocked["reasons"]
    assert AnalysisSnapshotRepository(db_path).get(stored["snapshot_id"])[
        "output_sha256"
    ] == stored["output_sha256"]


def test_unknown_dependency_and_empty_snapshot_fail_closed(tmp_path):
    db_path = str(tmp_path / "unknown.db")
    repo = AnalysisSnapshotRepository(db_path)
    stored = repo.add(
        AnalysisSnapshot(
            symbol="2330.TW",
            knowledge_cutoff_at="2026-07-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0", used_rule_versions={},
            source_resource_versions=[], manual_approval_ids=[],
            output={"status": "insufficient_data"},
            created_at="2026-07-01T01:00:00Z",
        ),
        "empty-snapshot",
    )
    result = DataFreshnessService(db_path).snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-02T00:00:00Z"
    )
    assert result["freshness_status"] == "unknown"
    assert result["reasons"] == ["snapshot_has_no_dependencies"]


def test_provider_health_reads_stored_state_and_provider_error_is_not_fresh(tmp_path):
    db_path = str(tmp_path / "health.db")
    twse = [{"Date": "115/08/11", "TradeValue": "300,000"}]
    tpex = [{"Date": "115/08/11", "TradeAmount": "20,000"}]

    def success(url, timeout):
        return Response(twse if "twse.com.tw" in url else tpex)

    service = ProductionIngestionService(db_path, success)
    service.ingest_twse_calendar(
        [{"Name": "開始交易", "Date": "1150811", "Weekday": "二", "Description": "開始交易"}],
        observed_at="2026-08-11T02:00:00Z",
    )
    service.ingest_official_turnover(
        "2026-08-11", observed_at="2026-08-11T02:01:00Z"
    )
    health = DataFreshnessService(db_path).provider_health(
        "2026-08-11T02:02:00Z", resource_id="twse.market-turnover"
    )[0]
    assert health["freshness"] == "current"
    assert health["last_success_at"] == "2026-08-11T02:01:00.000000Z"

    service.ingest_twse_calendar(
        [
            {"Name": "開始交易", "Date": "1150811", "Weekday": "二", "Description": "開始交易"},
            {"Name": "開始交易", "Date": "1150812", "Weekday": "三", "Description": "開始交易"},
        ],
        observed_at="2026-08-12T01:00:00Z",
    )
    stale = DataFreshnessService(db_path).provider_health(
        "2026-08-12T01:01:00Z", resource_id="twse.market-turnover"
    )[0]
    assert stale["freshness"] == "stale"
    assert stale["freshness_reason"] == "newer_official_session_expected"

    service.fetcher = lambda _url, timeout: Response(
        error=requests.ConnectionError("down")
    )
    service.ingest_official_turnover(
        "2026-08-12", observed_at="2026-08-12T02:01:00Z"
    )
    failed = DataFreshnessService(db_path).provider_health(
        "2026-08-12T02:02:00Z", resource_id="twse.market-turnover"
    )[0]
    assert failed["status"] == "provider_error"
    assert failed["freshness"] == "blocked"
    assert failed["last_success_at"] == "2026-08-11T02:01:00.000000Z"
    assert "ConnectionError" in failed["latest_error"]


def test_freshness_check_is_read_only_and_deterministic(tmp_path):
    db_path = str(tmp_path / "read-only.db")
    repo = AnalysisSnapshotRepository(db_path)
    stored = repo.add(
        AnalysisSnapshot(
            symbol="2330.TW", knowledge_cutoff_at="2026-07-01T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0", used_rule_versions={},
            source_resource_versions=[], manual_approval_ids=[],
            output={"status": "insufficient_data"},
            created_at="2026-07-01T01:00:00Z",
        ),
        "read-only-snapshot",
    )
    service = DataFreshnessService(db_path)
    first = service.snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-02T00:00:00Z"
    )
    second = service.snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-02T00:00:00Z"
    )
    assert first == second
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM snapshot_dependency_checks").fetchone()[0] == 0
