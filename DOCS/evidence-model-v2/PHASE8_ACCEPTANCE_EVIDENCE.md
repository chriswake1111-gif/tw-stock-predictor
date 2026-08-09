# Phase 8 Acceptance Evidence

- Base main SHA: `801865a984eb80d663fdc89bffeb7c784e0bb1ca`
- Branch: `feature/evidence-model-v2-phase8-validation`
- Commit 1: `5a43dfc` — persistence contracts
- Commit 2: `31ba4f7` — deterministic outcome evaluator
- Commit 3: `cce7bf3` — service and API
- Commit 4: identified by subject `test/docs: record phase 8 validation acceptance evidence`;
  its final SHA is recorded in the Draft PR and implementation report because a Git commit cannot
  contain its own final SHA.
- Migration: `20260810_09_evidence_model_v2_performance_validation`
- Evaluation profile: `phase8-historical-scenario-mvp`, revision 1, explicit protected acknowledgement
- Evaluator: `phase8_historical_scenario_v1`
- Universe: fixed Phase 2 14-symbol research cohort
- Outcome manifest hash: `0c2754abae952b145e22bb2b36f80fec8aa00122cc1d9e9acb93092dc4cb4745`
- Benchmark: frozen TAIEX price return
- Horizons: 20 and 60 frozen sessions

Final test counts, warning count, v1 golden result, runtime-cache gate and GitHub Actions Run ID are
recorded in the Draft PR and Phase 8 Implementation Report after the immutable Commit 4 exists.

The user-owned untracked specification package remains outside Git. No Phase 9, broker API, account
connection, real order, automated trading, probability, ranking or parameter optimization is included.
