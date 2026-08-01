# Walk-forward 台股歷史研究報告

> 僅供個人市場研究與決策參考；所有訂單皆為歷史模擬。

- Run ID: `expanded-phase-a-audited-v2-20260801-b8e7d89c9854`
- 未使用驗證資料最佳化；adaptive profile 僅由各驗證窗之前 504 個交易日選擇。
- 報酬均已納入手續費、證交稅與滑價模型。
- 行情分層為驗證後報告用途；參數敏感度僅使用驗證窗之前的資料。

## 彙總結果

| 標的 | 模型 | 複合報酬 | 最差視窗 MDD | 平均 Sharpe | 交易數 | 持倉天數率 | 平均資金使用率 |
|---|---|---:|---:|---:|---:|---:|---:|
| 0056.TW | tu_strategy | 20.66% | 3.10% | 0.6142 | 42 | 39.87% | 8.54% |
| 0056.TW | adaptive_tu_strategy | 28.03% | 5.31% | 0.6383 | 42 | 39.87% | 12.70% |
| 0056.TW | buy_and_hold | 312.03% | 25.82% | 1.1307 | 12 | 100.00% | 99.92% |
| 0056.TW | sma_8_21 | 173.83% | 14.50% | 0.9517 | 68 | 64.15% | 64.08% |
| 006208.TW | tu_strategy | 15.60% | 5.16% | 0.2082 | 46 | 37.67% | 8.04% |
| 006208.TW | adaptive_tu_strategy | 25.78% | 5.42% | 0.2227 | 46 | 37.67% | 10.22% |
| 006208.TW | buy_and_hold | 536.37% | 29.84% | 1.0716 | 11 | 100.00% | 99.72% |
| 006208.TW | sma_8_21 | 258.08% | 16.26% | 0.856 | 73 | 62.05% | 61.85% |
| 2317.TW | tu_strategy | 19.32% | 15.19% | 0.0336 | 31 | 24.25% | 7.04% |
| 2317.TW | adaptive_tu_strategy | 17.80% | 15.19% | 0.0427 | 31 | 24.25% | 8.04% |
| 2317.TW | buy_and_hold | 261.80% | 42.99% | 0.5095 | 12 | 100.00% | 99.52% |
| 2317.TW | sma_8_21 | 156.79% | 29.66% | 0.3138 | 75 | 55.39% | 55.17% |
| 2454.TW | tu_strategy | 50.11% | 16.51% | -0.0083 | 37 | 30.67% | 11.83% |
| 2454.TW | adaptive_tu_strategy | 53.36% | 17.20% | 0.0133 | 37 | 30.67% | 13.19% |
| 2454.TW | buy_and_hold | 963.80% | 51.58% | 0.8267 | 12 | 100.00% | 97.04% |
| 2454.TW | sma_8_21 | 498.20% | 39.57% | 0.5832 | 72 | 56.18% | 54.52% |
| 2308.TW | tu_strategy | 146.41% | 15.02% | 0.5517 | 31 | 26.10% | 9.23% |
| 2308.TW | adaptive_tu_strategy | 130.27% | 15.02% | 0.5308 | 31 | 26.10% | 10.34% |
| 2308.TW | buy_and_hold | 870.89% | 38.76% | 0.7048 | 12 | 100.00% | 99.07% |
| 2308.TW | sma_8_21 | 158.75% | 31.06% | 0.2776 | 79 | 53.52% | 52.83% |
| 2881.TW | tu_strategy | -0.08% | 5.25% | -0.5283 | 49 | 30.63% | 6.68% |
| 2881.TW | adaptive_tu_strategy | -3.92% | 7.52% | -0.5167 | 49 | 30.63% | 8.17% |
| 2881.TW | buy_and_hold | 403.73% | 34.07% | 0.7738 | 12 | 100.00% | 99.76% |
| 2881.TW | sma_8_21 | 136.70% | 18.90% | 0.344 | 81 | 58.76% | 58.65% |
| 2882.TW | tu_strategy | 9.08% | 5.94% | -0.03 | 42 | 28.52% | 6.34% |
| 2882.TW | adaptive_tu_strategy | 8.27% | 8.32% | -0.0125 | 42 | 28.52% | 8.12% |
| 2882.TW | buy_and_hold | 238.63% | 41.89% | 0.5844 | 12 | 100.00% | 99.86% |
| 2882.TW | sma_8_21 | 42.04% | 25.03% | 0.0362 | 89 | 57.19% | 57.08% |
| 1301.TW | tu_strategy | -16.37% | 17.49% | -0.4373 | 38 | 24.24% | 5.85% |
| 1301.TW | adaptive_tu_strategy | -20.98% | 17.49% | -0.4364 | 38 | 24.24% | 6.84% |
| 1301.TW | buy_and_hold | 10.15% | 55.05% | 0.1552 | 12 | 100.00% | 99.65% |
| 1301.TW | sma_8_21 | -62.61% | 45.79% | -0.4523 | 85 | 54.57% | 54.39% |
| 2002.TW | tu_strategy | -16.18% | 24.83% | -0.5042 | 31 | 17.88% | 3.93% |
| 2002.TW | adaptive_tu_strategy | -13.69% | 23.55% | -0.4842 | 31 | 17.88% | 4.25% |
| 2002.TW | buy_and_hold | -5.00% | 34.49% | -0.0075 | 12 | 100.00% | 99.92% |
| 2002.TW | sma_8_21 | -52.51% | 37.23% | -0.5723 | 95 | 49.22% | 49.17% |
| 1101.TW | tu_strategy | -8.05% | 6.43% | -0.4936 | 36 | 24.31% | 5.35% |
| 1101.TW | adaptive_tu_strategy | -8.15% | 6.43% | -0.4918 | 36 | 24.31% | 6.73% |
| 1101.TW | buy_and_hold | 16.10% | 39.76% | 0.2626 | 12 | 100.00% | 99.87% |
| 1101.TW | sma_8_21 | -17.81% | 23.23% | -0.0732 | 79 | 53.73% | 53.66% |
| 2105.TW | tu_strategy | -12.77% | 9.30% | -0.6617 | 28 | 19.05% | 4.15% |
| 2105.TW | adaptive_tu_strategy | -12.89% | 9.30% | -0.6283 | 28 | 19.05% | 4.53% |
| 2105.TW | buy_and_hold | -40.45% | 41.07% | -0.0524 | 12 | 100.00% | 99.82% |
| 2105.TW | sma_8_21 | -64.16% | 33.50% | -0.5548 | 81 | 48.04% | 47.95% |
| 1216.TW | tu_strategy | -6.47% | 3.83% | -0.3742 | 40 | 27.90% | 5.76% |
| 1216.TW | adaptive_tu_strategy | -7.82% | 5.16% | -0.3742 | 40 | 27.90% | 6.39% |
| 1216.TW | buy_and_hold | 120.11% | 19.86% | 0.484 | 12 | 100.00% | 99.76% |
| 1216.TW | sma_8_21 | -56.04% | 24.49% | -0.4602 | 85 | 55.93% | 55.77% |
| 2412.TW | tu_strategy | -2.07% | 2.09% | -0.2092 | 45 | 37.93% | 7.50% |
| 2412.TW | adaptive_tu_strategy | -2.92% | 2.09% | -0.2058 | 45 | 37.93% | 8.72% |
| 2412.TW | buy_and_hold | 119.26% | 16.29% | 0.7617 | 12 | 100.00% | 99.49% |
| 2412.TW | sma_8_21 | 6.70% | 12.21% | 0.1146 | 80 | 61.79% | 61.49% |
| 2912.TW | tu_strategy | -3.83% | 2.86% | -0.4717 | 30 | 22.37% | 4.37% |
| 2912.TW | adaptive_tu_strategy | -4.45% | 5.24% | -0.4717 | 30 | 22.37% | 5.32% |
| 2912.TW | buy_and_hold | 25.05% | 20.09% | 0.1932 | 12 | 100.00% | 99.09% |
| 2912.TW | sma_8_21 | -62.34% | 19.71% | -0.6456 | 92 | 48.91% | 48.41% |

