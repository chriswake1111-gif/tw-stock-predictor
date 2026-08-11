import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.domain.analysis_snapshot import SynthesisProfileRevision, SynthesisProfileScope
from src.engine.target_confluence import TargetConfluenceEngine
from src.services.rule_registry import RuleRegistry
from tests.test_phase7_api import create_approved_inputs, headers


client = TestClient(app)
CONTRACT_PATH = Path(__file__).parent / "contracts" / "phase9_frontend_contracts.json"


def _contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _create_fb04(key: str) -> None:
    created = client.post(
        "/api/v2/anchors",
        headers=headers(key, "phase9-contract-fb04-create"),
        json={
            "logical_anchor_set_id": "anchor-support",
            "revision_number": 1,
            "symbol": "2330.TW",
            "evidence_basis_rule_id": "FB-04",
            "anchors": [
                {"role": "origin", "price": 80, "market_date": "2026-01-01"},
                {"role": "swing_end", "price": 100, "market_date": "2026-02-01"},
            ],
            "available_at": "2026-08-01T00:00:00Z",
            "source": "manual-review",
        },
    )
    created.raise_for_status()
    anchor_id = created.json()["anchor_set"]["id"]
    approved = client.post(
        f"/api/v2/anchors/{anchor_id}/approval",
        headers=headers(key, "phase9-contract-fb04-approve"),
        json={
            "decision": "approved",
            "rule_id": "FB-04",
            "rationale": "reviewed support anchor",
            "approved_at": "2026-08-01T01:00:00Z",
        },
    )
    approved.raise_for_status()


def _create_deployment_plan(key: str) -> None:
    created = client.post(
        "/api/v2/deployment-plan",
        headers=headers(key, "phase9-contract-plan-create"),
        json={
            "logical_campaign_id": "phase9-contract-plan",
            "revision_number": 1,
            "symbol": "2330.TW",
            "planned_total_capital": "900000",
            "triggers": [
                {"stage": 1, "trigger_type": "manual_price", "value": "100"},
                {"stage": 2, "trigger_type": "manual_price", "value": "95"},
                {"stage": 3, "trigger_type": "manual_price", "value": "90"},
            ],
            "available_at": "2026-08-01T00:00:00Z",
        },
    )
    created.raise_for_status()
    plan_id = created.json()["deployment_plan"]["plan_revision_id"]
    approved = client.post(
        f"/api/v2/deployment-plan/{plan_id}/approval",
        headers=headers(key, "phase9-contract-plan-approve"),
        json={
            "decision": "approved",
            "rationale": "reviewed virtual budget plan",
            "approved_at": "2026-08-01T01:00:00Z",
        },
    )
    approved.raise_for_status()


def _analysis_contract_slice(body: dict) -> dict:
    valuation_fields = (
        "status", "observation_id", "pe_scenario_id", "fiscal_year",
        "source_name", "eps_scenario", "eps_value", "pe_value", "target_price",
    )
    scenario_fields = (
        "anchor_set_revision_id", "scenario_type", "semantic_role",
        "calculated_level", "price_unit",
    )
    entry_fields = (
        "stage", "weight", "capital_budget", "currency",
        "remaining_entries_after_stage",
    )
    return {
        "valuation_target_matrix": [
            {field: cell[field] for field in valuation_fields}
            for cell in body["valuation"]["target_matrix"]
        ],
        "technical_scenarios": [
            {
                **{field: scenario[field] for field in scenario_fields},
                "rule_trace": {"rule_id": scenario["rule_trace"]["rule_id"]},
            }
            for scenario in body["technical_support"]["scenarios"]
        ],
        "deployment_entries": [
            {
                **{field: entry[field] for field in entry_fields},
                "trigger": {
                    field: entry["trigger"][field]
                    for field in ("stage", "trigger_type", "value")
                },
            }
            for entry in body["deployment_plan"]["plans"][0]["entries"]
        ],
    }


