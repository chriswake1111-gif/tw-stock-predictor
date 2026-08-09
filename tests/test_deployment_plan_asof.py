import sqlite3

import pytest

from src.domain.deployment import (
    DeploymentPlanRevision, DeploymentTrigger, TriggerType,
)
from src.domain.valuation import ApprovalStatus
from src.domain.technical_anchor import (
    AnchorPoint, AnchorRevisionStatus, AnchorRole, ManualAnchorSetRevision,
)
from src.repositories.deployment_plan_repository import DeploymentPlanRepository
from src.services.deployment_plan_service import DeploymentPlanService
from src.services.technical_scenario_service import TechnicalScenarioService


def complete_triggers():
    return tuple(DeploymentTrigger(stage=stage, trigger_type=TriggerType.MANUAL_PRICE,
                                   value=str(101 - stage)) for stage in (1, 2, 3))


def revision(number=1, revision_of=None, *, capital="900000", triggers=None):
    return DeploymentPlanRevision(
        logical_campaign_id="2330-campaign-a", revision_number=number,
        revision_of=revision_of, symbol="2330", planned_total_capital=capital,
        triggers=complete_triggers() if triggers is None else triggers,
        available_at=f"2026-03-0{number}T01:00:00Z", created_by="reviewer",
    )


def fb04_revision(number=1, revision_of=None, *, symbol="2330",
                  status=AnchorRevisionStatus.AVAILABLE):
    return ManualAnchorSetRevision(
        logical_anchor_set_id=f"{symbol}-fb04", revision_number=number,
        revision_of=revision_of, symbol=symbol, evidence_basis_rule_id="FB-04",
        anchors=(
            AnchorPoint(AnchorRole.ORIGIN, 100, "2026-01-01"),
            AnchorPoint(AnchorRole.SWING_END, 150 + number, f"2026-02-0{number}"),
        ), available_at=f"2026-03-0{number}T01:00:00Z", created_by="reviewer",
        source="manual_research", status=status,
    )


def fb04_triggers(anchor_id, approval_id, reference_id="scenario-fb04-2330"):
    return (
        DeploymentTrigger(1, TriggerType.MANUAL_PRICE, value="140"),
        DeploymentTrigger(2, TriggerType.USER_PERCENTAGE, value="0.05"),
        DeploymentTrigger(
            3, TriggerType.APPROVED_FB04_SCENARIO, reference_id=reference_id,
            anchor_revision_id=anchor_id, approval_id=approval_id, rule_id="FB-04",
        ),
    )


def deployment_for_fb04(symbol, campaign_id, triggers, available_at="2026-03-04T01:00:00Z"):
    return DeploymentPlanRevision(
        logical_campaign_id=campaign_id, revision_number=1, symbol=symbol,
        planned_total_capital="900000", triggers=triggers,
        available_at=available_at, created_by="reviewer",
    )


def test_migration_is_additive_and_rerunnable_with_missing_parent(tmp_path):
    db = tmp_path / "missing" / "nested" / "phase5.db"
    first = DeploymentPlanRepository(str(db))
    DeploymentPlanRepository(str(db))
    assert first.db_path == str(db)
    with sqlite3.connect(db) as conn:
        versions = {row[0] for row in conn.execute("SELECT version_id FROM schema_migrations")}
    assert "20260809_06_evidence_model_v2_deployment" in versions


def test_revision_needs_new_approval_and_asof_reconstructs_prior_state(tmp_path, monkeypatch):
    now = ["2026-03-01T01:30:00.000000Z"]
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: now[0])
    service = DeploymentPlanService(str(tmp_path / "db.sqlite"))
    first = service.ingest(revision(), "plan-1")
    now[0] = "2026-03-01T02:30:00.000000Z"
    service.record_approval(
        plan_revision_id=first["id"], decision=ApprovalStatus.APPROVED,
        rationale="complete reviewed trigger set", approved_at="2026-03-01T02:00:00Z",
        approved_by="reviewer", idempotency_key="approve-1",
    )
    now[0] = "2026-03-02T01:30:00.000000Z"
    second = service.ingest(revision(2, first["id"], capital="1200000"), "plan-2")

    before = service.analyze("2330", "2026-03-01T03:00:00Z")
    after = service.analyze("2330", "2026-03-02T03:00:00Z")
    assert before["status"] == "available"
    assert before["plans"][0]["plan_revision_id"] == first["id"]
    assert after["status"] == "needs_human_input"
    assert after["reason"] == "approved_deployment_plan_required"
    assert after["plans"][0]["plan_revision_id"] == second["id"]


