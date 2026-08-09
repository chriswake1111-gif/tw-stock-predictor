# Phase 6 Acceptance Evidence

## Local validation

Executed on 2026-08-09 from `feature/evidence-model-v2-phase6-screening`:

```text
python -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-phase6-full-rerun
253 passed, 1 existing Starlette TestClient/httpx deprecation warning

python -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-phase6-golden tests/test_api_golden.py
2 passed, 1 existing Starlette TestClient/httpx deprecation warning
```

Phase 6 focused coverage contains 49 parametrized test cases across:

- `tests/test_phase6_migration.py`
- `tests/test_screening_profile_asof.py`
- `tests/test_two_lows_one_high_percentiles.py`
- `tests/test_security_screening_service.py`
- `tests/test_screening_api.py`

## Invariant evidence

| Invariant | Evidence |
| --- | --- |
| additive, rerunnable, fresh-parent migration | `test_phase6_migration_is_additive_rerunnable_and_fresh_parent_safe` |
| corrected/revoked/future valuation revisions | `test_screening_profile_asof.py` revision tests |
| immutable valuation identity | `test_valuation_revision_cannot_change_logical_identity` |
| profile revision needs new approval | `test_profile_revision_requires_new_approval_and_revoke_has_no_fallback` |
| deterministic percentile ties | `test_ties_use_deterministic_upper_rank` |
| invalid PE/PB/yield fail closed | `test_invalid_current_metrics_never_pass` |
| no fixed PB/yield override | `test_absolute_pb_and_yield_values_do_not_override_percentiles` |
| exact Forward EPS source, no averaging | `test_forward_eps_source_is_not_averaged_or_auto_selected` |
| consecutive fiscal years required | `test_forward_eps_requires_two_consecutive_years` |
| non-positive EPS denominator rejected | `test_nonpositive_earlier_forward_eps_is_not_low_pe_pass` |
| MA-03 cannot produce a pass | `test_technical_turn_is_replaceable_and_legacy_ma_cannot_pass` |
| profile selection has no hidden precedence | `test_profile_selection_is_explicit_when_multiple_apply` |
| industry data missing fails closed | `test_industry_basis_fails_closed_without_peer_contract` |
| write API default-closed/admin-protected | `test_screening_writes_default_closed_and_require_admin` |
| approval metadata controlled by server | `test_public_dtos_hide_dedup_and_server_controls_approval` |
| general analysis preserves section-local status | `test_general_analysis_exposes_screening_human_input_without_legacy_fallback` |

All tests are offline and use fixed local fixtures or SQLite databases. No FinMind, TWSE, yfinance,
broker, or other external network call is required.

## Knowledge debt check

### Core logic

- Percentile calculation: deterministic empirical upper rank.
- Threshold profile: immutable approved profile parameters, not fixed Du thresholds.
- EPS growth gate: exact approved Phase 2 source and consecutive years.
- Technical aggregation: replaceable protocol; unavailable default fails closed.
- Approval semantics: exact revision binding, append-only events, latest revoke wins.

### Data flow

```text
normalized valuation observation -> percentile engine
approved Forward EPS -> consecutive-year growth gate
technical component -> independent technical result
approved screening profile -> screening service -> detailed research output
```

### Human must understand

- 30/30/70 is project operationalization, not a fixed Du-method threshold.
- Low PE alone does not prove that a company is cheap; negative PE is invalid, not extra-low.
- Forward EPS source, revision, approval, and cutoff determine whether growth is comparable.
- Industry percentile is not interchangeable with self-history percentile.
- A technical turn is not an automatic entry point.
- `meets_approved_profile` is a research screen, not a buy recommendation.
