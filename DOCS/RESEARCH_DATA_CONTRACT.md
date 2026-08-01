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
