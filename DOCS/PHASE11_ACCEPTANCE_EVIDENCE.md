# Phase 11 Acceptance Evidence

Date: 2026-08-12

## Acceptance matrix

| ID | Evidence | Result |
|---|---|---|
| AC-01 | Stable comparison contract and policy version | PASS |
| AC-02 | Base-to-comparison direction is disclosed | PASS |
| AC-03 | Decimal canonicalization is deterministic | PASS |
| AC-04 | Timezone-aware timestamp canonicalization | PASS |
| AC-05 | Missing sentinel differs from null | PASS |
| AC-06 | Semantic whitelist; no recursive JSON diff | PASS |
| AC-07 | Stable delta ordering | PASS |
| AC-08 | Identical snapshots return no deltas | PASS |
| AC-09 | Incompatible contracts fail closed | PASS |
| AC-10 | Stored facts and current context are separate | PASS |
| AC-11 | Resource revision change detection | PASS |
| AC-12 | Approval revoke/change detection | PASS |
| AC-13 | Rule/profile/status change detection | PASS |
| AC-14 | Valuation/technical/confluence changes | PASS |
| AC-15 | Deployment/liquidity/screening changes | PASS |
| AC-16 | One SQLite read transaction | PASS |
| AC-17 | Connection-aware repository reads | PASS |
| AC-18 | Knowledge cutoff required and timezone-aware | PASS |
| AC-19 | Missing snapshot returns 404 | PASS |
| AC-20 | Persisted integrity failure is server error | PASS |
| AC-21 | Comparison API is GET-only | PASS |
| AC-22 | Public DTO omits snapshot hashes | PASS |
| AC-23 | API comparison causes zero database writes | PASS |
| AC-24 | Frontend renders server deltas without recomputation | PASS |
| AC-25 | Desktop/mobile responsive Playwright states | PASS |
| AC-26 | No migration, collector, approval, trading, or Phase 12 scope | PASS |

## Validation commands

The final command results are recorded in the Draft PR and delivery report. The validation set includes:

```text
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider tests/test_api_golden.py
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
npm.cmd run test:visual
git diff --check
```

## Recorded results

- Phase 11 focused backend: 23 passed, 1 existing TestClient deprecation warning.
- Full Python regression: 415 passed, 1 existing TestClient deprecation warning.
- v1 API golden snapshot: 2 passed, 1 existing TestClient deprecation warning.
- Frontend unit suite: 19 passed.
- Frontend lint, TypeScript check, and production build: passed.
- Playwright visual suite: 4 passed across desktop and mobile projects.

## Runtime artifact gate

Tracked cache and bytecode paths are checked with `git ls-files`. Local Playwright screenshots and build output remain ignored runtime evidence and are not committed.
