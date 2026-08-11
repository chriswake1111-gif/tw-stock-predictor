import sqlite3
import json

import requests

from src.domain.analysis_snapshot import (
    AnalysisSnapshot,
    CaptureMode,
    SynthesisProfileApproval,
    SynthesisProfileRevision,
    SynthesisProfileScope,
)
from src.domain.liquidity import MarketTurnoverObservation
from src.domain.data_foundation import sha256_text
from src.domain.valuation import (
    ApprovalResourceType,
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    PEScenario,
    PEScope,
    ValuationApproval,
)
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.repositories.liquidity_repository import LiquidityRepository
from src.repositories.synthesis_profile_repository import SynthesisProfileRepository
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
    return snapshot_with_dependencies(repo, [{
        "section": "valuation",
        "resource_type": "forward_eps_revision",
        "resource_id": resource["id"],
        "logical_resource_id": resource["logical_series_id"],
        "revision_number": resource["revision_number"],
        "available_at": resource["available_at"],
        "ingested_at": resource["ingested_at"],
        "approval_ids": [approval_id],
    }], [approval_id], suffix)


def snapshot_with_dependencies(repo, dependencies, approval_ids=(), suffix="a"):
    return repo.add(
        AnalysisSnapshot(
            symbol="2330.TW",
            knowledge_cutoff_at="2026-07-02T00:00:00Z",
            capture_mode=CaptureMode.HISTORICAL_RECONSTRUCTION,
            model_version="2.0.0",
            used_rule_versions={"VAL-02": "2.0.0"},
            source_resource_versions=dependencies,
            manual_approval_ids=approval_ids,
            output={"status": "available", "symbol": "2330.TW"},
            created_at="2026-07-02T01:00:00Z",
        ),
        f"snapshot-{suffix}",
    )


def turnover(revision, *, status="available", available_at):
    return MarketTurnoverObservation(
        trade_date="2026-07-01",
        twse_turnover_twd=100_000,
        tpex_turnover_twd=20_000,
        twse_source="TWSE",
        tpex_source="TPEx",
        twse_dataset="FMTQIK",
        tpex_dataset="tpex_daily_trading_index",
        available_at=available_at,
        fetched_at=available_at,
        revision=revision,
        status=status,
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
    calendar_health = DataFreshnessService(db_path).provider_health(
        "2026-08-11T02:02:00Z", resource_id="twse.trading-calendar"
    )[0]
    assert calendar_health["freshness"] == "unknown"
    assert calendar_health["freshness_reason"] == (
        "authoritative_periodic_cadence_not_proven"
    )
    unknown_coverage = DataFreshnessService(db_path).provider_health(
        "2026-08-12T00:00:00Z", resource_id="twse.market-turnover"
    )[0]
    assert unknown_coverage["freshness"] == "unknown"
    assert unknown_coverage["freshness_reason"] == (
        "official_calendar_coverage_incomplete"
    )

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


def test_new_pe_and_liquidity_revisions_produce_multiple_stale_reasons(tmp_path):
    db_path = str(tmp_path / "multiple.db")
    valuation = ForwardEPSRepository(db_path)
    liquidity = LiquidityRepository(db_path)
    pe1 = valuation.add_pe_scenario(
        PEScenario(
            logical_series_id="2330-approved-pe", revision_number=1,
            label="base", pe_value=15, rationale="reviewed scenario",
            evidence_level="U", scope=PEScope.SYMBOL, symbol="2330.TW",
            available_at="2026-07-01T08:00:00Z",
            approval_status=ApprovalStatus.DRAFT,
        ),
        "pe-1", ingested_at="2026-07-01T08:00:00Z",
    )
    pe1_approval = valuation.add_approval(
        ValuationApproval(
            approval_id="approval-pe-1",
            resource_type=ApprovalResourceType.PE_SCENARIO,
            resource_id=pe1["id"], decision=ApprovalStatus.APPROVED,
            rule_id="VAL-04", evidence_level="B",
            project_operationalization=False, approved_by="test-admin",
            rationale="reviewed scenario", available_at="2026-07-01T08:01:00Z",
        ),
        "approval-pe-1", ingested_at="2026-07-01T08:01:00Z",
    )
    turnover1 = liquidity.add_turnover(
        turnover(1, available_at="2026-07-01T09:00:00Z"),
        ingested_at="2026-07-01T09:00:00Z",
    )
    stored = snapshot_with_dependencies(
        AnalysisSnapshotRepository(db_path),
        [
            {
                "section": "valuation", "resource_type": "pe_scenario_revision",
                "resource_id": pe1["id"], "logical_resource_id": pe1["logical_series_id"],
                "revision_number": 1, "available_at": pe1["available_at"],
                "ingested_at": pe1["ingested_at"],
                "approval_ids": [pe1_approval["approval_id"]],
            },
            {
                "section": "liquidity", "resource_type": "market_turnover_revision",
                "resource_id": turnover1["id"], "logical_resource_id": turnover1["trade_date"],
                "revision_number": 1, "available_at": turnover1["available_at"],
                "ingested_at": turnover1["ingested_at"], "approval_ids": [],
            },
        ],
        [pe1_approval["approval_id"]], "multiple",
    )
    pe2 = valuation.add_pe_scenario(
        PEScenario(
            logical_series_id="2330-approved-pe", revision_number=2,
            revision_of=pe1["id"], label="base", pe_value=16,
            rationale="reviewed revision", evidence_level="U",
            scope=PEScope.SYMBOL, symbol="2330.TW",
            available_at="2026-07-03T08:00:00Z",
            approval_status=ApprovalStatus.DRAFT,
        ),
        "pe-2", ingested_at="2026-07-03T08:00:00Z",
    )
    valuation.add_approval(
        ValuationApproval(
            approval_id="approval-pe-2",
            resource_type=ApprovalResourceType.PE_SCENARIO,
            resource_id=pe2["id"], decision=ApprovalStatus.APPROVED,
            rule_id="VAL-04", evidence_level="B",
            project_operationalization=False, approved_by="test-admin",
            rationale="reviewed revision", available_at="2026-07-03T08:01:00Z",
        ),
        "approval-pe-2", ingested_at="2026-07-03T08:01:00Z",
    )
    liquidity.add_turnover(
        turnover(2, available_at="2026-07-03T09:00:00Z"),
        ingested_at="2026-07-03T09:00:00Z",
    )
    result = DataFreshnessService(db_path).snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-04T00:00:00Z"
    )
    assert result["freshness_status"] == "stale"
    assert result["reasons"] == [
        "newer_eligible_market_turnover_revision",
        "newer_eligible_pe_scenario_revision",
    ]