## Adaptive research gate

| 標的 | 狀態 | 報酬優於 legacy | MDD <= 12% | 熊市報酬 > -5% | 升格預設 |
|---|---|---|---|---|---|
| 0056.TW | hold_for_revision | True | True | True | False |
| 006208.TW | hold_for_revision | True | True | False | False |
| 2317.TW | hold_for_revision | False | False | True | False |
| 2454.TW | hold_for_revision | True | False | True | False |
| 2308.TW | hold_for_revision | False | False | True | False |
| 2881.TW | hold_for_revision | False | True | False | False |
| 2882.TW | hold_for_revision | False | True | True | False |
| 1301.TW | hold_for_revision | False | False | True | False |
| 2002.TW | hold_for_revision | True | False | True | False |
| 1101.TW | hold_for_revision | False | True | True | False |
| 2105.TW | hold_for_revision | False | True | False | False |
| 1216.TW | hold_for_revision | False | True | True | False |
| 2412.TW | hold_for_revision | False | True | True | False |
| 2912.TW | hold_for_revision | False | True | True | False |

## Universe research gate

- Universe: `twse_stratified_phase_a_2012_anchor`
- Status: `hold_for_revision`
- Usable symbols: 0 / 14
- Gate pass rate: 0.00%
- Maximum adaptive MDD: -
- Promotion to default: false

