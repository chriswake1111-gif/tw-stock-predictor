# Phase 11 Acceptance Evidence

Date: 2026-08-13

This matrix preserves the approved Phase 11 AC-01 through AC-26 wording. It does
not renumber implementation evidence into a replacement acceptance contract.

| AC | Approved Requirement | Implementation Evidence | Test Evidence | Result |
|---|---|---|---|---|
| AC-01 | 同 symbol、model version、capture mode 的 snapshots 可通過 gate。 | Read-time `analysis_snapshot_v1` validator and compatibility gate | `test_compatibility_gate_reasons_are_deterministic` | PASS |
| AC-02 | 不同 symbol 回傳 `incomparable_contract/different_symbol`。 | Compatibility reason mapping | `test_incompatible_contract_returns_no_deltas_or_current_context` | PASS |
| AC-03 | 不同 model version fail closed。 | Model-version gate after contract validation | `test_compatibility_gate_reasons_are_deterministic` | PASS |
| AC-04 | 不同 capture mode fail closed。 | Capture-mode gate | `test_compatibility_gate_reasons_are_deterministic` | PASS |
| AC-05 | 缺少 cutoff 為 request validation failure。 | Required GET query parameter | `test_static_compare_route_required_params_timezone_and_utc` | PASS |
| AC-06 | cutoff 無 timezone 被拒絕。 | Strict timestamp canonicalization | `test_static_compare_route_required_params_timezone_and_utc` | PASS |
| AC-07 | snapshot hash 失敗時不產生 semantic deltas。 | Typed integrity error before comparator | `test_integrity_failure_is_server_error_for_compare_and_detail` | PASS |
| AC-08 | stored deltas 只使用 taxonomy v1。 | `StoredChangeType` whitelist; no recursive fallback | `test_valuation_target_support_screening_liquidity_confluence_and_deployment` | PASS |
| AC-09 | Forward EPS／PE revision 與 approval references 可追溯。 | Dependency revision and approval-reference deltas; logical valuation identity | `test_resource_revision_approval_profile_and_rule_changes_are_explicit`, `test_valuation_revision_ids_do_not_change_logical_cell` | PASS |
| AC-10 | FB-03 永遠呈現 target，FB-04 永遠呈現 support。 | Semantic-role technical registry | Phase 9 frontend contract plus `test_technical_anchor_and_price_changes_have_separate_taxonomy` | PASS |
| AC-11 | higher／lower target 不產生方向語意。 | Neutral numeric before/after and absolute delta | Comparator taxonomy tests | PASS |
| AC-12 | screening pass／fail 只顯示 before／after。 | Screening result delta has no recommendation semantics | Comparator and frontend tests | PASS |
| AC-13 | confluence clusters 不排名、不選最佳。 | Cluster-ID add/remove/change only | Comparator confluence tests | PASS |
| AC-14 | current approval revoke 不改寫 stored approval。 | Stored/current arrays remain separate | `test_current_context_taxonomy_is_neutral_and_explicit` | PASS |
| AC-15 | 兩邊 freshness 使用同 cutoff 及同 DB read snapshot。 | One caller-owned query-only SQLite transaction | `test_service_comparison_is_deterministic_read_only_and_has_no_public_hash` | PASS |
| AC-16 | unknown／blocked reasons 完整保留。 | Separate current contexts and reasons | Service current-context tests; M1B lifecycle regression | PASS |
| AC-17 | same snapshot comparison deltas 為空。 | Deterministic self-comparison | `test_same_snapshot_has_zero_stored_and_current_context_deltas` | PASS |
| AC-18 | 相同輸入與 DB state 產生相同 canonical response。 | Stable canonicalization and delta sorting | Service determinism and set canonicalization tests | PASS |
| AC-19 | comparison GET 不造成 database row count 改變。 | GET-only query path with `PRAGMA query_only=ON` | `test_service_comparison_is_deterministic_read_only_and_has_no_public_hash` | PASS |
| AC-20 | Phase 8 evaluator及資料完全不變。 | No evaluator, profile, outcome or migration changes | Full regression suite | PASS |
| AC-21 | Frontend 不實作自己的 diff。 | UI renders server-provided `stored_deltas` and `current_context_deltas` | Frontend unit and governance tests | PASS |
| AC-22 | UI 清楚分隔 stored facts 與 current context。 | Separate workspace sections | Frontend unit and Playwright visual tests | PASS |
| AC-23 | UI 無 bullish／bearish／confidence／buy／sell 字樣。 | Neutral comparison copy | Frontend content-governance tests | PASS |
| AC-24 | 沒有 migration。 | Remediation reuses existing Phase 10 tables | Git diff scope and migration tests | PASS |
| AC-25 | 既有 v1／v2 API regression 通過。 | Additive comparison behavior | Full Python regression and v1 golden | PASS |
| AC-26 | 沒有新增 broker API 或真實交易能力。 | Read-only research scope | Product-boundary/runtime-cache gates | PASS |

## First review remediation evidence

- Unsupported or malformed stored snapshots return `incomparable_contract` with
  no stored or current-context deltas.
- Valuation revision IDs are not semantic cell identity.
- Technical anchor and calculated range changes use separate taxonomy.
- M1B current context uses the same exact publication-evidence binding helper as
  the Phase 10 eligible M1B query.
- Public comparison references and nested comparison contexts omit snapshot hashes.
- Set-semantic canonicalization deduplicates canonical nested values.

## Validation commands

Final local and CI counts are recorded in the Draft PR and delivery report:

```text
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider tests/test_api_golden.py
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
npm.cmd run test:visual
git diff --check
```

## Recorded local results

- Second-review focused Phase 11 backend: 42 passed.
- Full Python regression: 426 passed, with one existing TestClient deprecation warning.
- v1 API golden snapshot: 2 passed, with the same existing warning.
- Frontend unit suite: 19 passed.
- Frontend lint, typecheck and production build: passed.
- Playwright visual suite: 4 passed across desktop and mobile projects. Browser
  launch required execution outside the filesystem sandbox; no application or
  external state was modified.

Second-review regression evidence additionally covers blocked and missing
dependencies retaining stable logical identity, a fail-closed stored/DB logical
identity mismatch, one M1B period transitioning from blocked M1 to explicitly
bound M2 without physical remove/add identity artifacts, and authoritative
`CaptureMode` enum validation.

## Runtime artifact gate

Tracked cache and bytecode paths are checked with `git ls-files`. Local build,
Playwright, test-database and cache artifacts remain untracked or ignored.