def test_latest_revoked_liquidity_revision_does_not_resurrect_old_value(tmp_path):
    db_path = str(tmp_path / "turnover-revoked.db")
    liquidity = LiquidityRepository(db_path)
    first = liquidity.add_turnover(
        turnover(1, available_at="2026-07-01T09:00:00Z"),
        ingested_at="2026-07-01T09:00:00Z",
    )
    stored = snapshot_with_dependencies(
        AnalysisSnapshotRepository(db_path),
        [{
            "section": "liquidity", "resource_type": "market_turnover_revision",
            "resource_id": first["id"], "logical_resource_id": first["trade_date"],
            "revision_number": 1, "available_at": first["available_at"],
            "ingested_at": first["ingested_at"], "approval_ids": [],
        }],
        suffix="turnover-revoked",
    )
    liquidity.add_turnover(
        turnover(2, status="revoked", available_at="2026-07-03T09:00:00Z"),
        ingested_at="2026-07-03T09:00:00Z",
    )
    result = DataFreshnessService(db_path).snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-04T00:00:00Z"
    )
    assert result["freshness_status"] == "blocked"
    assert result["reasons"] == ["dependency_revoked"]


def test_approved_synthesis_profile_supersession_makes_snapshot_stale(tmp_path):
    db_path = str(tmp_path / "profile-stale.db")
    profiles = SynthesisProfileRepository(db_path)

    def profile(revision, available_at, revision_of=None):
        return SynthesisProfileRevision(
            logical_profile_id="default-target-profile",
            revision_number=revision,
            revision_of=revision_of,
            scope=SynthesisProfileScope.GLOBAL,
            allowed_method_families=("VAL-01", "FB-03"),
            overlap_tolerance="0.05",
            evidence_strength_policy=(
                {"minimum_independent_target_components": 2, "label": "moderate"},
            ),
            available_at=available_at,
            created_by="test-admin",
            rationale="reviewed target synthesis profile",
        )

    def approve(resource_id, suffix, approved_at):
        return profiles.add_approval(
            SynthesisProfileApproval(
                approval_id=f"profile-approval-{suffix}",
                profile_revision_id=resource_id,
                decision=ApprovalStatus.APPROVED,
                rule_id="TGT-01", rule_version="2.0.0", evidence_level="C",
                implementation_mode="project_operationalization",
                project_operationalization=True, approved_by="test-admin",
                rationale="reviewed TGT-01 profile", approved_at=approved_at,
            ),
            f"profile-approval-{suffix}", ingested_at=approved_at,
        )

    first = profiles.add_revision(
        profile(1, "2026-07-01T08:00:00Z"),
        "profile-1", ingested_at="2026-07-01T08:00:00Z",
    )
    first_approval = approve(first["id"], "1", "2026-07-01T08:01:00Z")
    stored = snapshot_with_dependencies(
        AnalysisSnapshotRepository(db_path),
        [{
            "section": "target_synthesis",
            "resource_type": "synthesis_profile_revision",
            "resource_id": first["id"],
            "logical_resource_id": first["logical_profile_id"],
            "revision_number": 1,
            "available_at": first["available_at"],
            "ingested_at": first["ingested_at"],
            "approval_ids": [first_approval["approval_id"]],
        }],
        [first_approval["approval_id"]], "profile",
    )
    second = profiles.add_revision(
        profile(2, "2026-07-03T08:00:00Z", first["id"]),
        "profile-2", ingested_at="2026-07-03T08:00:00Z",
    )
    approve(second["id"], "2", "2026-07-03T08:01:00Z")
    result = DataFreshnessService(db_path).snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-07-04T00:00:00Z"
    )
    assert result["freshness_status"] == "stale"
    assert result["reasons"] == ["newer_eligible_synthesis_profile_revision"]