## 行情分層

| 標的 | 行情 | 模型 | 視窗數 | 分層複合報酬 |
|---|---|---|---:|---:|
| 0056.TW | bull | tu_strategy | 9 | 24.12% |
| 0056.TW | bull | adaptive_tu_strategy | 9 | 35.30% |
| 0056.TW | bull | buy_and_hold | 9 | 457.06% |
| 0056.TW | bull | sma_8_21 | 9 | 227.67% |
| 0056.TW | bear | tu_strategy | 1 | -1.56% |
| 0056.TW | bear | adaptive_tu_strategy | 1 | -2.73% |
| 0056.TW | bear | buy_and_hold | 1 | -17.55% |
| 0056.TW | bear | sma_8_21 | 1 | -3.84% |
| 0056.TW | sideways | tu_strategy | 2 | -1.24% |
| 0056.TW | sideways | adaptive_tu_strategy | 2 | -2.72% |
| 0056.TW | sideways | buy_and_hold | 2 | -10.30% |
| 0056.TW | sideways | sma_8_21 | 2 | -13.09% |
| 006208.TW | bull | tu_strategy | 5 | 19.72% |
| 006208.TW | bull | adaptive_tu_strategy | 5 | 30.13% |
| 006208.TW | bull | buy_and_hold | 5 | 466.67% |
| 006208.TW | bull | sma_8_21 | 5 | 253.87% |
| 006208.TW | sideways | tu_strategy | 6 | -3.44% |
| 006208.TW | sideways | adaptive_tu_strategy | 6 | -3.34% |
| 006208.TW | sideways | buy_and_hold | 6 | 12.30% |
| 006208.TW | sideways | sma_8_21 | 6 | 1.19% |
| 2317.TW | bull | tu_strategy | 9 | 22.45% |
| 2317.TW | bull | adaptive_tu_strategy | 9 | 20.59% |
| 2317.TW | bull | buy_and_hold | 9 | 529.35% |
| 2317.TW | bull | sma_8_21 | 9 | 320.55% |
| 2317.TW | bear | tu_strategy | 1 | 0.33% |
| 2317.TW | bear | adaptive_tu_strategy | 1 | 0.58% |
| 2317.TW | bear | buy_and_hold | 1 | -1.57% |
| 2317.TW | bear | sma_8_21 | 1 | -6.03% |
| 2317.TW | sideways | tu_strategy | 2 | -2.88% |
| 2317.TW | sideways | adaptive_tu_strategy | 2 | -2.88% |
| 2317.TW | sideways | buy_and_hold | 2 | -41.59% |
| 2317.TW | sideways | sma_8_21 | 2 | -35.02% |
| 2454.TW | bull | tu_strategy | 9 | 60.64% |
| 2454.TW | bull | adaptive_tu_strategy | 9 | 66.73% |
| 2454.TW | bull | buy_and_hold | 9 | 4154.57% |
| 2454.TW | bull | sma_8_21 | 9 | 1061.30% |
| 2454.TW | bear | tu_strategy | 1 | -2.16% |
| 2454.TW | bear | adaptive_tu_strategy | 1 | -2.16% |
| 2454.TW | bear | buy_and_hold | 1 | -38.51% |
| 2454.TW | bear | sma_8_21 | 1 | -0.39% |
| 2454.TW | sideways | tu_strategy | 2 | -4.49% |
| 2454.TW | sideways | adaptive_tu_strategy | 2 | -5.99% |
| 2454.TW | sideways | buy_and_hold | 2 | -59.34% |
| 2454.TW | sideways | sma_8_21 | 2 | -48.29% |
| 2308.TW | bull | tu_strategy | 9 | 158.95% |
| 2308.TW | bull | adaptive_tu_strategy | 9 | 149.79% |
| 2308.TW | bull | buy_and_hold | 9 | 1160.85% |
| 2308.TW | bull | sma_8_21 | 9 | 547.25% |
| 2308.TW | bear | tu_strategy | 1 | -1.40% |
| 2308.TW | bear | adaptive_tu_strategy | 1 | -2.78% |
| 2308.TW | bear | buy_and_hold | 1 | -4.44% |
| 2308.TW | bear | sma_8_21 | 1 | -20.03% |
| 2308.TW | sideways | tu_strategy | 2 | -3.49% |
| 2308.TW | sideways | adaptive_tu_strategy | 2 | -5.18% |
| 2308.TW | sideways | buy_and_hold | 2 | -19.42% |
| 2308.TW | sideways | sma_8_21 | 2 | -50.01% |
| 2881.TW | bull | tu_strategy | 9 | 8.09% |
| 2881.TW | bull | adaptive_tu_strategy | 9 | 9.29% |
| 2881.TW | bull | buy_and_hold | 9 | 629.06% |
| 2881.TW | bull | sma_8_21 | 9 | 127.33% |
| 2881.TW | bear | tu_strategy | 1 | -3.63% |
| 2881.TW | bear | adaptive_tu_strategy | 1 | -5.88% |
| 2881.TW | bear | buy_and_hold | 1 | -15.74% |
| 2881.TW | bear | sma_8_21 | 1 | 6.78% |
| 2881.TW | sideways | tu_strategy | 2 | -4.08% |
| 2881.TW | sideways | adaptive_tu_strategy | 2 | -6.59% |
| 2881.TW | sideways | buy_and_hold | 2 | -18.00% |
| 2881.TW | sideways | sma_8_21 | 2 | -2.49% |
| 2882.TW | bull | tu_strategy | 9 | 10.08% |
| 2882.TW | bull | adaptive_tu_strategy | 9 | 10.69% |
| 2882.TW | bull | buy_and_hold | 9 | 521.11% |
| 2882.TW | bull | sma_8_21 | 9 | 104.37% |
| 2882.TW | bear | tu_strategy | 1 | 1.22% |
| 2882.TW | bear | adaptive_tu_strategy | 1 | 1.75% |
| 2882.TW | bear | buy_and_hold | 1 | -29.67% |
| 2882.TW | bear | sma_8_21 | 1 | -0.86% |
| 2882.TW | sideways | tu_strategy | 2 | -2.11% |
| 2882.TW | sideways | adaptive_tu_strategy | 2 | -3.87% |
| 2882.TW | sideways | buy_and_hold | 2 | -22.48% |
| 2882.TW | sideways | sma_8_21 | 2 | -29.90% |
| 1301.TW | bull | tu_strategy | 9 | -10.15% |
| 1301.TW | bull | adaptive_tu_strategy | 9 | -11.41% |
| 1301.TW | bull | buy_and_hold | 9 | 19.05% |
| 1301.TW | bull | sma_8_21 | 9 | -50.96% |
| 1301.TW | bear | tu_strategy | 1 | -2.79% |
| 1301.TW | bear | adaptive_tu_strategy | 1 | -4.94% |
| 1301.TW | bear | buy_and_hold | 1 | -10.92% |
| 1301.TW | bear | sma_8_21 | 1 | -14.12% |
| 1301.TW | sideways | tu_strategy | 2 | -4.26% |
| 1301.TW | sideways | adaptive_tu_strategy | 2 | -6.17% |
| 1301.TW | sideways | buy_and_hold | 2 | 3.86% |
| 1301.TW | sideways | sma_8_21 | 2 | -11.23% |
| 2002.TW | bull | tu_strategy | 9 | -15.46% |
| 2002.TW | bull | adaptive_tu_strategy | 9 | -12.94% |
| 2002.TW | bull | buy_and_hold | 9 | 43.64% |
| 2002.TW | bull | sma_8_21 | 9 | -35.24% |
| 2002.TW | bear | tu_strategy | 1 | 0.75% |
| 2002.TW | bear | adaptive_tu_strategy | 1 | 0.75% |
| 2002.TW | bear | buy_and_hold | 1 | -4.42% |
| 2002.TW | bear | sma_8_21 | 1 | 1.06% |
| 2002.TW | sideways | tu_strategy | 2 | -1.59% |
| 2002.TW | sideways | adaptive_tu_strategy | 2 | -1.59% |
| 2002.TW | sideways | buy_and_hold | 2 | -30.80% |
| 2002.TW | sideways | sma_8_21 | 2 | -27.44% |
| 1101.TW | bull | tu_strategy | 9 | -8.52% |
| 1101.TW | bull | adaptive_tu_strategy | 9 | -8.62% |
| 1101.TW | bull | buy_and_hold | 9 | 112.68% |
| 1101.TW | bull | sma_8_21 | 9 | 17.13% |
| 1101.TW | bear | tu_strategy | 1 | -0.53% |
| 1101.TW | bear | adaptive_tu_strategy | 1 | -0.53% |
| 1101.TW | bear | buy_and_hold | 1 | -20.22% |
| 1101.TW | bear | sma_8_21 | 1 | -3.64% |
| 1101.TW | sideways | tu_strategy | 2 | 1.05% |
| 1101.TW | sideways | adaptive_tu_strategy | 2 | 1.05% |
| 1101.TW | sideways | buy_and_hold | 2 | -31.58% |
| 1101.TW | sideways | sma_8_21 | 2 | -27.18% |
| 2105.TW | bull | tu_strategy | 9 | -8.16% |
| 2105.TW | bull | adaptive_tu_strategy | 9 | -6.30% |
| 2105.TW | bull | buy_and_hold | 9 | 1.30% |
| 2105.TW | bull | sma_8_21 | 9 | -34.35% |
| 2105.TW | bear | tu_strategy | 1 | -4.23% |
| 2105.TW | bear | adaptive_tu_strategy | 1 | -6.27% |
| 2105.TW | bear | buy_and_hold | 1 | -1.63% |
| 2105.TW | bear | sma_8_21 | 1 | -16.69% |
| 2105.TW | sideways | tu_strategy | 2 | -0.82% |
| 2105.TW | sideways | adaptive_tu_strategy | 2 | -0.82% |
| 2105.TW | sideways | buy_and_hold | 2 | -40.24% |
| 2105.TW | sideways | sma_8_21 | 2 | -34.46% |
| 1216.TW | bull | tu_strategy | 9 | -2.90% |
| 1216.TW | bull | adaptive_tu_strategy | 9 | -2.90% |
| 1216.TW | bull | buy_and_hold | 9 | 78.24% |
| 1216.TW | bull | sma_8_21 | 9 | -38.94% |
| 1216.TW | bear | tu_strategy | 1 | -1.18% |
| 1216.TW | bear | adaptive_tu_strategy | 1 | -1.18% |
| 1216.TW | bear | buy_and_hold | 1 | 0.71% |
| 1216.TW | bear | sma_8_21 | 1 | -11.01% |
| 1216.TW | sideways | tu_strategy | 2 | -2.53% |
| 1216.TW | sideways | adaptive_tu_strategy | 2 | -3.94% |
| 1216.TW | sideways | buy_and_hold | 2 | 22.62% |
| 1216.TW | sideways | sma_8_21 | 2 | -19.10% |
| 2412.TW | bull | tu_strategy | 9 | -1.19% |
| 2412.TW | bull | adaptive_tu_strategy | 9 | -1.69% |
| 2412.TW | bull | buy_and_hold | 9 | 90.64% |
| 2412.TW | bull | sma_8_21 | 9 | 20.58% |
| 2412.TW | bear | tu_strategy | 1 | -0.48% |
| 2412.TW | bear | adaptive_tu_strategy | 1 | -0.48% |
| 2412.TW | bear | buy_and_hold | 1 | 1.49% |
| 2412.TW | bear | sma_8_21 | 1 | -2.69% |
| 2412.TW | sideways | tu_strategy | 2 | -0.41% |
| 2412.TW | sideways | adaptive_tu_strategy | 2 | -0.78% |
| 2412.TW | sideways | buy_and_hold | 2 | 13.32% |
| 2412.TW | sideways | sma_8_21 | 2 | -9.06% |
| 2912.TW | bull | tu_strategy | 9 | -3.89% |
| 2912.TW | bull | adaptive_tu_strategy | 9 | -4.70% |
| 2912.TW | bull | buy_and_hold | 9 | 30.23% |
| 2912.TW | bull | sma_8_21 | 9 | -45.84% |
| 2912.TW | bear | tu_strategy | 1 | 0.03% |
| 2912.TW | bear | adaptive_tu_strategy | 1 | 0.03% |
| 2912.TW | bear | buy_and_hold | 1 | 1.50% |
| 2912.TW | bear | sma_8_21 | 1 | -12.00% |
| 2912.TW | sideways | tu_strategy | 2 | 0.03% |
| 2912.TW | sideways | adaptive_tu_strategy | 2 | 0.24% |
| 2912.TW | sideways | buy_and_hold | 2 | -5.39% |
| 2912.TW | sideways | sma_8_21 | 2 | -20.99% |

