"""Pure ENT-02 three-tranche capital budget planner."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


WEIGHTS = (Decimal("0.333333"), Decimal("0.333333"), Decimal("0.333334"))
CENT = Decimal("0.01")


def build_three_tranche_plan(
    planned_total_capital: str | float,
    triggers: list[dict[str, Any]],
    *,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Allocate an approved budget; never derive or infer entry triggers."""
    total = Decimal(str(planned_total_capital))
    if not total.is_finite() or total <= 0:
        raise ValueError("planned_total_capital must be finite and greater than zero")
    total = total.quantize(CENT, rounding=ROUND_HALF_UP)
    if len(triggers) > 3:
        raise ValueError("ENT-02 forbids a fourth deployment entry")
    stages = [trigger.get("stage") for trigger in triggers]
    if stages != list(range(1, len(stages) + 1)):
        raise ValueError("deployment triggers must be ordered stages 1, 2, and 3")

    one_third = (total / Decimal("3")).quantize(CENT, rounding=ROUND_HALF_UP)
    budgets = (one_third, one_third, total - one_third - one_third)
    entries = [
        {
            "stage": index,
            "weight": str(WEIGHTS[index - 1]),
            "capital_budget": str(budgets[index - 1]),
            "currency": "TWD",
            "trigger": triggers[index - 1] if len(triggers) >= index else None,
            "remaining_entries_after_stage": 3 - index,
        }
        for index in (1, 2, 3)
    ]

    complete = len(triggers) == 3
    approved = bool(
        complete
        and approval
        and approval.get("decision") == "approved"
        and approval.get("rule_id") == "ENT-02"
    )
    if approved:
        rule_trace = {
            "rule_id": "ENT-02",
            "rule_version": approval["rule_version"],
            "evidence_level": approval["evidence_level"],
            "implementation_mode": approval["implementation_mode"],
            "project_operationalization": bool(approval["project_operationalization"]),
            "approval_id": approval["approval_id"],
            "plan_revision_id": approval["plan_revision_id"],
        }
        status = "available"
        reason = None
    else:
        rule_trace = None
        status = "needs_human_input"
        reason = "deployment_triggers_required" if not complete else "approved_deployment_plan_required"
    return {
        "status": status,
        "reason": reason,
        "planned_total_capital": str(total),
        "currency": "TWD",
        "max_entries": 3,
        "automatic_order": False,
        "simulation_only": False,
        "rounding_convention": "project_engineering_decimal_half_up_last_tranche_residual",
        "entries": entries,
        "rule_trace": rule_trace,
        "pending_rule_id": None if approved else "ENT-02",
        "limitations": [
            "capital_budget_only",
            "external_triggers_only",
            "not_trade_instruction",
            "no_automatic_order",
        ],
    }
