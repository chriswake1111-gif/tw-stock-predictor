# Evidence Model V2 Phase 5

## Scope

Phase 5 adds an evidence-governed capital deployment planner and an external-signal-driven
historical simulator. It does not add a signal generator, broker connection, account integration,
or real order path.

## ENT-02 governance and formula

`ENT-02` is an A-level `verified_core` rule whose allowed Phase 5 output is an
`allocation_plan`. Human approval is mandatory and automatic orders are forbidden.

The planner accepts an explicit positive finite `planned_total_capital`, uses no account or broker
balance, and produces at most three approximately equal budgets. Weights are `0.333333`,
`0.333333`, and `0.333334`. Money uses `Decimal`, TWD cents, half-up rounding for the first two
tranches, and `total - tranche_1 - tranche_2` for the last. Rounding is a project engineering
convention, not a claimed Du rule. Legacy `20/30/50` and cumulative `20/50/100` are excluded.

## Trigger contract and draft behavior

The planner never derives triggers. It accepts `manual_price`, `user_percentage`, `atr_multiple`,
or `approved_fb04_scenario`. Percentages remain `user_parameter`; ATR remains project
configuration. FB-04 references retain scenario ID, anchor revision ID, approval ID, and Rule ID,
and are checked against the Phase 4 approval ledger when the deployment plan is approved.

Missing triggers return `needs_human_input / deployment_triggers_required`. Complete but
unapproved triggers return `needs_human_input / approved_deployment_plan_required`. Provisional
budgets may be shown, but `rules_used` stays approved-only. External Trigger is not a Du-verified
signal unless separately evidenced and approved.

## Human approval, revisions, and as-of behavior

Approval binds one immutable deployment plan revision containing symbol, campaign, planned
capital, and trigger set. A changed revision requires new approval. Events are append-only; the
latest revoke wins and an older approval is not reused. Historical lookup requires plan
`available_at` and `ingested_at`, plus approval `approved_at` and `ingested_at`, to be within the
knowledge cutoff.

Rule Trace contains `ENT-02`, rule version, evidence level A, `verified_core`, approval ID,
deployment revision ID, and source-data cutoff.

## Campaign and signal timeline

The simulator consumes approved plans and explicit `entry` or `invalidation` events. It never
derives a signal from OHLCV. Entry order is `1 -> 2 -> 3`; skips, duplicates, stage 4, and entries
after invalidation are rejected. State is isolated by campaign ID. Re-entry after termination
requires a new campaign.

A signal on T executes only at the next available bar open. Both signal and execution dates are
recorded. A future signal with no subsequent bar is rejected. External invalidation terminates the
campaign and closes its simulated position under documented commission, tax, and slippage
assumptions. Costs are simulation assumptions, not evidence rules. The simulator uses approved
stage budgets and never recalculates thirds.

## Legacy isolation

`LegacyExperimentalStrategy` retains the v1 MA resonance, 7%-11% pullback, cumulative
20/50/100 allocation, SMA55 exit, and breakout-plus-volume behavior. `TuStrategy` remains a
compatibility alias. `TuBacktester` and `CapitalAllocator` remain available for v1 compatibility.
None are used by the Phase 5 planner or signal-driven simulator.

## Boundary and limitations

- Deployment Plan is not a buy recommendation.
- Backtest is not a future prediction.
- Simulation is not a real order; `automatic_order` is false.
- No automatic anchor selection, wave detection, MA signal, pullback signal, or sell signal exists.
- Phase 5 adds no optimizer, broker SDK, account connector, live order, or user-facing approval UI.
