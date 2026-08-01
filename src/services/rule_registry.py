from pathlib import Path
from typing import Any

import yaml

from src.domain.evidence import EvidenceRule, ImplementationMode
from src.domain.model_status import RuleUseStatus


DEFAULT_REGISTRY_PATH = Path("config/model_rules.yaml")


class RuleRegistry:
    def __init__(self, path: str | Path = DEFAULT_REGISTRY_PATH):
        self.path = Path(path)
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw_rules = raw.get("rules")
        if not isinstance(raw_rules, dict) or not raw_rules:
            raise ValueError("Rule registry must contain a non-empty rules mapping")
        self._rules = {
            rule_id: EvidenceRule.from_mapping(rule_id, definition)
            for rule_id, definition in raw_rules.items()
        }

    def get(self, rule_id: str) -> EvidenceRule:
        try:
            return self._rules[rule_id]
        except KeyError as exc:
            raise KeyError(f"Unknown rule_id: {rule_id}") from exc

    def list_rules(self) -> list[EvidenceRule]:
        return [self._rules[key] for key in sorted(self._rules)]

    def describe(self, rule_id: str) -> dict[str, Any]:
        return self.get(rule_id).to_dict()

    def evaluate_use(self, rule_id: str, usage: str, approval_id: str | None = None) -> dict[str, Any]:
        rule = self.get(rule_id)
        if usage in rule.forbidden_uses:
            status = RuleUseStatus.FORBIDDEN
        elif rule.implementation_mode is ImplementationMode.UNSUPPORTED:
            status = RuleUseStatus.UNSUPPORTED
        elif rule.human_approval_required and not approval_id:
            status = RuleUseStatus.NEEDS_HUMAN_INPUT
        else:
            status = RuleUseStatus.AVAILABLE
        return {"rule_id": rule_id, "usage": usage, "status": status.value}