def test_approval_after_cutoff_is_invisible(tmp_path, monkeypatch):
    now = ["2026-03-01T01:30:00.000000Z"]
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: now[0])
    service = DeploymentPlanService(str(tmp_path / "db.sqlite"))
    plan = service.ingest(revision(), "plan")
    now[0] = "2026-03-05T03:00:00.000000Z"
    service.record_approval(
        plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
        rationale="reviewed", approved_at="2026-03-05T02:00:00Z",
        approved_by="reviewer", idempotency_key="approval",
    )
    before = service.analyze("2330", "2026-03-04T23:59:00Z")
    after = service.analyze("2330", "2026-03-11T00:00:00Z")
    assert before["reason"] == "approved_deployment_plan_required"
    assert after["status"] == "available"


def test_incomplete_plan_cannot_be_approved(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    service = DeploymentPlanService(str(tmp_path / "db.sqlite"))
    plan = service.ingest(revision(triggers=complete_triggers()[:2]), "draft")
    with pytest.raises(ValueError, match="incomplete"):
        service.record_approval(
            plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
            rationale="not complete", approved_at="2026-03-01T02:00:00Z",
            approved_by="reviewer", idempotency_key="approve",
        )


def test_revocation_and_backdated_event_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    service = DeploymentPlanService(str(tmp_path / "db.sqlite"))
    plan = service.ingest(revision(), "plan")
    service.record_approval(
        plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
        rationale="reviewed", approved_at="2026-03-01T02:00:00Z",
        approved_by="reviewer", idempotency_key="approve",
    )
    service.record_approval(
        plan_revision_id=plan["id"], decision=ApprovalStatus.REVOKED,
        rationale="withdrawn", approved_at="2026-03-01T03:00:00Z",
        approved_by="reviewer", idempotency_key="revoke",
    )
    assert service.analyze("2330", "2026-03-11T04:00:00Z")["reason"] == "deployment_plan_approval_revoked"
    with pytest.raises(ValueError, match="backdated"):
        service.record_approval(
            plan_revision_id=plan["id"], decision=ApprovalStatus.REVOKED,
            rationale="backdated", approved_at="2026-03-01T02:30:00Z",
            approved_by="reviewer", idempotency_key="backdated",
        )


def test_idempotency_key_is_permanently_bound(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    repo = DeploymentPlanRepository(str(tmp_path / "db.sqlite"))
    a = repo.add_revision(revision(), "key-a")
    b = repo.add_revision(revision(), "key-b")
    assert a["id"] == b["id"]
    with pytest.raises(ValueError, match="different payload"):
        repo.add_revision(revision(capital="1000000"), "key-b")


def test_revision_cannot_cross_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    repo = DeploymentPlanRepository(str(tmp_path / "db.sqlite"))
    first = repo.add_revision(revision(), "first")
    changed = DeploymentPlanRevision(
        logical_campaign_id="2330-campaign-a", revision_number=2, revision_of=first["id"],
        symbol="2317", planned_total_capital="900000", triggers=complete_triggers(),
        available_at="2026-03-02T01:00:00Z", created_by="reviewer",
    )
    with pytest.raises(ValueError, match="cannot change symbol"):
        repo.add_revision(changed, "changed")


def test_fb04_trigger_requires_exact_effective_phase4_approval(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    monkeypatch.setattr("src.repositories.technical_anchor_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    db = str(tmp_path / "db.sqlite")
    technical = TechnicalScenarioService(db)
    anchor = technical.ingest(fb04_revision(), "anchor")
    anchor_approval = technical.record_approval(
        anchor_revision_id=anchor["id"], decision=ApprovalStatus.APPROVED,
        rule_id="FB-04", rationale="reviewed retracement anchors",
        approved_at="2026-03-01T02:00:00Z", approved_by="reviewer",
        idempotency_key="anchor-approval",
    )
    deployment = DeploymentPlanService(db)
    triggers = fb04_triggers(anchor["id"], anchor_approval["approval_id"])
    plan = deployment.ingest(revision(triggers=triggers), "plan")
    approved = deployment.record_approval(
        plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
        rationale="three triggers and capital context reviewed",
        approved_at="2026-03-02T02:00:00Z", approved_by="reviewer",
        idempotency_key="plan-approval",
    )
    assert approved["decision"] == "approved"

    fake_trigger = triggers[:-1] + (DeploymentTrigger(
        3, TriggerType.APPROVED_FB04_SCENARIO, reference_id="scenario-fake",
        anchor_revision_id=anchor["id"], approval_id="fake", rule_id="FB-04",
    ),)
    second = DeploymentPlanRevision(
        logical_campaign_id="2330-campaign-b", revision_number=1, symbol="2330",
        planned_total_capital="900000", triggers=fake_trigger,
        available_at="2026-03-02T01:00:00Z", created_by="reviewer",
    )
    fake_plan = deployment.ingest(second, "fake-plan")
    with pytest.raises(ValueError, match="effective approved"):
        deployment.record_approval(
            plan_revision_id=fake_plan["id"], decision=ApprovalStatus.APPROVED,
            rationale="should fail", approved_at="2026-03-02T03:00:00Z",
            approved_by="reviewer", idempotency_key="fake-approval",
        )


def test_fb04_trigger_rejects_cross_symbol_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    monkeypatch.setattr("src.repositories.technical_anchor_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    db = str(tmp_path / "db.sqlite")
    technical = TechnicalScenarioService(db)
    anchor = technical.ingest(fb04_revision(), "anchor")
    approval = technical.record_approval(
        anchor_revision_id=anchor["id"], decision=ApprovalStatus.APPROVED,
        rule_id="FB-04", rationale="reviewed", approved_at="2026-03-01T02:00:00Z",
        approved_by="reviewer", idempotency_key="anchor-approval",
    )
    deployment = DeploymentPlanService(db)
    plan = deployment.ingest(deployment_for_fb04(
        "2317", "2317-campaign", fb04_triggers(anchor["id"], approval["approval_id"])
    ), "plan")
    with pytest.raises(ValueError, match="symbol must match"):
        deployment.record_approval(
            plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
            rationale="must reject", approved_at="2026-03-05T02:00:00Z",
            approved_by="reviewer", idempotency_key="plan-approval",
        )


def test_fb04_trigger_rejects_superseded_anchor_revision(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    monkeypatch.setattr("src.repositories.technical_anchor_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    db = str(tmp_path / "db.sqlite")
    technical = TechnicalScenarioService(db)
    first = technical.ingest(fb04_revision(), "anchor-1")
    first_approval = technical.record_approval(
        anchor_revision_id=first["id"], decision=ApprovalStatus.APPROVED,
        rule_id="FB-04", rationale="reviewed", approved_at="2026-03-01T02:00:00Z",
        approved_by="reviewer", idempotency_key="approval-1",
    )
    technical.ingest(fb04_revision(2, first["id"]), "anchor-2")
    deployment = DeploymentPlanService(db)
    plan = deployment.ingest(deployment_for_fb04(
        "2330", "superseded-campaign",
        fb04_triggers(first["id"], first_approval["approval_id"]),
    ), "plan")
    with pytest.raises(ValueError, match="effective latest"):
        deployment.record_approval(
            plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
            rationale="must reject", approved_at="2026-03-05T02:00:00Z",
            approved_by="reviewer", idempotency_key="plan-approval",
        )


def test_fb04_trigger_does_not_fallback_from_revoked_latest_revision(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    monkeypatch.setattr("src.repositories.technical_anchor_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    db = str(tmp_path / "db.sqlite")
    technical = TechnicalScenarioService(db)
    first = technical.ingest(fb04_revision(), "anchor-1")
    first_approval = technical.record_approval(
        anchor_revision_id=first["id"], decision=ApprovalStatus.APPROVED,
        rule_id="FB-04", rationale="reviewed", approved_at="2026-03-01T02:00:00Z",
        approved_by="reviewer", idempotency_key="approval-1",
    )
    technical.ingest(
        fb04_revision(2, first["id"], status=AnchorRevisionStatus.REVOKED),
        "anchor-2",
    )
    deployment = DeploymentPlanService(db)
    plan = deployment.ingest(deployment_for_fb04(
        "2330", "revoked-latest-campaign",
        fb04_triggers(first["id"], first_approval["approval_id"]),
    ), "plan")
    with pytest.raises(ValueError, match="effective latest"):
        deployment.record_approval(
            plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
            rationale="must reject", approved_at="2026-03-05T02:00:00Z",
            approved_by="reviewer", idempotency_key="plan-approval",
        )


def test_fb04_trigger_accepts_effective_latest_approved_revision(tmp_path, monkeypatch):
    monkeypatch.setattr("src.repositories.deployment_plan_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    monkeypatch.setattr("src.repositories.technical_anchor_repository.utc_now_timestamp",
                        lambda: "2026-03-10T00:00:00.000000Z")
    db = str(tmp_path / "db.sqlite")
    technical = TechnicalScenarioService(db)
    first = technical.ingest(fb04_revision(), "anchor-1")
    second = technical.ingest(fb04_revision(2, first["id"]), "anchor-2")
    second_approval = technical.record_approval(
        anchor_revision_id=second["id"], decision=ApprovalStatus.APPROVED,
        rule_id="FB-04", rationale="reviewed latest",
        approved_at="2026-03-02T02:00:00Z", approved_by="reviewer",
        idempotency_key="approval-2",
    )
    deployment = DeploymentPlanService(db)
    plan = deployment.ingest(deployment_for_fb04(
        "2330", "latest-approved-campaign",
        fb04_triggers(second["id"], second_approval["approval_id"], "scenario-v2"),
    ), "plan")
    result = deployment.record_approval(
        plan_revision_id=plan["id"], decision=ApprovalStatus.APPROVED,
        rationale="latest trigger reviewed", approved_at="2026-03-05T02:00:00Z",
        approved_by="reviewer", idempotency_key="plan-approval",
    )
    assert result["decision"] == "approved"
