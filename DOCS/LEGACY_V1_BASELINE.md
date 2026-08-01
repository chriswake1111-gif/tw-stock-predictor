# Legacy v1 baseline

Recorded before evidence-based model v2 Phase 1 changes.

- Branch baseline: `main` -> `feature/evidence-based-model-v2`
- Test command: `python -m pytest -q`
- Result: 74 passed, 1 existing httpx deprecation warning
- CI: Python 3.12 installs `requirements.txt`, pytest and pytest-cov, then runs `python -m pytest tests/ -v`
- Existing API retained: `GET /api/health`, `GET /api/analysis/{symbol}`
- Existing analysis contract is research-only and has no execution capability.
- Current calculations are classified as legacy model v1 until their evidence rule is independently verified.

The deterministic v1 golden fixture is stored in `tests/fixtures/v1_api_golden.json`. It protects the response fields consumed by the existing frontend while allowing additive model-governance metadata.
