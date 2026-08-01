# Walk-forward 台股歷史研究報告

> 僅供個人市場研究與決策參考；所有訂單皆為歷史模擬。

- Run ID: `adaptive-v1-1-20260801-193192967aed`
- 未使用驗證資料最佳化；adaptive profile 僅由各驗證窗之前 504 個交易日選擇。
- 報酬均已納入手續費、證交稅與滑價模型。
- 行情分層為驗證後報告用途；參數敏感度僅使用驗證窗之前的資料。

## 彙總結果

| 標的 | 模型 | 複合報酬 | 最差視窗 MDD | 平均 Sharpe | 交易數 | 持倉天數率 | 平均資金使用率 |
|---|---|---:|---:|---:|---:|---:|---:|
| ^TWII | tu_strategy | 12.43% | 3.43% | 0.1158 | 47 | 38.10% | 8.14% |
| ^TWII | adaptive_tu_strategy | 20.34% | 4.82% | 0.1433 | 47 | 38.10% | 10.51% |
| ^TWII | buy_and_hold | 316.39% | 31.61% | 0.9411 | 12 | 100.00% | 99.93% |
| ^TWII | sma_8_21 | 118.14% | 23.79% | 0.5626 | 78 | 63.93% | 63.88% |
| 0050.TW | tu_strategy | 17.91% | 3.84% | 0.2933 | 52 | 39.10% | 8.48% |
| 0050.TW | adaptive_tu_strategy | 27.63% | 6.43% | 0.3283 | 52 | 39.10% | 11.73% |
| 0050.TW | buy_and_hold | 681.30% | 33.79% | 1.1588 | 12 | 100.00% | 99.90% |
| 0050.TW | sma_8_21 | 208.22% | 19.43% | 0.735 | 81 | 62.68% | 62.60% |
| 2330.TW | tu_strategy | 19.82% | 7.86% | 0.3125 | 50 | 42.49% | 9.56% |
| 2330.TW | adaptive_tu_strategy | 26.43% | 11.63% | 0.3767 | 50 | 42.49% | 13.43% |
| 2330.TW | buy_and_hold | 1791.30% | 44.31% | 1.2358 | 12 | 100.00% | 98.83% |
| 2330.TW | sma_8_21 | 407.37% | 30.26% | 0.8385 | 80 | 62.74% | 61.16% |

## Adaptive research gate

| 標的 | 狀態 | 報酬優於 legacy | MDD <= 12% | 熊市報酬 > -5% | 升格預設 |
|---|---|---|---|---|---|
| ^TWII | candidate_for_broader_validation | True | True | True | False |
| 0050.TW | candidate_for_broader_validation | True | True | True | False |
| 2330.TW | candidate_for_broader_validation | True | True | True | False |

## 行情分層

| 標的 | 行情 | 模型 | 視窗數 | 分層複合報酬 |
|---|---|---|---:|---:|
| ^TWII | bull | tu_strategy | 9 | 15.53% |
| ^TWII | bull | adaptive_tu_strategy | 9 | 24.27% |
| ^TWII | bull | buy_and_hold | 9 | 635.19% |
| ^TWII | bull | sma_8_21 | 9 | 226.47% |
| ^TWII | bear | tu_strategy | 1 | -0.66% |
| ^TWII | bear | adaptive_tu_strategy | 1 | -1.15% |
| ^TWII | bear | buy_and_hold | 1 | -24.08% |
| ^TWII | bear | sma_8_21 | 1 | -7.39% |
| ^TWII | sideways | tu_strategy | 2 | -2.04% |
| ^TWII | sideways | adaptive_tu_strategy | 2 | -2.04% |
| ^TWII | sideways | buy_and_hold | 2 | -25.40% |
| ^TWII | sideways | sma_8_21 | 2 | -27.85% |
| 0050.TW | bull | tu_strategy | 9 | 21.78% |
| 0050.TW | bull | adaptive_tu_strategy | 9 | 33.96% |
| 0050.TW | bull | buy_and_hold | 9 | 1154.82% |
| 0050.TW | bull | sma_8_21 | 9 | 352.20% |
| 0050.TW | bear | tu_strategy | 1 | -0.45% |
| 0050.TW | bear | adaptive_tu_strategy | 1 | -0.80% |
| 0050.TW | bear | buy_and_hold | 1 | -23.85% |
| 0050.TW | bear | sma_8_21 | 1 | -4.75% |
| 0050.TW | sideways | tu_strategy | 2 | -2.74% |
| 0050.TW | sideways | adaptive_tu_strategy | 2 | -3.96% |
| 0050.TW | sideways | buy_and_hold | 2 | -18.24% |
| 0050.TW | sideways | sma_8_21 | 2 | -28.44% |
| 2330.TW | bull | tu_strategy | 9 | 30.43% |
| 2330.TW | bull | adaptive_tu_strategy | 9 | 42.99% |
| 2330.TW | bull | buy_and_hold | 9 | 2946.30% |
| 2330.TW | bull | sma_8_21 | 9 | 601.46% |
| 2330.TW | bear | tu_strategy | 1 | -0.10% |
| 2330.TW | bear | adaptive_tu_strategy | 1 | -0.10% |
| 2330.TW | bear | buy_and_hold | 1 | -31.51% |
| 2330.TW | bear | sma_8_21 | 1 | 2.14% |
| 2330.TW | sideways | tu_strategy | 2 | -8.04% |
| 2330.TW | sideways | adaptive_tu_strategy | 2 | -11.49% |
| 2330.TW | sideways | buy_and_hold | 2 | -9.35% |
| 2330.TW | sideways | sma_8_21 | 2 | -29.19% |

## 訓練窗參數敏感度

| 標的 | 候選數 | 可用訓練窗 | 平均報酬範圍 | 正平均報酬候選比率 |
|---|---:|---:|---:|---:|
| ^TWII | 5 | 11 | 0.3409 個百分點 | 1.0 |
| 0050.TW | 5 | 11 | 0.8454 個百分點 | 1.0 |
| 2330.TW | 5 | 11 | 7.9809 個百分點 | 1.0 |

## 資料品質

| 標的 | 狀態 | 筆數 | 範圍 | 最大單日變動 | 價格還原契約 |
|---|---|---:|---|---:|---|
| ^TWII | available | 3059 | 2014-01-02 ~ 2026-07-31 | 9.6997% | yfinance auto_adjust=True; actions=False; repair=False |
| 0050.TW | available | 3064 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 2330.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 9.9788% | yfinance auto_adjust=True; actions=False; repair=False |

## 解讀限制

- 資料涵蓋期間以本次快照與 data_provenance.csv 記錄的實際範圍為準。
- Walk-forward 視窗重設初始資金；彙總報酬以各連續視窗報酬率複合計算。
- 尚未納入股利、除權息還原差異、流動性衝擊與個股漲跌停成交限制。
- yfinance 為非官方資料供應路徑；完整抓取契約與資料雜湊見 data_provenance.csv。
- 供應商可能回溯修訂調整價；本次報告以內附 CSV 與 normalized_snapshot_sha256 固定版本。
- 參數敏感度不會自動挑選或套用最佳參數，避免驗證資料洩漏。
- adaptive profile 只改變配置比例，legacy 進退場訊號與風控規則維持不變。
