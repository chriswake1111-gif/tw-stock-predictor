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
| FB-04 symbol/latest-revision enforcement | `tests/test_deployment_plan_asof.py` |
| Deployment revoke remains possible after FB-04 supersession or revoke | `tests/test_deployment_plan_asof.py` |
| Approved-trigger execution provenance | `tests/test_signal_driven_backtester.py` |
| Same-time deterministic fail-closed policy | `tests/test_signal_driven_backtester.py` |

## Local validation

First-review local validation passed 43 focused tests, 18 Phase 4 regression tests,
202 full-regression tests, and 2 unchanged v1 golden tests. The only warning category was the
existing Starlette TestClient deprecation.
Commit and CI identifiers belong in the Draft PR after they are actually observed.

## First review hardening

- Phase 4 and Phase 5 share one effective-anchor state lookup invariant.
- Deployment approval rejects cross-symbol, superseded, revoked, or mismatched FB-04 provenance.
- Deployment revocation remains appendable after its FB-04 anchor is superseded or revoked; only
  positive approval creation revalidates current FB-04 eligibility.
- Approved stage triggers, rather than signal-event strings, populate execution provenance.
- Same-campaign events at the same normalized time are rejected regardless of input order.
- These changes require no schema migration and do not alter the planner formula.

## Second review revocation hardening

- Positive FB-04 eligibility is enforced only when an `APPROVED` deployment event is created.
- A `REVOKED` deployment event remains appendable after its anchor revision is superseded or its
  upstream FB-04 approval is revoked; event timestamps and no-backdating remain enforced.
- Local validation passed 14 deployment as-of tests, 64 Phase 4/5 regression tests, 204 full
  regression tests, and 2 unchanged v1 golden tests. The only warning category was the existing
  Starlette TestClient deprecation.

## Product boundary

The API returns planning data only. Simulation declares `simulation_only=true` and
`automatic_order=false`. No broker SDK, real account, real position, order webhook, copy trading,
or automatic live execution is present.
