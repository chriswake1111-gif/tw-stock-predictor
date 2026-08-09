# Evidence Model V2 Phase 3 — M1B 流動性

## 範圍與公式

```text
turnover_m1b_ratio_pct =
  (TWSE_turnover_TWD + TPEx_turnover_TWD) / M1B_TWD * 100
```

LIQ-01 是 C 級 `project_operationalization`；LIQ-02 是 A 級公開案例參考。LIQ-03 固定 2.5 兆與 LIQ-04 融資報酬率仍為 unsupported，不進入計算或 Rule Trace。

## 官方資料、單位與時間

- CBC Statistical Database API `EF15M01`：原始 M1B 單位為新台幣百萬元，乘 1,000,000 標準化為 TWD。
- TWSE OpenAPI `exchangeReport/FMTQIK.TradeValue`：TWD。
- TPEx OpenAPI `tpex_daily_trading_index.TradeAmount`：TWD。

原始值、原始單位、標準值、dataset、來源 URL／hash、抓取時間與 revision 分別保存。TWSE 與 TPEx 必須同時存在才計算 total 與 ratio；單一市場回傳 `partial`。

`available_at` 是資料當時可供系統使用的時間，`data_date` 只是資料所屬日期。查詢要求 `available_at <= knowledge_cutoff_at` 與 `ingested_at <= knowledge_cutoff_at`。revision 以不可變新增保存；歷史 cutoff 只能看見當時已公布且已匯入版本，revoked 最新版本不會退回舊值。

市場成交額的 `available`、`partial`、`revoked` 是明確且經驗證的 revision 狀態。查詢先選出每個 trade date 在 cutoff 當時的最新 revision，再解讀狀態；最新 revision 若 revoked，該日期不會回退到舊 available revision。cutoff 前最新 trade date 若為 partial，整個 liquidity section 維持 partial，舊完整資料只能以 `latest_complete_observation` 作歷史參考。遠端 TWSE／TPEx 任一來源失敗時保存另一側 partial，只有兩側皆失敗才回傳 `insufficient_data`。

CBC API 提供目前資料與 `last_updated`，但不提供每個歷史月份的精確公布 timestamp。歷史匯入必須由官方發布日曆／新聞稿建立明確 mapping；沒有 mapping 時回傳 `needs_human_input`，不以抓取時間或推定日期回填歷史。

## Rolling mean、percentile 與 alert

- 20／60 日均值只使用完整有效交易日；樣本不足回傳 `null` 和實際 count，不補零。
- 5／10 年百分位採 deterministic upper rank：`count(x <= current) / n * 100`；資料未覆蓋完整期間或少於最小樣本時回傳 `null`。
- 工程門檻預設 80th percentile `elevated`、95th percentile `historically_high`，可設定且不是杜金龍本人公開參數。
- 3.3%～3.4% 只標示 `2026 公開案例參考帶`／`reference_extreme_case`，不是統計分位、固定賣點、必然見頂或反轉保證。

本模組不輸出買賣訊號、`market_top`、真實委託或自動交易。

## Legacy 與限制

v1 `market_sentiment` 固定 threshold 保留供相容性，不進入 v2 verified output。v2 使用 additive tables `cbc_m1b_monthly` 與 `market_turnover_daily`，不破壞舊表。目前未內建歷史 CBC 發布 timestamp 全表，沒有受核對 mapping 時不能建立歷史核心結果。