def _candidate(candidate_id: str, family: str, low: str, high: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "method_family": family,
        "rule_id": family,
        "rule_version": "2.0.0",
        "evidence_level": "A",
        "implementation_mode": "verified_core",
        "semantic_role": "target",
        "price_low": low,
        "price_high": high,
        "source_resource_ids": [f"{candidate_id}-source"],
        "dependency_keys": [f"{candidate_id}-dependency"],
        "approval_ids": [f"{candidate_id}-approval"],
    }


def test_checked_frontend_contract_matches_real_analysis_and_snapshot_api(
    monkeypatch, tmp_path
):
    contract = _contract()
    key, profile_id = create_approved_inputs(monkeypatch, tmp_path)
    _create_fb04(key)
    _create_deployment_plan(key)
    params = {
        "knowledge_cutoff_at": "2030-01-01T00:00:00Z",
        "synthesis_profile_revision_id": profile_id,
    }
    response = client.get("/api/v2/analysis/2330.TW", params=params)
    response.raise_for_status()
    body = response.json()

    actual = _analysis_contract_slice(body)
    assert actual["valuation_target_matrix"] == contract["valuation_target_matrix"]
    assert actual["technical_scenarios"] == contract["technical_scenarios"]
    assert actual["deployment_entries"] == contract["deployment_entries"]

    refresh = client.post(
        "/api/v2/analysis/2330.TW/refresh",
        params={"knowledge_cutoff_at": params["knowledge_cutoff_at"]},
        headers=headers(key, "phase9-contract-snapshot"),
        json={"synthesis_profile_revision_id": profile_id},
    )
    refresh.raise_for_status()
    snapshot_id = refresh.json()["snapshot"]["snapshot_id"]
    loaded = client.get(f"/api/v2/analysis/snapshots/{snapshot_id}")
    loaded.raise_for_status()
    snapshot = loaded.json()["snapshot"]
    assert ("analysis_status" in snapshot) is contract["snapshot_detail"]["analysis_status_present"]
    assert contract["snapshot_detail"]["status_source"] == "output.status"
    assert snapshot["output"]["status"] == refresh.json()["snapshot"]["output"]["status"]


def _canonical_phase7_profile(method_families=("VAL-01", "FB-03", "FB-04")):
    profile = SynthesisProfileRevision(
        logical_profile_id="phase9-production-contract",
        revision_number=1,
        scope=SynthesisProfileScope.GLOBAL,
        allowed_method_families=method_families,
        overlap_tolerance="0.01",
        evidence_strength_policy=(
            {"minimum_independent_target_components": 2, "label": "moderate"},
            {"minimum_independent_target_components": 3, "label": "high"},
        ),
        available_at="2026-08-01T00:00:00Z",
        created_by="phase9-contract-test",
        rationale="canonical production contract profile",
    ).canonical_payload()
    return {
        **profile,
        "id": "profile-contract",
        "verified_approval_id": "approval-profile",
    }


def test_checked_production_confluence_contract_uses_canonical_domain_profile():
    contract = _contract()
    candidates = [
        _candidate("a-val", "VAL-01", "790", "805"),
        _candidate("a-fb", "FB-03", "790", "805"),
    ]
    profile = _canonical_phase7_profile()
    rule = RuleRegistry().describe("TGT-01")
    rule_contract = contract["target_confluence_rule"]
    assert rule_contract == {
        "rule_id": "TGT-01",
        "rule_version": rule["version"],
        "evidence_level": rule["evidence_level"],
        "implementation_mode": rule["implementation_mode"],
        "project_operationalization": rule["project_operationalization"],
        "approval_id": "approval-tgt",
    }
    result = TargetConfluenceEngine().evaluate(
        candidates=candidates,
        profile=profile,
        rule_trace=rule_contract,
    )
    assert result["overlap_ranges"] == contract["target_confluence_clusters"]
    assert {item["method_family"] for item in candidates} == {"VAL-01", "FB-03"}

    with pytest.raises(ValueError, match="unsupported method family"):
        _canonical_phase7_profile(("VAL-01", "FB-03", "VAL-03"))
