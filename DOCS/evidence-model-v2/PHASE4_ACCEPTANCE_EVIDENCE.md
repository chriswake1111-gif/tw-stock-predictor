# Phase 4 Acceptance Evidence

## Scope

- FB-03 approved-anchor equal-amplitude scenario.
- FB-04 approved upward-swing 0.382 retracement scenario.
- Immutable, symbol-bound manual anchor revisions and rule-specific approval events.
- Independent v2 `technical_support` partial section.

## Contract evidence

- `available_at` and `ingested_at` protect anchor knowledge cutoff.
- `approved_at` and approval `ingested_at` protect approval knowledge cutoff.
- Revision and approval revocation rank before status interpretation.
- New anchor revisions do not inherit approval.
- Rule Trace includes exact approval ID and anchor revision ID.
- 0.618, 1.618 and FB-05 are absent from verified Phase 4 calculation.
- Calculation is Decimal-based and rounds half-up to four decimals only at output.

## Validation record

- Phase 4 focused suite: 17 passed.
- Full repository regression with repository-local `--basetemp`: 157 passed.
- v1 API golden snapshot: 2 passed.
- `git diff --check`: passed.
- Existing Starlette TestClient `httpx`／`httpx2` deprecation warning remains; no new warning category was observed.
- The literal full-suite command without `--basetemp` was also attempted locally and was blocked by the pre-existing Windows ACL on `C:\Users\User\AppData\Local\Temp\pytest-of-User`; the same collected suite passed with a repository-local basetemp.

Tests are offline and deterministic. The v1 golden snapshot, Phase 2 valuation and Phase 3 liquidity contracts remain regression guards.

## Boundary

Scenario is not a prediction, guaranteed target, buy/sell instruction or trading action. No automatic anchor/wave detection, broker connection, real order or Phase 5 planner is included.
