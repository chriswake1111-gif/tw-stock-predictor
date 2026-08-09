# Phase 8 Acceptance Evidence

- Base main SHA: `801865a984eb80d663fdc89bffeb7c784e0bb1ca`
- Branch: `feature/evidence-model-v2-phase8-validation`
- Commit 1: `5a43dfc` — persistence contracts
- Commit 2: `31ba4f7` — deterministic outcome evaluator
- Commit 3: `cce7bf3` — service and API
- Commit 4: identified by subject `test/docs: record phase 8 validation acceptance evidence`;
  its final SHA is recorded in the Draft PR and implementation report because a Git commit cannot
  contain its own final SHA.
- Migrations: `20260810_09_evidence_model_v2_performance_validation` and additive review remediation
  `20260810_10_phase8_review_remediation`
- Evaluation profile: `phase8-historical-scenario-mvp`, revision 1, explicit protected acknowledgement
- Evaluator: `phase8_historical_scenario_v1`
- Universe: fixed Phase 2 14-symbol research cohort
- Outcome manifest hash: `39b4998e0d120fcb5aa6d87a4d6645b490f58975789b36bf2f12e515fcb7ab15`
- Independent calendar: `twse_fmtqik_20140102_20260731_v1`, 3,068 sessions,
  SHA-256 `110dd61a903797bfa6c5e345bf6b1245ad509def7a52aac67a8d595c6b0ab259`
- Calendar filter: raw files remain hashed; 2 non-session TAIEX rows are excluded from the usable
  view under `exclude_raw_rows_outside_frozen_calendar_v1`
- Run membership: immutable requested snapshot membership, including zero-subject snapshots
- Coverage: cohort, snapshot, subject and evaluation-record units are reported independently
- Pending policy: only horizon expiry beyond immutable `outcome_observed_through_session`; missing
  bars within the observed boundary are `insufficient_future_data`
- Benchmark: frozen TAIEX price return
- Horizons: 20 and 60 frozen sessions

Final test counts, warning count, v1 golden result, runtime-cache gate and GitHub Actions Run ID are
recorded in the Draft PR and Phase 8 Implementation Report after the immutable Commit 4 exists.

First Code Review remediation local acceptance (2026-08-10):

- Phase 8 focused: 31 passed
- Full regression: 326 passed, 1 Starlette/httpx deprecation warning
- v1 golden: 2 passed
- `git diff --check`: passed
- tracked runtime-cache gate: passed
- GitHub Actions: recorded in Draft PR #7 after the remediation commit is pushed

The user-owned untracked specification package remains outside Git. No Phase 9, broker API, account
connection, real order, automated trading, probability, ranking or parameter optimization is included.
