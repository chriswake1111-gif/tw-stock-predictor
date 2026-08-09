# Phase 5 Acceptance Evidence

## Implemented scope

- A-level ENT-02 three-tranche planner with exact Decimal budget conservation.
- Immutable plan revisions, approval events, and idempotency ledger.
- Knowledge-cutoff-safe approval and revision lookup.
- External-signal-only historical simulator with next-bar execution.
- Protected v2 plan and approval endpoints.
- Explicit v1 legacy strategy isolation and compatibility alias.

## Acceptance mapping

| Contract | Evidence |
|---|---|
| Equal thirds and exact total | `tests/test_three_tranche_planner.py` |
| No fourth entry at either boundary | `tests/test_no_fourth_entry.py` |
| Approval, revision, revoke, FB-04 trace | `tests/test_deployment_plan_asof.py` |
| No external signal means no entry | `tests/test_signal_driven_backtester.py` |
| Next-bar timing, future guard, campaign isolation | `tests/test_signal_driven_backtester.py` |
| Protected and scrubbed API | `tests/test_deployment_plan_api.py` |
| Additive, rerunnable migration | `tests/test_phase5_migration.py` |

## Local validation

Final local validation passed 34 Phase 5 focused tests, 191 full-regression tests, and 2 unchanged
v1 golden tests. The only warning category was the existing Starlette TestClient deprecation.
Commit and CI identifiers belong in the Draft PR after they are actually observed.

## Product boundary

The API returns planning data only. Simulation declares `simulation_only=true` and
`automatic_order=false`. No broker SDK, real account, real position, order webhook, copy trading,
or automatic live execution is present.