## 訓練窗參數敏感度

| 標的 | 候選數 | 可用訓練窗 | 平均報酬範圍 | 正平均報酬候選比率 |
|---|---:|---:|---:|---:|
| 0056.TW | 5 | 11 | 0.9772 個百分點 | 1.0 |
| 006208.TW | 5 | 10 | 0.536 個百分點 | 1.0 |
| 2317.TW | 5 | 11 | 2.4382 個百分點 | 1.0 |
| 2454.TW | 5 | 11 | 6.9609 個百分點 | 1.0 |
| 2308.TW | 5 | 11 | 3.3991 個百分點 | 1.0 |
| 2881.TW | 5 | 11 | 2.0073 個百分點 | 0.2 |
| 2882.TW | 5 | 11 | 1.63 個百分點 | 1.0 |
| 1301.TW | 5 | 11 | 2.6064 個百分點 | 0.0 |
| 2002.TW | 5 | 11 | 1.0755 個百分點 | 0.0 |
| 1101.TW | 5 | 11 | 1.7118 個百分點 | 0.0 |
| 2105.TW | 5 | 11 | 2.2173 個百分點 | 0.0 |
| 1216.TW | 5 | 11 | 0.6128 個百分點 | 0.0 |
| 2412.TW | 5 | 11 | 0.0654 個百分點 | 0.8 |
| 2912.TW | 5 | 11 | 1.5764 個百分點 | 0.0 |

