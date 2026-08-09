# Phase 8 Bias Controls

## Look-ahead and same-day leakage

The evaluator starts strictly after the Asia/Taipei date containing the cutoff, including pre-market,
intraday and after-close cutoff values. It uses only the frozen outcome calendar and exact stored
snapshot. Horizon H ends at index H after the start session at index 0.

## Hindsight control

`historical_reconstruction` is physically preserved and never relabeled as prospective. Summary
groups it separately from actual `live_refresh` snapshots. Current model code, approvals and Rule
Registry state cannot reconstruct or improve the stored analysis.

## Dataset and corporate actions

The approved adapter accepts only the fixed Phase 2 directory, verifies the source contract,
14 symbol files, TAIEX file, per-file SHA-256, frozen calendar hash and aggregate approved hash.
There is no mutable database or `daily_ohlcv` fallback. OHLC uses the already frozen
`provider_compatible_adjusted_v1` contract; runtime corporate-action rewriting is prohibited.

## Interpretation controls

- The manual 14-symbol cohort is not representative of all TWSE/TPEx.
- Historical constituent-universe evidence is unavailable; selection and survivorship bias remain.
- No model/rule/horizon ranking, threshold tuning or same-sample optimization is implemented.
- Observed historical frequency is descriptive, not probability.
- Evidence strength and evidence grade retain their Phase 7 meanings; performance cannot promote them.
