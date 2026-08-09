# DATA_REQUIREMENTS.md

## Phase 3 additive liquidity contracts

`cbc_m1b_monthly` preserves CBC EF15M01 raw values (`TWD_million`) and normalized `value_twd`, with period, data date, explicit official-release-derived `available_at`, fetch／ingest timestamps, source metadata, immutable revision and status. Unknown historical release time is `needs_human_input`; it is never inferred from fetch time.

`market_turnover_daily` preserves TWSE `exchangeReport/FMTQIK.TradeValue` and TPEx `tpex_daily_trading_index.TradeAmount` independently in TWD. A total is present only when both markets are present; otherwise the record is `partial`.

## 1. 核心資料表

### 1.1 `forward_eps_observations`

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | TEXT PK | 唯一 ID |
| symbol | TEXT | 股票代號 |
| fiscal_year | INTEGER | 預估年度 |
| eps_low | REAL NULL | 保守值 |
| eps_base | REAL | 基準值 |
| eps_high | REAL NULL | 樂觀值 |
| source_name | TEXT | 來源 |
| source_type | TEXT | broker/company/consensus/manual |
| published_at | DATE | 發布日期 |
| available_at | DATETIME | 系統當時可使用時間 |
| analyst_count | INTEGER NULL | 樣本數 |
| quality_note | TEXT NULL | 備註 |
| fetched_at | DATETIME | 收集時間 |
| revision_of | TEXT NULL | 修正版來源 |

### 1.2 `pe_scenarios`

- symbol_scope
- scenario label
- pe value
- rationale
- evidence level
- approved_by／approved_at
- effective_from／effective_to
- version

### 1.3 `manual_anchor_sets`

保存人工錨點、波浪標籤、市場階段、理由與 supersedes 關係。

### 1.4 `market_turnover_daily`

- trade_date
- twse_turnover_twd
- tpex_turnover_twd
- total_turnover_twd
- source datasets
- available_at
- status

### 1.5 `cbc_m1b_monthly`

- period
- value_twd
- data_date
- available_at
- source
- revision
- status

### 1.6 `analysis_snapshots`

不可變分析快照。

### 1.7 `model_rule_registry`

Rule ID、證據層級、模式、版本與禁止用途。

### 1.8 `prediction_evaluations`

- snapshot_id
- target range
- valid_from／valid_until
- invalidation conditions
- reached_at
- MFE
- MAE
- modified_before_expiry
- objective_result

---

## 2. 資料時間語意

每筆資料至少包含：

```text
period / data_date
available_at
fetched_at
source
revision
unit
status
```

### 2.1 Look-ahead Guard

分析 `as_of_date=T` 時：

```sql
WHERE available_at <= T
```

### 2.2 財報

- 單季 EPS、TTM EPS 與 Forward EPS 必須分表或分型別。
- `historical_ttm` 不得轉型為 `forward_consensus`。
- 修正版不得覆寫原始版本，需以 revision 關聯。

---

## 3. Forward EPS 來源策略

v2 初期允許：

1. 使用者手動輸入研究報告中的未來 EPS。
2. 經授權 API 的共識 EPS。
3. 公司法說／財測。
4. 多來源匯入後由使用者核准。

不允許：

- 使用股價 ÷ PE 回推 EPS。
- TTM EPS 固定成長率自動當作法人預估。
- 沒有來源日期的數值。

---

## 4. M1B 對齊規則

- 儲存原始單位與標準化 TWD。
- 日成交額與 M1B 轉為相同 TWD 單位。
- 每個交易日使用當時已公布的最新 M1B。
- 如果公布日未知，該期間不得進入歷史回測核心。
- API 顯示 `m1b_period` 與 `m1b_available_at`。

---

## 5. 資料狀態

統一狀態：

```text
available
stale
partial
needs_human_input
insufficient_data
unsupported
experimental
```

禁止用 0、固定值或上一筆無標記數據冒充最新值。

---

## 6. Collector 邊界

Collector 只負責：

- 下載。
- 原始解析。
- 單位轉換。
- 日期語意。
- 寫入資料庫。
- 回傳 Data Contract。

Collector 不負責：

- 判斷波浪。
- 選 PE。
- 產生交易訊號。
- 編寫自然語言結論。

---

## Phase 4 additive manual-anchor contract

`technical_anchor_revisions` 保存人工輸入的 anchor set revision。每組只屬於一個 symbol 與一個 evidence basis Rule ID；角色明確映射為 `origin (A)`、`swing_end (B)`，FB-03 另需 `projection_origin (C)`。價格單位固定為 `TWD_per_share`，anchor type 只允許不暗示市場結構的 `manual_price_anchor`。

Revision 採 append-only，`logical_anchor_set_id`、symbol、Rule ID 與單位不可跨 revision 改變。查詢同時要求 `available_at <= knowledge_cutoff_at` 與 `ingested_at <= knowledge_cutoff_at`，先選 cutoff 可見的最高 revision，再解讀 `available`／`revoked`；revoked 不得回退舊版。

`technical_anchor_approvals` 是 rule-specific immutable event。事件綁定特定 anchor revision；新 revision 不繼承舊 approval。查詢同時要求 `approved_at` 與 `ingested_at` 不晚於 cutoff。核准及撤銷事件不可回溯排序。
