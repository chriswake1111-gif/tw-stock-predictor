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

FB-04 provenance is symbol-bound. At deployment approval time, the referenced anchor must be the
effective latest revision of its logical anchor set under the approval effective time and ingestion
cutoff. Superseded or revoked revisions cannot fall back to an older approved revision, and the
latest approval event must match the stored approval ID. Phase 4 does not persist a separate
scenario entity, so `reference_id` remains a caller provenance label; authoritative identity is
FB-04 plus symbol, anchor revision ID, and approval ID.

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

A new `APPROVED` event must revalidate the current positive eligibility of every FB-04 trigger.
A `REVOKED` event is a safety downgrade and therefore does not require the referenced FB-04 anchor
or approval to remain current, available, or approved. Revocation still requires the exact plan
revision, ENT-02 metadata, valid event timestamps, and append-only no-backdating order.

Rule Trace contains `ENT-02`, rule version, evidence level A, `verified_core`, approval ID,
deployment revision ID, and source-data cutoff.

## Campaign and signal timeline

The simulator consumes approved plans and explicit `entry` or `invalidation` events. It never
derives a signal from OHLCV. Entry order is `1 -> 2 -> 3`; skips, duplicates, stage 4, and entries
after invalidation are rejected. State is isolated by campaign ID. Re-entry after termination
requires a new campaign.

The approved plan trigger is the execution provenance source of truth. An external event only
reports that the approved stage trigger occurred; it cannot replace the trigger type, value, rule,
anchor, approval, or reference. ATR and FB-04 event references must match the approved trigger.
Execution records copy `approved_trigger` from the plan and keep caller text separately as
`signal_event_source`.

Multiple state-changing events for the same campaign and normalized signal time are all rejected as
`ambiguous_same_time_events`. Same-time events for different campaigns remain valid. Remaining
events serialize by signal time, campaign ID, and event ID. This fail-closed ordering policy is a
project operationalization, not a published Du rule.

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
