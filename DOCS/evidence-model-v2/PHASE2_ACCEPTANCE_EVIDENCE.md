# Phase 2 acceptance evidence

Date: 2026-08-01

## Commands executed

```powershell
python -m py_compile src\domain\valuation.py src\repositories\migration_runner.py src\repositories\forward_eps_repository.py
python -m py_compile src\engine\forward_pe_valuation.py src\services\forward_eps_service.py src\api\routes\v2_valuation.py src\api\main.py
python -m pytest -q -p no:cacheprovider --basetemp C:\tmp\tw-stock-evidence-phase2-final-focus-20260801 tests\test_forward_eps_asof.py tests\test_forward_pe_valuation.py tests\test_v2_valuation_api.py
python -m pytest -q -p no:cacheprovider --basetemp C:\tmp\tw-stock-evidence-phase2-full-final-20260801
```

## Results

- Final Phase 2 focus tests: 13 passed.
- Full repository regression including the rollback case: 100 passed.
- Existing warning: one Starlette TestClient/httpx deprecation warning.
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

## Boundary evidence

No broker API, real account connection, real order submission, automatic trading, or external Forward EPS collector was added.
