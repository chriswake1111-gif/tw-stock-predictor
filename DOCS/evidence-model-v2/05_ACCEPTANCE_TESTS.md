# ACCEPTANCE_TESTS.md

## Phase 3 additions

Acceptance includes unit conversion, explicit CBC release timestamp, M1B revision visibility, revoked revision behavior, both-market completeness, partial data, exact 20／60-observation rolling means, deterministic `<=` percentile ties, insufficient history, 3.3%～3.4% reference-only alert, LIQ-03 exclusion, symbol-invariant API output, section independence, clean／rerunnable migration, full regression and unchanged v1 golden snapshots.

## A. 規則治理

1. `U` 級規則不能被註冊為 `verified_core`。
2. `U` 級結果不能影響 `evidence_strength`。
3. 需人工核准的規則沒有 approval ID 時，回傳 `needs_human_input`。
4. 每個輸出均可追溯 Rule ID 與版本。
5. 舊規則版本不可被覆寫。

## B. Forward EPS

1. 沒有 Forward EPS 時，不得使用 TTM EPS 代替。
2. 固定 10% 成長率不得產生 `forward_eps`。
3. 同一年度多來源資料需保留來源、日期與修訂。
4. `available_at > as_of_date` 的預估不可使用。
5. Target Price 必須等於 EPS × PE。
6. 不同股票不可未經核准共用同一 PE。
7. 歷史 TTM 只能出現在 `historical_ttm_reference`。

## C. M1B

1. TWSE 與 TPEx 都可用時才計算完整上市櫃比率。
2. 單位全部轉成 TWD。
3. 交易日使用當時已公布的最新 M1B。
4. 未來月份 M1B 不得回填。
5. 3.3%～3.4% 只觸發參考警示，不產生 sell。
6. 固定 2.5 兆設定不存在。
7. 缺 M1B 時回傳 `insufficient_data`，不得 fallback。

## D. 三等份

1. 權重總和為 1。
2. 最多三筆。
3. 第四筆被拒絕。
4. 三筆預算皆以同一 campaign base 計算。
5. 買點間距缺失時只產生 planner draft，不可下單。
6. 20／30／50 不得出現在 v2 verified planner。

## E. 波浪與錨點

1. Pivot candidate 具 `confirmed_at`。
2. Prefix Invariance：加入未來資料不改變已發布 pivot。
3. 未核准 anchor 不可生成 verified wave target。
4. 重標建立新 anchor set，舊 set 保留。
5. Hindsight extrema 不得進入 realtime response。
6. 自動候選不得宣稱唯一浪級。

## F. 0.382 與等幅

1. 0.382 公式以人工核准 A、B 計算。
2. A/B 時序錯誤時拒絕。
3. 等幅 A/B/C 時序錯誤時拒絕。
4. 錨點版本改變時產生新 snapshot。
5. 計算結果附 formula、anchor IDs、as_of_date。
6. market date 早於人工 available_at 時，歷史 cutoff 不可提前看見 anchor。
7. 新 revision 未重新核准時，不得沿用舊 revision approval。
8. 最新 anchor 或 approval revoked 時，不得回退舊版。
9. FB-03 保留 `B-A` 方向，向上與向下範例均 deterministic。
10. FB-04 只接受規格明定的 `B>A` 上升波；向下關係 fail closed。
11. 0.618、1.618 或 FB-05 不得進入 Phase 4 verified scenario。
12. 2330 的 anchor 不得被 2317 使用。
13. FB-03 輸出 `formula=equal_move`，FB-04 輸出 `formula=retracement_0382`；數學式另存 `formula_expression`。
14. 無 anchor、未核准與 revoked approval 必須映射到 top-level human-input requirement；有效 scenario 時不得殘留。

## G. 時間窗

1. 預設以曆月計數，不是交易日。
2. 13／21／34／55／89 可配置。
3. 只輸出提醒，不輸出必然反轉。
4. 起點重標後舊提醒仍可重現。
5. ± tolerance 可配置並寫入快照。

## H. 二低一高

1. 門檻使用分位數或明確配置。
2. 顯示每一分項數據與分位數。
3. EPS 負成長時不得只因低 PE 判定通過。
4. 固定 `PB < 1.5`、`yield > 4%` 不得標成已驗證本人門檻。
5. 技術轉強模組需標示證據等級。

## I. API

1. 每次 response 有 model version。
2. 每個 section 有 status。
3. 不支援 EVA 時回傳 unsupported。
4. Partial data 不造成 500。
5. 所有預測區間附失效條件或 `not_defined`。
6. 不出現保證性文字。
7. `/api/v1` 與 `/api/v2` 可並存直到遷移完成。

## J. 歷史驗證

1. 預測保存發布日、期限、失效條件與版本。
2. 尚未到期不得算命中。
3. 中途重標必須標記 modified。
4. 同時計算 MFE、MAE、期限內命中。
5. 不能只因日後某天碰到目標就算成功。
6. 樣本外與 walk-forward 使用 `available_at` 防止資料洩漏。

## Phase 5 acceptance tests

1. Divisible and non-divisible capital produce three budgets with exact weight and budget sums.
2. Zero, negative, NaN, infinity, stage skips, and stage 4 fail closed.
3. Trigger source classifications and Phase 4 FB-04 reference IDs remain traceable.
4. Draft, post-cutoff, revoked, and superseded plan approvals cannot appear available.
5. Valid approval produces an exact A-level ENT-02 Rule Trace for that revision only.
6. OHLCV patterns without external events produce zero v2 entries.
7. External stages 1-3 execute on the next bar; a fourth entry never executes.
8. Invalidation terminates one campaign and rejects later entries for it.
9. Simulation is offline, deterministic, and reports `automatic_order=false`.
10. Phase 2-4 regressions and the unchanged v1 golden snapshot remain green.

## K. Repository Hygiene

1. `data/cache.db` 不得提交版本控制。
2. `__pycache__`、`.pyc` 不得提交。
3. CI 必須驗證上述規則。
4. 測試不得依賴即時網路。
5. 官方 API response 使用固定 fixtures。