def test_monthly_publication_without_proven_cadence_is_unknown(tmp_path):
    db_path = str(tmp_path / "cbc-freshness.db")
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    evidence = {
        "official_release_at": "2026-06-25T16:00:00+08:00",
        "source_reference": "https://example.test/cbc/official-release",
        "source_identity": "CBC official publication notice",
        "evidence_file_sha256": sha256_text("cbc official evidence"),
        "captured_at": "2026-06-25T16:05:00+08:00",
        "verification_mode": "manual_official_source_review",
        "verified_by": "internal.researcher",
        "status": "accepted",
    }
    ingestion_result = ProductionIngestionService(db_path).ingest_cbc_m1b(
        payload, {"2026-05": evidence},
        observed_at="2026-08-11T02:00:00Z",
    )
    health = DataFreshnessService(db_path).provider_health(
        "2026-08-11T03:00:00Z", resource_id="cbc.m1b"
    )[0]
    assert health["freshness"] == "unknown"
    assert health["freshness_reason"] == "publication_cadence_not_proven"
    m1b = ingestion_result["records"][0]
    stored = snapshot_with_dependencies(
        AnalysisSnapshotRepository(db_path),
        [{
            "section": "liquidity", "resource_type": "m1b_revision",
            "resource_id": m1b["id"], "logical_resource_id": m1b["period"],
            "revision_number": m1b["revision"],
            "available_at": m1b["available_at"],
            "ingested_at": m1b["ingested_at"], "approval_ids": [],
        }],
        suffix="cbc-unknown",
    )
    snapshot_state = DataFreshnessService(db_path).snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-08-11T03:00:00Z"
    )
    assert snapshot_state["freshness_status"] == "unknown"
    assert snapshot_state["reasons"] == ["dependency_freshness_unknown"]


def test_provider_failure_blocks_prior_turnover_snapshot_without_mutation(tmp_path):
    db_path = str(tmp_path / "provider-blocked.db")
    twse_10 = [{"Date": "115/08/10", "TradeValue": "300,000"}]
    tpex_10 = [{"Date": "115/08/10", "TradeAmount": "20,000"}]

    def success(url, timeout):
        return Response(twse_10 if "twse.com.tw" in url else tpex_10)

    ingestion = ProductionIngestionService(db_path, success)
    ingestion.ingest_twse_calendar(
        [{"Name": "開始交易", "Date": "1150810", "Weekday": "一", "Description": "開始交易"}],
        observed_at="2026-08-10T01:00:00Z",
    )
    turnover_result = ingestion.ingest_official_turnover(
        "2026-08-10", observed_at="2026-08-10T02:00:00Z"
    )
    turnover_row = turnover_result["turnover"]
    stored = snapshot_with_dependencies(
        AnalysisSnapshotRepository(db_path),
        [{
            "section": "liquidity",
            "resource_type": "market_turnover_revision",
            "resource_id": turnover_row["id"],
            "logical_resource_id": turnover_row["trade_date"],
            "revision_number": turnover_row["revision"],
            "available_at": turnover_row["available_at"],
            "ingested_at": turnover_row["ingested_at"],
            "approval_ids": [],
        }],
        suffix="provider-blocked",
    )
    original_hash = stored["output_sha256"]
    ingestion.ingest_twse_calendar(
        [
            {"Name": "開始交易", "Date": "1150810", "Weekday": "一", "Description": "開始交易"},
            {"Name": "開始交易", "Date": "1150811", "Weekday": "二", "Description": "開始交易"},
        ],
        observed_at="2026-08-11T01:00:00Z",
    )
    ingestion.fetcher = lambda _url, timeout: Response(
        error=requests.ConnectionError("provider down")
    )
    assert ingestion.ingest_official_turnover(
        "2026-08-11", observed_at="2026-08-11T02:00:00Z"
    )["status"] == "failed"
    result = DataFreshnessService(db_path).snapshot_dependency_freshness(
        stored["snapshot_id"], "2026-08-11T03:00:00Z"
    )
    assert result["freshness_status"] == "blocked"
    assert result["reasons"] == ["dependency_provider_error"]
    assert result["historical_snapshot_validity"] == "unchanged"
    assert AnalysisSnapshotRepository(db_path).get(stored["snapshot_id"])[
        "output_sha256"
    ] == original_hash