## 資料品質

| 標的 | 狀態 | 筆數 | 範圍 | 最大單日變動 | 價格還原契約 |
|---|---|---:|---|---:|---|
| 0056.TW | quality_warning | 3056 | 2014-01-02 ~ 2026-07-31 | 9.9801% | yfinance auto_adjust=True; actions=False; repair=False |
| 006208.TW | quality_warning | 2837 | 2014-01-02 ~ 2026-07-31 | 9.9807% | yfinance auto_adjust=True; actions=False; repair=False |
| 2317.TW | quality_warning | 3047 | 2014-01-02 ~ 2026-07-31 | 10.4846% | yfinance auto_adjust=True; actions=False; repair=False |
| 2454.TW | quality_warning | 3054 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 2308.TW | quality_warning | 3055 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 2881.TW | quality_warning | 3055 | 2014-01-02 ~ 2026-07-31 | 9.9861% | yfinance auto_adjust=True; actions=False; repair=False |
| 2882.TW | quality_warning | 3055 | 2014-01-02 ~ 2026-07-31 | 9.9692% | yfinance auto_adjust=True; actions=False; repair=False |
| 1301.TW | quality_warning | 3056 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 2002.TW | quality_warning | 3055 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 1101.TW | quality_warning | 3052 | 2014-01-02 ~ 2026-07-31 | 9.9702% | yfinance auto_adjust=True; actions=False; repair=False |
| 2105.TW | quality_warning | 3055 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 1216.TW | quality_warning | 3054 | 2014-01-02 ~ 2026-07-31 | 9.0354% | yfinance auto_adjust=True; actions=False; repair=False |
| 2412.TW | quality_warning | 3055 | 2014-01-02 ~ 2026-07-31 | 6.1033% | yfinance auto_adjust=True; actions=False; repair=False |
| 2912.TW | quality_warning | 3054 | 2014-01-02 ~ 2026-07-31 | 9.932% | yfinance auto_adjust=True; actions=False; repair=False |

## 解讀限制

- 資料涵蓋期間以本次快照與 data_provenance.csv 記錄的實際範圍為準。
- Walk-forward 視窗重設初始資金；彙總報酬以各連續視窗報酬率複合計算。
- 尚未納入股利、除權息還原差異、流動性衝擊與個股漲跌停成交限制。
- yfinance 為非官方資料供應路徑；完整抓取契約與資料雜湊見 data_provenance.csv。
- 供應商可能回溯修訂調整價；本次報告以內附 CSV 與 normalized_snapshot_sha256 固定版本。
- 參數敏感度不會自動挑選或套用最佳參數，避免驗證資料洩漏。
- adaptive profile 只改變配置比例，legacy 進退場訊號與風控規則維持不變。
