# Phase 8 Evaluation Acceptance Tests

Acceptance covers:

- additive, transactional and rerunnable migration; DB-level run/result immutability;
- profile and approval cutoff/revocation behavior plus permanent idempotency ledger;
- fixed manifest missing/contract mismatch/unmanifested file/file tampering fail-closed behavior;
- strict local-date timing, weekend, frozen-calendar skip, missing bar and 20/60 off-by-one behavior;
- closed target boundaries, above/below/already-in-range, support-only FB-04, excursions and returns;
- exact TAIEX endpoint behavior and no nearest-date fallback;
- anti-model-rebuild spies over current valuation, technical, screening and synthesis services;
- prospective/reconstruction storage and summary separation;
- quality warning coverage without target-rate miss/hit classification;
- default-closed write API, bad key, explicit profile acknowledgement, duplicate POST and exact GET;
- DTO wording guard against prediction probability/ranking semantics;
- full regression, v1 golden, diff check and tracked runtime-cache gate.

Canonical commands:

```text
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider tests/test_api_golden.py
git diff --check
git ls-files | Select-String -Pattern '(^|/)(__pycache__/|data/cache\.db$)|\.py[co]$'
```
