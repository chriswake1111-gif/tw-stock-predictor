# Phase 8 Performance Validation Spec

## Inputs

1. Exact immutable Phase 7 `analysis_snapshot` including its output SHA-256.
2. Explicit, revisioned and approved Phase 8 `EvaluationProfile`.
3. Immutable `OutcomeResourceManifest` for the fixed Phase 2 dataset, including an independently
   tracked official-session calendar and `outcome_observed_through_session` boundary.

The semantic run identity includes snapshot-set hash, profile revision, outcome manifest ID/hash,
evaluator version and origin policy. Scenario identity additionally includes snapshot ID, origin,
subject type/ID and horizon.

## Subjects

| Subject | Role | Observed behavior |
|---|---|---|
| VAL-01 | target_candidate | range touch, return, excursions, benchmark/excess return |
| FB-03 | target_candidate | same target observations |
| TGT-01 | target_cluster | exact stored cluster overlap range; stored independence metadata only |
| FB-04 | support_candidate | touched, held, breached context; never target reach |

Phase 8 never re-derives candidate independence and never treats multiple VAL-01 cells as new
independent methods.

## Session and price rules

- Convert `knowledge_cutoff_at` to Asia/Taipei and exclude its entire local calendar date.
- Pick the first later frozen session where the symbol has a usable adjusted bar.
- Record skipped frozen sessions. Never use weekday fallback or nearest-date fallback.
- Start at adjusted open. End at adjusted close of calendar index `start_index + H`.
- Require every frozen session position in the symbol window. If expiry is later than the immutable
  `outcome_observed_through_session`, return `pending`. If expiry is within that boundary but any
  required symbol bar is absent, return `insufficient_future_data`. A shortened closed dataset is
  therefore insufficient, not pending.
- A target touches when `low <= target_high AND high >= target_low`.
- Returns and excursions use Decimal with `ROUND_HALF_UP` and the approved calculation quantum.
- Benchmark uses exact same start/end session. A missing endpoint makes only the benchmark
  component `insufficient_data`.

## Summary denominator

Every group exposes `n`, `numerator`, and `denominator`. Numerator is `target_reached`; denominator
contains only available target subjects ending `target_reached` or `expired`. Quality warnings,
pending, insufficient future data, support context and already-in-range records are excluded.
Grouping always includes `evaluation_origin`, horizon, method family, evidence strength and subject
type.

## Run membership and coverage units

Every requested snapshot is persisted in immutable `evaluation_run_snapshots`, even when it yields
zero eligible subjects. A finalized membership is `evaluated` or `no_eligible_subjects`; the latter
has an explicit reason. Summary coverage keeps these units separate:

- `cohort_symbol_count` and `cohort_symbols_represented`;
- `requested_snapshot_count`, `snapshots_with_eligible_subjects`, and
  `snapshots_without_eligible_subjects`;
- `eligible_subject_count` and `evaluated_subject_count`;
- `evaluation_record_count`, target candidate/cluster counts, and support candidate count;
- quality-warning snapshot count and quality-warning evaluation-record count.
