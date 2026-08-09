"""Signal-driven Phase 5 historical simulator with no internally derived signals."""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from decimal import Decimal, ROUND_DOWN
from typing import Any, Iterable

import pandas as pd


def _normalize_signal_time(value: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("signal_date must be parseable") from exc
    if pd.isna(timestamp):
        raise ValueError("signal_date must be parseable")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp


@dataclass(frozen=True)
class SignalEvent:
    event_id: str
    campaign_id: str
    symbol: str
    event_type: str
    signal_date: str
    stage: int | None = None
    trigger_source: str = "external_signal"
    trigger_reference_id: str | None = None

    def validate(self) -> None:
        if not all((self.event_id.strip(), self.campaign_id.strip(), self.symbol.strip())):
            raise ValueError("signal event identity, campaign, and symbol are required")
        _normalize_signal_time(self.signal_date)
        if self.event_type not in {"entry", "invalidation"}:
            raise ValueError("event_type must be entry or invalidation")
        if self.event_type == "entry" and self.stage not in {1, 2, 3}:
            raise ValueError("entry signal stage must be 1, 2, or 3; a fourth entry is forbidden")
        if self.event_type == "invalidation" and self.stage is not None:
            raise ValueError("invalidation signal cannot carry a deployment stage")


class SignalDrivenBacktester:
    """Consumes approved plans and external events; never derives signals from OHLCV."""

    def __init__(self, *, commission_rate: float = 0.001425,
                 sales_tax_rate: float = 0.003, slippage_rate: float = 0.001):
        rates = {
            "commission_rate": commission_rate,
            "sales_tax_rate": sales_tax_rate,
            "slippage_rate": slippage_rate,
        }
        if any(value < 0 for value in rates.values()):
            raise ValueError("simulation cost rates cannot be negative")
        self.commission_rate = Decimal(str(commission_rate))
        self.sales_tax_rate = Decimal(str(sales_tax_rate))
        self.slippage_rate = Decimal(str(slippage_rate))

    @staticmethod
    def _bars(ohlcv_df: pd.DataFrame) -> pd.DataFrame:
        if not {"date", "open"}.issubset(ohlcv_df.columns):
            raise ValueError("OHLCV data requires date and open columns")
        bars = ohlcv_df.copy()
        bars["date"] = pd.to_datetime(bars["date"])
        bars = bars.dropna(subset=["date", "open"]).sort_values("date")
        if bars.empty or bars["date"].duplicated().any():
            raise ValueError("execution calendar must be non-empty with unique dates")
        return bars

    @staticmethod
    def _next_bar(bars: pd.DataFrame, signal_time: pd.Timestamp) -> pd.Series | None:
        candidates = bars[bars["date"] > signal_time]
        return None if candidates.empty else candidates.iloc[0]

    @staticmethod
    def _validate_plan(plan: dict[str, Any]) -> None:
        trace = plan.get("rule_trace")
        if plan.get("status") != "available" or not trace:
            raise ValueError("signal simulation requires an approved deployment plan")
        if trace.get("rule_id") != "ENT-02" or not trace.get("approval_id"):
            raise ValueError("deployment plan requires an exact ENT-02 approval trace")
        entries = plan.get("entries", [])
        if len(entries) != 3 or [entry.get("stage") for entry in entries] != [1, 2, 3]:
            raise ValueError("approved plan must contain exactly three ordered entries")
        if any(not isinstance(entry.get("trigger"), dict) for entry in entries):
            raise ValueError("approved plan entries must retain their approved triggers")
        allowed_trigger_types = {
            "manual_price", "user_percentage", "atr_multiple", "approved_fb04_scenario",
        }
        for entry in entries:
            trigger = entry["trigger"]
            if trigger.get("stage") != entry["stage"]:
                raise ValueError("approved trigger stage must match its plan entry")
            if trigger.get("trigger_type") not in allowed_trigger_types:
                raise ValueError("approved plan contains an unsupported trigger type")

    @staticmethod
    def _trigger_matches(event: SignalEvent, approved_trigger: dict[str, Any]) -> bool:
        trigger_type = approved_trigger.get("trigger_type")
        approved_reference = approved_trigger.get("reference_id")
        if trigger_type in {"atr_multiple", "approved_fb04_scenario"}:
            return bool(approved_reference) and event.trigger_reference_id == approved_reference
        return event.trigger_reference_id is None

    def run(self, ohlcv_df: pd.DataFrame, plans: dict[str, dict[str, Any]],
            events: Iterable[SignalEvent]) -> dict[str, Any]:
        bars = self._bars(ohlcv_df)
        for campaign_id, plan in plans.items():
            self._validate_plan(plan)
            if campaign_id != plan.get("logical_campaign_id"):
                raise ValueError("plan dictionary key must match logical_campaign_id")
        event_list = list(events)
        normalized_events: list[tuple[pd.Timestamp, SignalEvent]] = []
        for event in event_list:
            event.validate()
            plan = plans.get(event.campaign_id)
            if plan is None:
                raise ValueError(f"signal references unknown campaign {event.campaign_id}")
            if event.symbol.strip().upper() != plan["symbol"].strip().upper():
                raise ValueError("signal symbol must match its deployment plan")
            normalized_events.append((_normalize_signal_time(event.signal_date), event))
        conflict_counts = Counter(
            (event.campaign_id, signal_time)
            for signal_time, event in normalized_events
        )
        normalized_events.sort(
            key=lambda item: (item[0], item[1].campaign_id, item[1].event_id)
        )

        states = {campaign_id: {"next_stage": 1, "shares": Decimal("0"),
                               "invested_cash": Decimal("0"), "realized_pnl": None,
                               "terminated": False}
                  for campaign_id in plans}
        executions: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        total_costs = Decimal("0")

        for signal_time, event in normalized_events:
            state = states[event.campaign_id]
            plan = plans[event.campaign_id]
            if conflict_counts[(event.campaign_id, signal_time)] > 1:
                rejected.append({
                    "event_id": event.event_id,
                    "reason": "ambiguous_same_time_events",
                    "normalized_signal_time": signal_time.isoformat(),
                })
                continue
            if event.event_type == "entry" and state["terminated"]:
                rejected.append({"event_id": event.event_id, "reason": "campaign_already_invalidated"})
                continue
            approved_trigger = None
            if event.event_type == "entry":
                approved_trigger = dict(plan["entries"][event.stage - 1]["trigger"])
                if not self._trigger_matches(event, approved_trigger):
                    rejected.append({
                        "event_id": event.event_id,
                        "reason": "trigger_reference_mismatch",
                    })
                    continue
            bar = self._next_bar(bars, signal_time)
            if bar is None:
                rejected.append({"event_id": event.event_id, "reason": "no_next_available_bar"})
                continue
            execution_date = pd.Timestamp(bar["date"]).date().isoformat()
            raw_open = Decimal(str(bar["open"]))
            if not raw_open.is_finite() or raw_open <= 0:
                rejected.append({"event_id": event.event_id, "reason": "invalid_execution_open"})
                continue

            if event.event_type == "entry":
                if event.stage != state["next_stage"]:
                    rejected.append({"event_id": event.event_id, "reason": "stage_sequence_violation",
                                     "expected_stage": state["next_stage"]})
                    continue
                budget = Decimal(plan["entries"][event.stage - 1]["capital_budget"])
                execution_price = raw_open * (Decimal("1") + self.slippage_rate)
                shares = (budget / (execution_price * (Decimal("1") + self.commission_rate))).quantize(
                    Decimal("0.000001"), rounding=ROUND_DOWN
                )
                gross = shares * execution_price
                commission = gross * self.commission_rate
                cost = commission + (execution_price - raw_open) * shares
                state["shares"] += shares
                state["invested_cash"] += gross + commission
                state["next_stage"] += 1
                total_costs += cost
                executions.append({
                    "event_id": event.event_id, "campaign_id": event.campaign_id,
                    "symbol": event.symbol.strip().upper(), "action": "simulated_entry",
                    "stage": event.stage, "signal_date": event.signal_date,
                    "execution_date": execution_date,
                    "signal_event_source": event.trigger_source,
                    "approved_trigger": approved_trigger,
                    "capital_budget": str(budget), "shares": str(shares),
                    "execution_price": str(execution_price), "gross_value": str(gross),
                    "commission": str(commission),
                })
            else:
                state["terminated"] = True
                shares = state["shares"]
                if shares <= 0:
                    rejected.append({"event_id": event.event_id, "reason": "no_position_to_invalidate"})
                    continue
                execution_price = raw_open * (Decimal("1") - self.slippage_rate)
                gross = shares * execution_price
                commission = gross * self.commission_rate
                sales_tax = gross * self.sales_tax_rate
                net_proceeds = gross - commission - sales_tax
                realized_pnl = net_proceeds - state["invested_cash"]
                total_costs += commission + sales_tax + (raw_open - execution_price) * shares
                state["shares"] = Decimal("0")
                state["realized_pnl"] = realized_pnl
                executions.append({
                    "event_id": event.event_id, "campaign_id": event.campaign_id,
                    "symbol": event.symbol.strip().upper(), "action": "simulated_exit",
                    "stage": None, "signal_date": event.signal_date,
                    "execution_date": execution_date,
                    "signal_event_source": event.trigger_source,
                    "approved_trigger": None, "shares": str(shares),
                    "execution_price": str(execution_price), "gross_value": str(gross),
                    "net_proceeds": str(net_proceeds), "realized_pnl": str(realized_pnl),
                    "commission": str(commission),
                    "sales_tax": str(sales_tax), "reason": "external_invalidation",
                })

        return {
            "status": "available", "simulation_only": True, "automatic_order": False,
            "signal_source": "external_timeline_only", "executions": executions,
            "rejected_events": rejected,
            "campaign_states": {campaign_id: {
                "next_stage": state["next_stage"], "open_shares": str(state["shares"]),
                "invested_cash": str(state["invested_cash"]),
                "realized_pnl": None if state["realized_pnl"] is None else str(state["realized_pnl"]),
                "terminated": state["terminated"],
            } for campaign_id, state in states.items()},
            "cost_model": {
                "commission_rate": str(self.commission_rate),
                "sales_tax_rate": str(self.sales_tax_rate),
                "slippage_rate": str(self.slippage_rate),
                "total_simulated_costs": str(total_costs),
            },
            "limitations": ["historical_simulation_only", "not_trade_instruction", "no_broker_connection"],
        }
