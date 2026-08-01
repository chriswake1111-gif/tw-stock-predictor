# Walk-forward 台股歷史研究報告

> 僅供個人市場研究與決策參考；所有訂單皆為歷史模擬。

- Run ID: `baseline-v2-20260801-5cb3f28be410`
- 未執行參數最佳化；每個驗證窗只使用先前資料作指標暖機。
- 報酬均已納入手續費、證交稅與滑價模型。

## 彙總結果

| 標的 | 模型 | 複合報酬 | 最差視窗 MDD | 平均 Sharpe | 交易數 | 暴露率 |
|---|---|---:|---:|---:|---:|---:|
| ^TWII | tu_strategy | 13.37% | 3.43% | 0.43 | 16 | 45.14% |
| ^TWII | buy_and_hold | 201.56% | 27.47% | 1.6267 | 4 | 100.00% |
| ^TWII | sma_8_21 | 130.39% | 15.28% | 1.3667 | 23 | 68.91% |
| 0050.TW | tu_strategy | 11.44% | 3.82% | 1.68 | 5 | 76.95% |
| 0050.TW | buy_and_hold | 113.18% | 10.83% | 3.2594 | 1 | 100.00% |
| 0050.TW | sma_8_21 | 80.15% | 9.27% | 2.8411 | 4 | 81.89% |
| 2330.TW | tu_strategy | 17.48% | 6.09% | 0.6275 | 17 | 47.62% |
| 2330.TW | buy_and_hold | 404.40% | 28.87% | 1.6094 | 4 | 100.00% |
| 2330.TW | sma_8_21 | 207.61% | 18.14% | 1.3315 | 24 | 65.08% |

## 資料品質

| 標的 | 狀態 | 筆數 | 範圍 | 最大單日變動 | 價格還原契約 |
|---|---|---:|---|---:|---|
| ^TWII | available | 1214 | 2021-08-02 ~ 2026-07-31 | 9.6997% | legacy_cache_unknown; new yfinance fetches explicitly use auto_adjust=True |
| 0050.TW | available | 486 | 2024-07-31 ~ 2026-07-31 | 10.0% | legacy_cache_unknown; new yfinance fetches explicitly use auto_adjust=True |
| 2330.TW | available | 1213 | 2021-08-02 ~ 2026-07-31 | 10.2273% | legacy_cache_unknown; new yfinance fetches explicitly use auto_adjust=True |

## 解讀限制

- 資料涵蓋期間依本地快取實際範圍，不推定已有十年資料。
- Walk-forward 視窗重設初始資金；彙總報酬以各連續視窗報酬率複合計算。
- 尚未納入股利、除權息還原差異、流動性衝擊與個股漲跌停成交限制。
