# Phase 2 acceptance evidence

Date: 2026-08-01

## Commands executed

```powershell
python -m py_compile src\domain\valuation.py src\repositories\migration_runner.py src\repositories\forward_eps_repository.py
python -m py_compile src\engine\forward_pe_valuation.py src\services\forward_eps_service.py src\api\routes\v2_valuation.py src\api\main.py
python -m pytest -q -p no:cacheprovider --basetemp=.test-tmp tests/test_forward_eps_asof.py tests/test_forward_pe_valuation.py tests/test_v2_valuation_api.py
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider tests/test_api_golden.py
git diff --check
```

## Results

- Phase 2 review-hardening focus tests: 28 passed.
- Clean-environment full repository regression: 119 passed.
- Independent v1 API golden snapshot: 2 passed.
- Clean environment compatibility set: FastAPI 0.141.1, Starlette 1.3.1, Pydantic 2.13.4, httpx2 2.9.1, pytest 9.1.1, pytest-cov 7.0.0.
- Remaining warnings are existing NumPy timedelta deprecations in backtest research tests; TestClient collection succeeds without the legacy httpx package.
- No live network dependency was used by the new tests.

## Acceptance coverage

- Same-day publication cutoff: covered.
- `available_at` and `ingested_at` look-ahead guards: covered.
- Revision state before and after ingestion: covered.
- Duplicate POST idempotency: covered.
- Reproducible fingerprint deduplication: covered.
- Multiple Forward EPS sources are not averaged: covered.
- PE symbol/industry/market scope behavior: covered.
- Draft and revoked PE exclusion: covered.
- Zero or negative EPS `not_applicable`: covered.
- Versioned migration rerun and transactional rollback: covered.
- Partial v2 response without HTTP 500: covered.
- v1 golden snapshot: included in the full regression.
- Write routes default closed and management API-key protected: covered.
- Draft-only import and server-controlled approver identity: covered.
- Forward EPS approval/revocation and approval ID traceability: covered.
- U-level PE fail-closed and C-level project operationalization: covered.
- Cross-symbol/year/source/unit/scope revision rejection: covered.
- Permanent multi-key idempotency bindings: covered.
- Fresh-clone migration with missing parent directory: covered.
- Draft, missing, revoked, and approved Forward EPS states: covered.
- VAL-02 Forward EPS and VAL-04 PE approval IDs map only to their exact Rule Trace entries: covered.
- Backdated approval/revocation events are rejected: covered.

## Boundary evidence

No broker API, real account connection, real order submission, automatic trading, or external Forward EPS collector was added.
