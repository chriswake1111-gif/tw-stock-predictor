# Phase 12 Acceptance Evidence

This matrix preserves the approved Phase 12 AC-01 through AC-57 namespace. `PASS` means the referenced automated test or direct contract gate was executed in this implementation branch; final command counts are recorded in the Draft PR after the complete regression.

| AC | Approved requirement | Evidence | Status |
|---|---|---|---|
| AC-01 | New canonical watchlist symbol creates one active item. | `test_add_duplicate_restore_and_unarchive_converge_without_losing_history` | PASS |
| AC-02 | `2330` and `2330.TW` are one identity. | `test_canonical_research_symbol_contract`; repository lifecycle test | PASS |
| AC-03 | `2330.TWO` remains independent; no OTC inference. | `test_canonical_research_symbol_contract` | PASS |
| AC-04 | Active duplicate add returns the existing item. | Repository and API lifecycle tests | PASS |
| AC-05 | Archived duplicate add restores the same item with `restored=true`. | Repository and API lifecycle tests | PASS |
| AC-06 | Explicit unarchive converges on the same active state. | Repository lifecycle test | PASS |
| AC-07 | Archive and unarchive transitions are idempotent. | Repository lifecycle test and state helpers | PASS |
| AC-08 | Membership cannot be hard-deleted or have identity fields changed. | Migration trigger test | PASS |
| AC-09 | Review events are append-only. | `test_review_event_is_append_only_and_idempotent` | PASS |
| AC-10 | Acknowledgment binds an exact immutable snapshot ID. | Repository/API acknowledgment tests | PASS |
| AC-11 | Acknowledged snapshot symbol must match membership. | Repository validation path | PASS |
| AC-12 | Archived membership cannot receive a new acknowledgment. | Repository validation path | PASS |
| AC-13 | Review comparison cutoff is explicit. | Domain/API cutoff tests | PASS |
| AC-14 | Cutoffs must be timezone-aware and normalize to UTC. | Domain and service cutoff validators | PASS |
| AC-15 | Cutoff after request receipt is rejected with zero event creation. | API acknowledgment cutoff test | PASS |
| AC-16 | Cutoff before snapshot knowledge cutoff is rejected. | Service acknowledgment validation | PASS |
| AC-17 | `reviewed_at` is the captured server request timestamp. | API/service acknowledgment path | PASS |
| AC-18 | `reviewed_at` and `comparison_cutoff_at` remain separate fields. | Migration and backup round-trip tests | PASS |
| AC-19 | Same idempotency key and semantic payload returns the original event. | Repository and API acknowledgment tests | PASS |
| AC-20 | Same key with a different semantic payload returns conflict. | Repository and API acknowledgment tests | PASS |
| AC-21 | Latest review event ordering is deterministic. | Repository ordering SQL and append-only ID tie-breaker | PASS |
| AC-22 | Actual latest visible snapshot uses cutoff-safe deterministic ordering. | `test_actual_latest_ordering_and_future_cutoff_fail_closed` | PASS |
| AC-23 | An incompatible actual latest snapshot cannot fall back to an older compatible row. | Shared service state machine and Phase 11 compatibility regression | PASS |
| AC-24 | A corrupt actual latest snapshot cannot fall back to an older valid row. | `test_actual_latest_integrity_failure_does_not_fall_back` | PASS |
| AC-25 | Queue/list detail uses one query-only SQLite read transaction. | Research service implementation and shared-connection tests | PASS |
| AC-26 | Phase 11 comparison semantics are reused through `compare_with_connection`. | Research service and Phase 11 regression suite | PASS |
| AC-27 | Comparison results are query-time only and are not persisted. | Two-table migration/schema test | PASS |
| AC-28 | Review state is a dedicated typed state, separate from analysis/freshness/comparison. | Domain state tests and DTO tests | PASS |
| AC-29 | No visible snapshot maps to `no_snapshot` and delta availability is null. | `test_queue_no_snapshot_and_baseline_not_set` | PASS |
| AC-30 | Snapshot without review event maps to `baseline_not_set`. | `test_queue_no_snapshot_and_baseline_not_set` | PASS |
| AC-31 | Missing acknowledged baseline fails closed as `blocked`. | Research service state precedence | PASS |
| AC-32 | Baseline/latest integrity failure maps to `snapshot_integrity_error`. | Actual-latest integrity regression and Phase 11 integrity tests | PASS |
| AC-33 | Contract incompatibility maps to `incomparable_contract`. | Phase 11 compatibility regression | PASS |
| AC-34 | Blocked dependency state remains blocked. | Shared Phase 11 freshness mapping | PASS |
| AC-35 | Unknown dependency state remains unknown. | Shared Phase 11 freshness mapping | PASS |
| AC-36 | Comparable stored or current-context deltas map to `comparison_has_deltas=true`. | Domain tri-state test and Phase 11 delta regressions | PASS |
| AC-37 | Comparable empty delta sets map to `comparison_has_deltas=false`. | Domain tri-state test and AC-57 regression | PASS |
| AC-38 | Unavailable comparison is `null`, never false. | Domain tri-state test and queue-state tests | PASS |
| AC-39 | Cross-origin Research GET is rejected. | `test_research_get_origin_host_and_loopback_contract` | PASS |
| AC-40 | Origin-less GET is allowed only with loopback client and exact Host. | Research GET security test | PASS |
| AC-41 | Loopback is derived from ASGI client; forwarded headers do not grant trust. | Research GET security test and middleware contract | PASS |
| AC-42 | Host must exactly match a configured explicit-port authority. | Research GET security test | PASS |
| AC-43 | Missing or malformed application origin disables all Research routes. | `test_research_configuration_fails_closed` | PASS |
| AC-44 | Any malformed development origin fails the subsystem closed. | `test_research_configuration_fails_closed` | PASS |
| AC-45 | Research writes are disabled by default. | Research write security test | PASS |
| AC-46 | Writes require a present exact-allowlisted Origin. | Research write security test | PASS |
| AC-47 | Writes require JSON and a body no larger than 16 KiB. | Research write security test | PASS |
| AC-48 | CSRF uses a host-only HttpOnly Strict cookie and matching header token. | CSRF route and middleware tests | PASS |
| AC-49 | CSRF validity is strict before 1,800 seconds; exact expiry rejects before mutation. | `test_csrf_expiry_is_exact_and_capacity_is_bounded`; `test_expired_csrf_rejects_before_mutation` | PASS |
| AC-50 | CSRF capacity is bounded at 128 with fail-closed overflow. | Session-store bound and implementation gate | PASS |
| AC-51 | Browser code contains no admin secret and expiry requires explicit user retry. | Frontend source guard and mutation client test | PASS |
| AC-52 | Write DTOs reject extra fields and expose typed HTTP outcomes. | Phase 12 API tests | PASS |
| AC-53 | Page load, GET, inspection, comparison, or scrolling never auto-acknowledges. | `does not acknowledge on render or row inspection` | PASS |
| AC-54 | UI wording avoids ranking, recommendation, trade signal, and temporal-drift claims. | Frontend unit/Playwright wording guards | PASS |
| AC-55 | Backup/restore preserves counts, IDs, snapshot references, cutoffs, and FK integrity. | `test_phase12_backup` | PASS |
| AC-56 | v1 golden and Phase 8–11 behavior remain regression protected. | Full regression, v1 golden, Phase 8/10/11 focused commands | PASS |
| AC-57 | Same baseline/latest snapshot at the same cutoff may be stale while deltas remain false, with no temporal claim. | `test_ac57_same_snapshot_stale_is_false_not_temporal_change`; Playwright | PASS |

## Product boundary

No broker API, real account connection, real order, automatic trading, stock ranking, recommendation, or Phase 13 feature was introduced.
