# 歷史研究資料契約

## 用途與邊界

本契約只適用於歷史研究、walk-forward 回測及虛擬資金配置，不得用於真實委託或保證報酬。

## 長期價格來源

- 供應路徑：Yahoo Finance，透過 `yfinance`。
- 日線參數：`interval=1d`、`auto_adjust=True`、`actions=False`、`repair=False`。
- `start` 為包含邊界；`end` 在 yfinance 為排他邊界，CLI 對使用者提供包含邊界並自動加一天轉換。
- yfinance 是非官方資料供應路徑。TWSE OpenAPI 可用於原始行情與交易日交叉查核，但不直接取代本研究所需的完整調整價序列。
- 參考文件：
  - https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html
  - https://openapi.twse.com.tw/
  - https://data.gov.tw/dataset/11549

## 可追溯欄位

每次 `--refresh` 報告必須保存：

- provider 與是否為官方交易所來源
- yfinance 版本、抓取時間、symbol、requested/actual range
- auto_adjust、actions、repair、interval 與 end 邊界語意
- provider payload SHA-256
- 標準化 CSV snapshot SHA-256
- 實際筆數、日期範圍及資料品質統計

供應商可能回溯修改調整價的小數或除權息因子。因此，可重現研究以報告內的 CSV snapshot 與 `normalized_snapshot_sha256` 為準，不以重新抓取結果相同作為前提。

## 交易日完整性與官方補值

- `volume <= 0` 的供應商列不得作為策略觀測 bar；清理數量與日期必須寫入 provenance。
- 個股日期須與同批 `^TWII` 參考交易日比對；缺日或曾移除零量列時標記 `quality_warning`，research gate 採 fail-closed。
- `tools/audit_twse_snapshot.py` 只讀取固定快照並與 TWSE 官方月成交資料比對，不得自動修改快照。
- 官方補值只能由預註冊 universe 指向的 `config/research_snapshot_overrides.yaml` 逐筆新增；不得覆寫既有日期。
- 每筆補值須保存 TWSE 原始 OHLCV、調整因子、因子推導方式、官方 URL 與補值列 SHA-256。
- 單月稽核通過不代表完整長期資料已驗證；在全期間官方日曆與公司行動契約完成前，不得用於策略升格。

## Walk-forward 契約

- `data-start` 至第一個驗證窗之間只作指標暖機。
- 每個驗證窗固定為 12 個月，資料不足 120 個交易日時不納入。
- 訊號使用當日收盤資訊，模擬成交最早為下一交易日開盤。
- 每個驗證窗重設模擬資金；彙總結果是窗別報酬複合，不等同單一帳戶連續持有報酬。

## 行情分層

以相同驗證窗內 `^TWII` 的實現報酬作事後分類：

- 牛市：大於或等於 15%
- 熊市：小於或等於 -15%
- 盤整：介於兩者之間

分類只用於結果報告，不得進入該驗證窗的交易訊號。

## 參數敏感度

- 比較 Stage 1 配置比例與浪 3 拉回區間的五個有限變體。
- 每個敏感度樣本只能使用對應驗證窗開始前的訓練資料。
- 敏感度結果不得自動選擇或套用「最佳參數」到驗證窗。
- 報告應揭露候選平均報酬範圍、正報酬候選比例與各候選訓練窗數。

## 重現指令

```powershell
python tools\run_backtest.py `
  --data-start 2014-01-01 `
  --start 2015-01-01 `
  --end 2026-07-31 `
  --cash 10000000 `
  --refresh `
  --run-id long-history-v1-20260801 `
  --json
