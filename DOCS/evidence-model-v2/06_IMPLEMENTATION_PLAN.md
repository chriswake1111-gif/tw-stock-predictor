# CODEX_IMPLEMENTATION_PLAN.md

## 0. 工作分支與變更策略

建立：

```text
feature/evidence-based-model-v2
```

原則：

- 不直接破壞舊 API。
- 不一次性重寫所有 Collector。
- 每一階段一個可審查 commit。
- 每階段完成後跑完整 pytest。
- 不修改真實下單邊界。
- 不將研究報告直接轉成程式常數。

---

## Phase 0：基準快照與清理

### 目標

建立修改前基準，清除 Repository 污染。

### 工作

- 記錄目前 CI、測試數量與 API fixture。
- 從 Git 移除：
  - `data/cache.db`
  - `__pycache__/`
  - `*.pyc`
- 更新 `.gitignore`。
- 將目前模型標記為 `legacy_model_version=1.x`。
- 建立 API golden snapshot，避免 v1 被意外破壞。

### 驗收

- v1 測試維持通過。
- Repository 不再追蹤 runtime cache。

---

## Phase 1：證據規則註冊與命名修正

### 新增

```text
config/model_rules.yaml
src/domain/evidence.py
src/domain/model_status.py
src/services/rule_registry.py
tests/test_rule_registry.py
```

### 修改

- `src/api/main.py`
  - title 改為中性名稱。
  - 回傳 `official_affiliation=false`。
  - v1 標記 legacy。
- `src/engine/ma_deduction.py`
  - 文件與類別說明改為通用技術工具。
  - `detect_resonance_signal` 標記 U／experimental。
- `src/engine/valuation_eva.py`
  - 移除「杜金龍 EVA 引擎」宣稱。
  - EVA 設為 unsupported。
- `config/config.yaml`
  - 刪除殘留 `default_m1b_billion`。
  - 刪除固定 2% 頭部閾值的核心含義。
  - 將硬編碼波浪點改為 `legacy_example_anchors`。

### 驗收

- U 級規則不能進入 verified core。
- API 可顯示每個規則的 evidence level。

---

## Phase 2：Forward EPS 與 PE 情境

### 新增

```text
src/domain/valuation.py
src/repositories/forward_eps_repository.py
src/services/forward_eps_service.py
src/engine/forward_pe_valuation.py
src/api/routes/v2_valuation.py
tests/test_forward_pe_valuation.py
tests/test_forward_eps_asof.py
```

### 資料庫 migration

- `forward_eps_observations`
- `pe_scenarios`

### 修改

- `src/api/main.py` 保留 v1。
- 新增 `/api/v2/analysis/{symbol}`。
- 現有 `historical_ttm` 只作參考。
- 刪除 v2 的 `default_eps_growth` fallback。

### 驗收

- 無 Forward EPS 時 v2 valuation 為 insufficient_data。
- 未來資料不會進入過去分析。
- EPS×PE 矩陣可重現。

---

## Phase 3：M1B 流動性 v2

### 新增／重構

```text
src/engine/liquidity.py
src/services/market_liquidity_service.py
tests/test_m1b_asof_alignment.py
tests/test_turnover_m1b_ratio.py
```

### 修改

- `src/engine/market_sentiment.py`
  - 保留 legacy wrapper 或改為呼叫 v2。
- `src/collectors/cbc_collector.py`
  - 確認 period、available_at、revision。
- `src/collectors/market_turnover_collector.py`
  - 確認 TWSE、TPEx 都有 scope 與來源。

### 行為

- 輸出 ratio、rolling mean、historical percentile。
- 3.3%～3.4% 為 reference case。
- 不產生自動賣出。

### 驗收

- 通過單位與日期對齊測試。
- 沒有固定 2.5 兆與 fallback。

---

## Phase 4：人工錨點、費氏情境與版本控制

### 新增

```text
src/domain/anchors.py
src/repositories/anchor_repository.py
src/services/anchor_service.py
src/engine/fibonacci_scenarios.py
src/engine/fibonacci_time_windows.py
src/api/routes/v2_anchors.py
tests/test_anchor_versioning.py
tests/test_fibonacci_scenarios.py
tests/test_month_windows.py
```

### 重構

