import pytest

from src.domain.evidence import EvidenceRule
from src.services.rule_registry import RuleRegistry


def test_registry_exposes_rule_evidence_mode_and_forbidden_uses():
    registry = RuleRegistry()

    rule = registry.describe("MA-03")

    assert rule["rule_id"] == "MA-03"
    assert rule["evidence_level"] == "U"
    assert rule["implementation_mode"] == "legacy_experimental"
    assert "core_score" in rule["forbidden_uses"]
    assert "automatic_order" in rule["forbidden_uses"]


@pytest.mark.parametrize("rule_id", ["MA-01", "MA-02", "MA-03", "ENT-01", "ENT-03", "VAL-05"])
def test_unverified_legacy_rules_are_not_verified_core(rule_id):
    rule = RuleRegistry().get(rule_id)

    assert rule.evidence_level.value == "U"
    assert rule.implementation_mode.value != "verified_core"


def test_u_level_rule_cannot_be_loaded_as_verified_core():
    with pytest.raises(ValueError, match="U evidence cannot be registered as verified_core"):
        EvidenceRule.from_mapping(
            "U-01",
            {
                "title": "Unverified rule",
                "evidence_level": "U",
                "implementation_mode": "verified_core",
                "allowed_outputs": ["research"],
                "forbidden_uses": ["automatic_order"],
                "version": "2.0.0",
            },
        )


def test_human_approval_rule_fails_closed_without_approval_id():
    registry = RuleRegistry()

    assert registry.evaluate_use("VAL-02", "forward_eps_observation")["status"] == "needs_human_input"
    assert registry.evaluate_use(
        "VAL-02", "forward_eps_observation", approval_id="review-001"
    )["status"] == "available"
    assert registry.evaluate_use("VAL-01", "valuation_scenario")["status"] == "available"
    assert registry.evaluate_use("VAL-03", "valuation_scenario")["status"] == "available"
    assert registry.evaluate_use("VAL-04", "valuation_scenario")["status"] == "needs_human_input"


def test_forbidden_usage_takes_priority():
    result = RuleRegistry().evaluate_use("MA-03", "automatic_order")

    assert result["status"] == "forbidden"


def test_all_c_level_rules_disclose_project_operationalization():
    rules = RuleRegistry().list_rules()

    assert rules
    assert all(rule.project_operationalization for rule in rules if rule.evidence_level.value == "C")
