# Phase 12 Acceptance Evidence

本表逐條保留已核准且唯一權威的 Phase 12 AC-01～AC-57 語意。`PASS` 只表示列出的自動化測試、schema gate 或可重現建置檢查已實際覆蓋該契約；不代表系統無其他限制。

| AC | Approved Requirement | Implementation / Test Evidence | Status |
|---|---|---|---|
| AC-01 | 合法symbol可新增watchlist membership。 | `canonical_research_symbol`; repository/API lifecycle tests | PASS |
| AC-02 | `2330`與`2330.TW`解析為相同canonical identity。 | `test_phase12_symbol_identity_is_canonical_and_otc_is_explicit`; duplicate API test | PASS |
| AC-03 | 明確`.TWO`保留；系統不自行推測OTC。 | Domain canonical identity test; DB canonical constraint test | PASS |
| AC-04 | 非法symbol fail closed。 | Domain invalid-symbol assertion; strict API DTO | PASS |
| AC-05 | 重複add不建立duplicate membership。 | Repository/API duplicate lifecycle tests | PASS |
| AC-06 | archive保留membership與review history。 | Repository lifecycle and backup/restore tests | PASS |
| AC-07 | unarchive復用原watchlist item。 | Repository lifecycle test | PASS |
| AC-08 | hard delete在application與database皆被拒絕。 | No delete repository surface; migration trigger test | PASS |
| AC-09 | empty watchlist回`available`及`items=[]`。 | `test_phase12_api_empty_queue_and_membership_lifecycle` | PASS |
| AC-10 | symbol沒有snapshot時回`review_state=no_snapshot`，且不自動refresh。 | `test_queue_no_snapshot_and_baseline_not_set`; no-auto-ack frontend test | PASS |
| AC-11 | 沒有review event時回`baseline_not_set`及`comparison_has_deltas=null`。 | Queue state test; corrupt-latest precedence regression | PASS |
| AC-12 | acknowledgment精確綁定指定snapshot ID。 | Repository/API acknowledgment tests | PASS |
| AC-13 | cross-symbol acknowledgment被拒絕。 | `test_cross_symbol_acknowledgment_rejected_and_new_event_preserves_history` | PASS |
| AC-14 | review event為append-only。 | DB update/delete trigger assertions | PASS |
| AC-15 | 新acknowledgment不覆寫舊event。 | Two-event history-preservation regression | PASS |
| AC-16 | `reviewed_at`由server產生。 | Service/API captured request-time path; backup round-trip assertion | PASS |
| AC-17 | review comparison cutoff由request明確提供、normalize UTC並保存。 | Domain/API acknowledgment and backup tests | PASS |
| AC-18 | `reviewed_at`與`comparison_cutoff_at`維持獨立欄位。 | Migration schema and backup round-trip test | PASS |
| AC-19 | naive review/query timestamp、future review cutoff及future query cutoff均回HTTP 422。 | `test_phase12_api_typed_cutoff_and_strict_schema_errors`; acknowledgment cutoff regression | PASS |
| AC-20 | query cutoff早於latest review event cutoff時回HTTP 422。 | API `comparison_cutoff_before_review_baseline` regression | PASS |
| AC-21 | latest snapshot依symbol、query cutoff、created_at及snapshot_id deterministic選取。 | Latest ordering and same-time tie regressions | PASS |
| AC-22 | incompatible actual latest snapshot不得退回compatible次新snapshot。 | `test_incompatible_latest_is_used_without_compatible_fallback` | PASS |
| AC-23 | actual latest snapshot integrity failure不得退回次新snapshot。 | `test_actual_latest_integrity_failure_does_not_fall_back` | PASS |
| AC-24 | acknowledged snapshot missing時回blocked，且不得替換baseline。 | Missing-baseline state regression | PASS |
| AC-25 | baseline或latest snapshot integrity failure回`snapshot_integrity_error`。 | `test_baseline_integrity_failure_and_missing_baseline_fail_closed` baseline-only path with a newer valid latest; `test_actual_latest_integrity_failure_does_not_fall_back` actual-latest path | PASS |
| AC-26 | comparison只能重用Phase 11 `SnapshotComparisonService`。 | `compare_with_connection`; hidden-connection regression; Phase 11 compatibility suite | PASS |
| AC-27 | comparison result、deltas、counts及`comparison_has_deltas`均不得持久化。 | Two-table migration schema; summary/detail query-time derivation test | PASS |
| AC-28 | stored delta存在時`comparison_has_deltas=true`。 | Typed comparison state matrix regression | PASS |
| AC-29 | current-context delta存在時`comparison_has_deltas=true`。 | Typed comparison state matrix regression | PASS |
| AC-30 | comparable且兩類delta皆空時`comparison_has_deltas=false`。 | Domain tri-state and AC-57 regressions | PASS |
| AC-31 | same snapshot、same query cutoff、comparable且無delta時`comparison_has_deltas=false`。 | `test_ac57_same_snapshot_stale_is_false_not_temporal_change` | PASS |
| AC-32 | no snapshot、baseline not set、missing baseline、incomparable、blocked、unknown、integrity failure或comparison unavailable時`comparison_has_deltas=null`。 | Queue precedence, missing/incompatible/integrity and typed state matrix tests | PASS |
| AC-33 | stale維持獨立freshness語意，不自動形成delta、blocked或temporal-change宣稱。 | AC-57 service, frontend wording and Playwright regressions | PASS |
| AC-34 | dependency blocked形成`review_state=blocked`。 | Typed comparison state matrix regression | PASS |
| AC-35 | dependency unknown形成`review_state=unknown`。 | Typed comparison state matrix regression | PASS |
| AC-36 | page open、GET、scroll或comparison完成均不得自動acknowledge。 | `does not acknowledge on render or row inspection`: real queue row, detail GET, scroll, zero acknowledgment POST, then one explicit-click positive control | PASS |
| AC-37 | workflow metadata不進Evidence Grade、Rule Trace、模型輸出、screening或historical performance。 | Governance product-boundary/static contract guard | PASS |
| AC-38 | Research write API預設disabled並回HTTP 503。 | Research write security test | PASS |
| AC-39 | 帶有non-allowlisted Origin的Research browser GET回HTTP 403。 | Research GET Origin security test | PASS |
| AC-40 | 非loopback、invalid Host、non-allowlisted Origin、invalid CSRF、invalid content type或oversized body的Research write依typed contract拒絕。 | Research workflow security matrix；partial body後`http.disconnect`直接終止且不呼叫downstream | PASS |
| AC-41 | frontend source及production bundle均不包含Evidence admin API key。 | Source guard; production `build` bundle scanner | PASS |
| AC-42 | duplicate acknowledgment同key同semantic payload回原event。 | Repository/API idempotency tests；公開ack/queue/detail DTO不含raw key，DB仍保留key | PASS |
| AC-43 | 相同idempotency key搭配不同payload回HTTP 409。 | Repository/API conflict tests | PASS |
| AC-44 | duplicate archive/unarchive為deterministic idempotent operation。 | Repository/API tests驗證首次transition為`changed=true`、重試no-op為`changed=false`且timestamp/history不變 | PASS |
| AC-45 | backup/restore保留membership、archive state、review history及idempotency behavior。 | Phase 12 backup/restore round-trip test | PASS |
| AC-46 | database failure不得偽裝成empty queue。 | `test_phase12_api_database_failure_is_not_an_empty_queue` | PASS |
| AC-47 | list只回summary/count；detail才可回完整Phase 11 comparison。 | Summary/detail service regression | PASS |
| AC-48 | list maximum 50，並使用同一SQLite read snapshot。 | API limit test; counted single read connection and hidden-connection guard | PASS |
| AC-49 | frontend不得使用ranking、recommendation、temporal-change或directional investment語彙。 | Frontend wording unit/Playwright and governance guards | PASS |
| AC-50 | desktop、mobile、keyboard及accessibility驗證通過。 | Playwright desktop/mobile; keyboard unit; axe accessibility tests | PASS |
| AC-51 | v1 API golden維持不變。 | `tests/test_api_golden.py` | PASS |
| AC-52 | Phase 8–11既有regression維持。 | Full pytest and Phase 11 focused compatibility suite | PASS |
| AC-53 | 不新增Journal、price、notification、LLM、broker或trading功能。 | Product-boundary/governance test and scoped route inventory | PASS |
| AC-54 | migration additive、transactional、rerunnable且rollback-safe。 | Phase 12 fresh/rerun and rollback tests | PASS |
| AC-55 | CSRF session在1,800秒TTL到期後回HTTP 403 `csrf_session_expired`，mutation不得執行。 | Exact-expiry and zero-mutation security regression | PASS |
| AC-56 | 無Origin的loopback＋valid Host request可讀；無Origin的non-loopback request回HTTP 403。 | Research GET loopback/no-Origin security test | PASS |
| AC-57 | same snapshot在同一query cutoff下即使freshness為stale，無delta時仍回`comparison_has_deltas=false`及`freshness_status=stale`，且不得宣稱T1至T2沒有變化。 | Dedicated AC-57 service test and Playwright temporal-wording guard | PASS |

## Product boundary

No Journal, current-price workflow, notification, LLM, broker API, real account connection, real order, automatic trading, ranking, recommendation, or Phase 13 feature was introduced.