- `src/engine/wave_fibonacci.py`
  - 保留 `detect_confirmed_pivots` 作候選器。
  - 移除自動候選＝已確認杜氏波浪的語意。
  - 0.382、等幅、倍率必須引用 approved anchor set。
  - 時間窗改用月數。
  - 1.382、2.618、3.236 等沒有研究支持的預設倍率移至 experimental config。

### 驗收

- 無人工 approval 不生成 verified wave target。
- Prefix Invariance 通過。
- 重標不覆寫舊結果。

---

## Phase 5：資金部署與 Backtester 隔離

### 新增

```text
src/strategy/three_tranche_planner.py
src/strategy/signal_driven_backtester.py
tests/test_three_tranche_planner.py
tests/test_no_fourth_entry.py
```

### 修改

- 現有 `TuStrategy` 更名或標記：
  - `LegacyExperimentalStrategy`
- 固定 20／30／50 與 7%～11% 不得出現在 v2。
- v2 Backtester 接收外部 signal timeline：
  - entry trigger
  - invalidation trigger
  - approved anchor／scenario IDs
- 三等份只負責資金預算與最多三筆限制。

### 驗收

- 三筆各約三分之一。
- 第四筆被拒絕。
- 不因 MA resonance 自動當成杜氏交易訊號。

---

## Phase 6：二低一高篩選

### 新增

```text
src/engine/value_screening.py
src/services/security_screening_service.py
tests/test_two_lows_one_high_percentiles.py
```

### 行為

- 使用產業／自身歷史分位。
- EPS growth 非負。
- 技術轉強為獨立、可替換 component。
- 顯示分項，不只輸出 Boolean。

### 驗收

- 不再使用硬編碼 PB 1.5、yield 4% 當本人固定門檻。
- 低 PE 虧損公司不會自動通過。

---

## Phase 7：情境整合與不可變快照

### 新增

```text
src/domain/analysis_snapshot.py
src/repositories/analysis_snapshot_repository.py
src/services/evidence_analysis_service.py
src/engine/target_confluence.py
src/api/routes/v2_analysis.py
tests/test_snapshot_immutability.py
tests/test_target_independence.py
```

### 行為

- 大盤與個股流程分開。
- 多方法目標只做 overlap。
- 共享同一依賴不得重複加分。
- 輸出 evidence strength，不輸出偽機率。
- 每次分析保存快照。

### 驗收

- 任一歷史 snapshot 可重現。
- 重標、PE 修改或 EPS 更新建立新版本。

---

## Phase 8：預測績效驗證

### 新增

```text
src/evaluation/prediction_evaluator.py
src/evaluation/walk_forward.py
tests/test_prediction_expiry.py
tests/test_mfe_mae.py
tests/test_modified_prediction.py
```

### 指標

- 期限內目標區命中。
- MFE。
- MAE。
- 是否中途修改。
- 失效條件觸發時間。
- 樣本外結果。
- 與買入持有及簡單基準比較。

### 驗收

- 尚未到期不得算成功。
- 日後碰到目標不自動補算命中。
- 修改前後版本分開評估。

---

## Phase 9：PWA／報告層

核心完成後才開始：

- 大盤總覽。
- 個股估值矩陣。
- M1B 流動性。
- 人工錨點編輯器。
- 樂觀／基準／保守情境。
- 規則證據展開。
- 分析版本比較。
- 資料缺失與更新狀態。

不得在 Phase 1～8 未完成前，優先花大量時間美化 UI。

---

## 建議 Commit 順序

1. `chore: establish legacy baseline and repository hygiene`
2. `feat: add evidence rule registry and model status contracts`
3. `feat: add forward EPS and PE scenario valuation`
4. `feat: add as-of aligned turnover to M1B liquidity model`
5. `feat: add versioned manual anchors and fibonacci scenarios`
6. `refactor: isolate legacy allocation strategy`
7. `feat: add verified three-tranche deployment planner`
8. `feat: add percentile-based value screening`
9. `feat: add evidence analysis snapshots and confluence`
10. `feat: add walk-forward prediction evaluation`
11. `docs: finalize v2 model evidence and limitations`

## Codex 每階段回報格式

```text
Assessment
Changed Files
Behavior Before / After
Rule IDs Implemented
Evidence Levels
Data Contract Changes
Migration
Tests Executed
Test Results
Remaining Limitations
Legacy Compatibility
Product Boundary Check
```