```

重新抓取可能得到不同 hash；重現既有結果時應直接使用該報告目錄的 `data/*.csv`。

固定快照重播：

```powershell
python tools\run_backtest.py `
  --snapshot-dir reports\backtest\long-history-v1-20260801-eee3948d6fa6 `
  --data-start 2014-01-01 `
  --start 2015-01-01 `
  --end 2026-07-31 `
  --run-id adaptive-v1-1-20260801 `
  --json
```

CLI 會在執行前比對 `normalized_snapshot_sha256`；不相符時以 exit code 3 停止。

官方月成交稽核範例：

```powershell
python tools\audit_twse_snapshot.py `
  --snapshot-dir reports\backtest\expanded-phase-a-audited-v2-20260801-b8e7d89c9854 `
  --check 1301.TW:2025-08 `
  --check 2317.TW:2018-10
```

發現缺日或零量列時以 exit code 1 表示品質警告；完全對齊時回傳 0。

## TWSE 官方完整性層（Phase 1）

Phase 1 僅新增平行的官方原始事實表，不覆寫既有 `daily_ohlcv`，也不自動建立調整價：

- `twse_market_calendar`：TWSE `FMTQIK` 市場實際成交日與市場成交統計。
- `twse_daily_raw`：只針對快照缺口月份查詢 `STOCK_DAY` 的未調整個股日資料。
- `twse_corporate_actions`：`TWT49U` 除權息與 `TWTAUU` 減資事件。
- `twse_data_audit_runs`：稽核 scope、請求計畫、checkpoint、完成狀態與摘要。

所有表均保留來源 dataset、來源 URL、抓取時間與原始 payload SHA-256。相同 `audit_id` 只有在快照、日期範圍與標的完全相同時才能續跑；scope 不一致時必須停止，避免混合稽核紀錄。

```powershell
python tools\audit_twse_official_data.py `
  --snapshot-dir reports\backtest\expanded-phase-a-audited-v2-20260801-b8e7d89c9854 `
  --db-path data\cache.db `
  --start 2014-01-01 `
  --end 2026-07-31 `
  --dry-run
```

確認請求數後，將 `--dry-run` 改為 `--execute`，並指定可重用的 `--audit-id`。執行時預設每次官方請求間隔 150 ms；中斷後可用相同參數與 audit ID 續跑。exit code 1 表示完成稽核但仍有 `quality_warning`，不是抓取程序失敗。

2026-08-01 稽核 `twse-phase1-20260801` 的範圍為 2014-01-01 至 2026-07-31、14 檔 TWSE 標的：

- 官方市場成交日 3,068 日，407 個官方請求全部完成。
- 14/14 仍為 `quality_warning`，品質合格為 0/14。
- 多數標的缺少 12～13 個官方有成交日；主要型態包含台灣補行交易的星期六，顯示 yfinance 固定快照並非完整官方交易日集合。
- 2317 的 6 日無個股官方成交資料被分為停牌／未交易，不再誤判為供應商缺日。
- 006208 的 162 筆官方零量列被獨立分類；另有 32 筆雖有成交股數／金額，但官方 OHLC 為空，均不得當作有效 OHLCV 或以成交均價推測補值。
- 稽核前後既有 `daily_ohlcv` 均為 52,074 筆；以排序後 pipe 分隔、列間換行且結尾不加換行的 canonical bytes 計算，SHA-256 均為 `2a7e8411f8780906ec78bbba409e5a7dbdd20a0b88b363bf25382fa218a449c3`。

完整分類證據保存於 `official_integrity_audit_v1.json`。Phase 1 只回答「官方市場有無交易、個股有無官方成交列、是否為官方零量、附近有哪些已收錄公司行動」；ETF 分割／反分割、面額變更與調整價還原仍屬 Phase 2，不得由本層推算或自動修補。

## 官方缺日 adjusted snapshot（Phase 2）

Phase 2 使用 `provider_compatible_adjusted_v1` 契約，只處理 Phase 1 已確認為官方市場成交日、但固定 adjusted snapshot 缺少的日期：

- raw OHLCV 必須來自 `twse_daily_raw`；成交量維持官方原始股數。
- 調整因子只能取自同月份、同公司行動區段的 parent adjusted close／官方 raw close 錨點。
- 至少兩個錨點須在相對誤差 `1e-6` 內形成唯一最大共識群組；使用該群組因子的中位數。
- 錨點不足、共識平手、官方 OHLC 缺失或價量無效時回傳 `insufficient_data`，不得內插或用成交均價反推。
- 官方零量、停牌／未交易及 TWSE 非交易日只能在逐日證據存在時豁免 reference-day gate。
- 此契約是為了相容 parent yfinance `auto_adjust=True` 快照，不是 TWSE 官方調整價，也不是完整股利現金流／總報酬模型。

重建工具預設只讀來源與 SQLite 官方表，`--execute` 只建立新目錄：

```powershell
python tools\build_official_gap_snapshot.py `
  --source-snapshot reports\backtest\expanded-phase-a-audited-v2-20260801-b8e7d89c9854 `
  --audit-report reports\backtest\expanded-phase-a-audited-v2-20260801-b8e7d89c9854\official_integrity_audit_v1.json `
  --db-path data\cache.db `
  --output-root reports\backtest `
  --run-id phase2-gap-adjusted-v1-20260801 `
  --dry-run
```

2026-08-01 正式結果：236 個缺口中 202 筆通過契約並新增，34 筆 fail-closed；12/14 標的通過官方完整性 gate。`006208.TW` 保留 33 筆不可重建資料，`2308.TW` 保留 1 筆調整因子歧義。新快照為 `phase2-gap-adjusted-v1-20260801-ec781a02134f`；source snapshot 不變。完整逐列 ledger 保留於本機並由 manifest SHA-256 固定，Git 只保存必要摘要、未解決清單與 provenance。

## Evidence Model v2 Forward EPS 與 PE（Phase 2）

- 所有歷史知識查詢使用完整 `knowledge_cutoff_at`，並正規化為 UTC ISO-8601。
- API 只有日期時採 `00:00:00 Asia/Taipei`，不得採當日結束時間；回應必須揭露 cutoff policy。
- Forward EPS 同時以 `available_at <= cutoff` 與 `ingested_at <= cutoff` 限制可見性。
- `logical_series_id`、`revision_number`、`revision_of` 形成不可變修訂鏈，歷史查詢取得當時最新可用版本。
- 多個來源不得自動平均；每筆 observation 分別產生矩陣，矩陣格保存 observation ID。
- `historical_ttm` 不是允許的 Forward EPS source type，且不得進入 Forward PE 矩陣。
- PE scope 明確分為 `symbol`、`industry`、`market`；verified matrix 只自動使用該股票已核准的 symbol PE。
- draft、revoked、未生效或已失效的 PE 不得進入矩陣。
- Forward EPS 與 PE 必須先經受保護的人工作業核准；矩陣與 Rule Trace 保存 approval ID。U 級一律 fail-closed，C 級標示 project operationalization。
- 寫入 API 預設關閉，啟用後仍須管理 API key；匯入只能建立 draft，`approved_by` 由伺服器管理設定決定。
- 所有 POST 需要 idempotency key；永久 ledger 綁定每個 key、SHA-256 fingerprint 與 resource ID，public DTO 不揭露 key 或 fingerprint。
- 詳細契約見 `DOCS/EVIDENCE_MODEL_V2_PHASE2.md`。

## Adaptive 配置研究契約

- legacy 訊號、退場與風控規則維持不變；adaptive 只改變 Stage 配置比例。
- `v1.1_balanced_cap` 只允許：
  - legacy：20% / 50% / 100%
  - balanced：35% / 65% / 100%
- 每個驗證窗只使用前 504 個交易日作 profile 選擇，並保留 144 日暖機。
- 候選訓練報酬須為正、MDD 不得超過 12%，且至少有兩筆完整交易；無候選通過時回退 legacy。
- 50% 與 65% 初始配置的 adaptive v1 因跨期回撤放大，只保留為失敗實驗證據，不得升格。
- research gate 必須同時滿足：
  - 報酬高於 legacy
  - 最差驗證窗 MDD 不超過 12%
  - 至少存在一個熊市窗
  - 熊市複合報酬高於 -5%
  - 熊市最差 MDD 不超過 5%
- 通過 research gate 只代表可進入更廣標的驗證，`promotion_to_default` 仍固定為 `False`。
