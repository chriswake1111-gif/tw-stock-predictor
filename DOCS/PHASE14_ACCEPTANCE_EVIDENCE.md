# Phase 14 Acceptance Evidence — LUK-29

## Scope

This evidence file covers the Phase 14 Implementation and the latest Second
Code Review remediation. Baseline: `be8e931038bf818cf7d9f578e18fa91b8f068cd4`.
The implementation must remain in one Draft PR through the review gates before
any merge or deployment. Phase 15 is not started.

## Acceptance mapping

| Acceptance criteria | Implementation/evidence |
| --- | --- |
| AC-01–AC-04 | Exact TWSE/TPEx resource IDs, URLs, top-level array contracts, required fields, and close-field selection are implemented in `src/collectors/eod_close_collectors.py`; parser fixtures and drift tests cover the boundary. |
| AC-05–AC-07 | Phase 14 resource registry, immutable TWD/share policies, policy fingerprints, write-disabled seed, and rerunnable migration are in `migrations/20260827_18_phase14_eod_close_context.sql` and `src/repositories/migration_runner.py`. |
| AC-08–AC-10 | Exact TWSE ISIN classification URL/labels, product classifier, stock-only eligibility, unsupported-product handling, and currency/unit gate are implemented in `src/collectors/twse_isin_classification_collector.py` and `src/domain/eod_close.py`. |
| AC-11–AC-16 | The currentness, source-date, volume/close, zero-volume, suspension, partial-proof, and status-precedence policies are implemented in `src/services/eod_close_service.py` and `src/domain/eod_close.py`; non-available close values are null. |
| AC-17–AC-21 | The repository performs cutoff-visible identity/classification reads and date-first exact-D as-of selection without earlier-date fallback in `src/repositories/eod_close_repository.py`. |
| AC-22–AC-25 | Phase 10 raw provenance, append-only EOD source/classification/observation chains, explicit supersession, immutable SQL triggers, a durable command-reservation state machine, and the distinct operator-command idempotency ledger are implemented in the EOD repository and ingestion service. |
| AC-26–AC-29 | GET-only current/as-of routes, 422/503 handling, absence of a `trade_date` query parameter, stable DTO redaction, and query-only public reads are covered by `src/api/routes/v2_eod_close.py` and API tests. |
| AC-30–AC-31 | The `/eod-close` frontend page, API client/types, neutral disclosure, backend-only GET calls, and non-available null rendering are covered by the frontend unit test. |
| AC-32–AC-34 | Evidence backup/restore includes the six EOD tables, including command reservations; the Phase 14 repository/service tests cover migration, lineage, idempotency, atomic failure recovery, classifier fixtures, as-of blocker behavior, backup/restore, and Phase 10/13 regression boundaries. |
| AC-35 | Phase 14 scope/boundary documentation, acceptance evidence, explicit no-calendar/no-fallback policy, Draft PR-only gate, and “Phase 15 not started” state are recorded in this file and `DOCS/EVIDENCE_MODEL_V2_PHASE14.md`. |

## First Code Review remediation evidence

The latest First Code Review required evidence for the complete PIT lineage and
visibility boundary. The remediation adds focused repository/service coverage
for:

- D1 correction visibility before and after its cutoff;
- a late D1 correction that cannot replace a newer D2;
- D2 blocked, revoked, missing-row, and ineligible/zero-volume states with no
  fallback to D1;
- classifier correction visibility, latest-blocker no-fallback, and repeated
  identical-cutoff determinism;
- Phase 13 identity epoch/code reuse at the EOD cutoff boundary;
- current selection where `available_at` and `ingested_at` differ, proving
  future-available evidence cannot affect an earlier evaluated result;
- backup/restore semantic equivalence for correction, revocation, D-first
  selection, current latest-blocking, identity epochs, and raw zero-volume
  retention versus public ineligibility.

The EOD repository now applies both `available_at <= evaluated_at` and
`ingested_at <= evaluated_at` to current source, classification, and
observation selection. As-of selection retains the same two-boundary rule.

## Second Code Review remediation evidence

The Second Code Review identified three issues. The remediation is additive and
keeps Migration 18 immutable:

- `migrations/20260827_19_phase14_second_code_review_remediation.sql` adds the
  `source_observation_state=source_observed` evidence state and a durable
  `eod_ingestion_command_reservations` state machine. Public eligibility and
  observation status remain separate derived outcomes.
- Operator price/classification writes reserve the idempotency key before
  evidence side effects. Raw revision, source/classification evidence,
  observations, the legacy EOD ledger, Phase 10 run/item, and terminal run
  state are committed in one SQLite transaction; failure cleanup resets the
  reservation for a retry and preserves lock/run/audit references.
- Fixed tests cover failure injection after raw, source, and observation
  writes; same-key concurrency; same-key/different-payload rejection; backup
  of the reservation; and source-observed rows for eligible, zero-volume,
  unsupported, missing/blocked-classifier, and unusable-value cases.
- Product classification now passes exact warrant/TDR forms and explicit
  CFI/Remarks evidence through the parser, domain classifier, ingestion path,
  and public context composition. Fixed fixtures cover `認購權證`, `認售權證`,
  `臺灣存託憑證`, and preferred CFI evidence.

## Validation record

The following results were obtained in the remediation worktree after the
Second Code Review changes:

| Check | Result |
| --- | --- |
| Second Code Review remediation tests | `21 passed` |
| Phase 14 backend plus Phase 13 migration regression tests | `73 passed` |
| Full Python suite excluding `tests/test_api.py` | `627 passed, 1 warning` |
| Legacy `tests/test_api.py` isolation | `1 passed, 2 failed`; the two existing `/api/analysis` tests could not open the local yfinance cache database in this environment. No Phase 14 test failed. |
| Python compile gate | passed for all changed Python modules |
| Frontend typecheck | passed |
| Frontend lint | passed |
| Frontend unit tests | `6 files, 27 tests passed` |
| Frontend production build and secret gate | passed; `production_bundle_admin_secret_gate=PASS` |
| Frontend visual test | blocked: the local Playwright runner did not obtain a 4173 listener and was safely terminated; no visual pass is claimed |

No result is treated as passed unless its command was actually executed. The final `git diff --check`, artifact gate, exact implementation head, and exact-head GitHub Actions result are recorded in the LUK-29 completion comment and Draft PR.

Required validation set:

```powershell
python -X utf8 -m pytest -q --basetemp=.pytest_phase14_full
python -X utf8 -m py_compile src/domain/eod_close.py src/collectors/eod_close_collectors.py src/collectors/twse_isin_classification_collector.py src/repositories/eod_close_repository.py src/services/eod_close_ingestion_service.py src/services/eod_close_service.py src/api/routes/v2_eod_close.py
npm.cmd run typecheck
npm.cmd run lint
npm.cmd test -- --run
npm.cmd run build
npm.cmd run test:visual
git diff --check
```

The visual test is an evidence item, not a reason to weaken the read-only boundary. If the local browser/runtime cannot execute it, the result is recorded as blocked by environment.
