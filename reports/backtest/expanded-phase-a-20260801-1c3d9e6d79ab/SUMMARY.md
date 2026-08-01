# Walk-forward 台股歷史研究報告

> 僅供個人市場研究與決策參考；所有訂單皆為歷史模擬。

- Run ID: `expanded-phase-a-20260801-1c3d9e6d79ab`
- 未使用驗證資料最佳化；adaptive profile 僅由各驗證窗之前 504 個交易日選擇。
- 報酬均已納入手續費、證交稅與滑價模型。
- 行情分層為驗證後報告用途；參數敏感度僅使用驗證窗之前的資料。

## 彙總結果

| 標的 | 模型 | 複合報酬 | 最差視窗 MDD | 平均 Sharpe | 交易數 | 持倉天數率 | 平均資金使用率 |
|---|---|---:|---:|---:|---:|---:|---:|
| 0056.TW | tu_strategy | 20.92% | 3.10% | 0.6317 | 43 | 39.81% | 8.52% |
| 0056.TW | adaptive_tu_strategy | 28.51% | 5.31% | 0.6533 | 43 | 39.81% | 12.68% |
| 0056.TW | buy_and_hold | 312.03% | 25.82% | 1.1268 | 12 | 100.00% | 99.92% |
| 0056.TW | sma_8_21 | 167.32% | 14.50% | 0.9255 | 69 | 64.13% | 64.06% |
| 006208.TW | tu_strategy | 15.32% | 6.80% | 0.1192 | 51 | 39.52% | 8.96% |
| 006208.TW | adaptive_tu_strategy | 22.12% | 6.33% | 0.1925 | 51 | 39.52% | 12.22% |
| 006208.TW | buy_and_hold | 689.45% | 33.65% | 1.1182 | 12 | 100.00% | 99.82% |
| 006208.TW | sma_8_21 | 234.29% | 21.42% | 0.7957 | 82 | 63.32% | 63.11% |
| 2317.TW | tu_strategy | 19.49% | 15.19% | 0.0473 | 31 | 24.30% | 7.04% |
| 2317.TW | adaptive_tu_strategy | 17.97% | 15.19% | 0.0564 | 31 | 24.30% | 8.03% |
| 2317.TW | buy_and_hold | 261.80% | 42.99% | 0.5104 | 12 | 100.00% | 99.52% |
| 2317.TW | sma_8_21 | 153.04% | 29.66% | 0.3024 | 75 | 55.28% | 55.05% |
| 2454.TW | tu_strategy | 49.70% | 16.51% | -0.0383 | 37 | 30.55% | 11.78% |
| 2454.TW | adaptive_tu_strategy | 52.94% | 17.20% | -0.0167 | 37 | 30.55% | 13.13% |
| 2454.TW | buy_and_hold | 963.80% | 51.58% | 0.8262 | 12 | 100.00% | 97.04% |
| 2454.TW | sma_8_21 | 511.01% | 40.10% | 0.5842 | 72 | 56.16% | 54.47% |
| 2308.TW | tu_strategy | 146.70% | 15.02% | 0.5483 | 31 | 25.75% | 9.12% |
| 2308.TW | adaptive_tu_strategy | 132.80% | 15.02% | 0.5358 | 31 | 25.75% | 10.19% |
| 2308.TW | buy_and_hold | 870.89% | 38.76% | 0.7042 | 12 | 100.00% | 99.07% |
| 2308.TW | sma_8_21 | 147.00% | 34.19% | 0.2603 | 79 | 53.43% | 52.74% |
| 2881.TW | tu_strategy | 0.39% | 5.25% | -0.525 | 49 | 30.69% | 6.70% |
| 2881.TW | adaptive_tu_strategy | -3.59% | 7.52% | -0.5125 | 49 | 30.69% | 8.19% |
| 2881.TW | buy_and_hold | 403.73% | 34.07% | 0.7722 | 12 | 100.00% | 99.76% |
| 2881.TW | sma_8_21 | 122.74% | 18.90% | 0.3143 | 82 | 58.83% | 58.71% |
| 2882.TW | tu_strategy | 8.32% | 5.94% | -0.05 | 43 | 28.56% | 6.34% |
| 2882.TW | adaptive_tu_strategy | 7.52% | 8.32% | -0.0333 | 43 | 28.56% | 8.13% |
| 2882.TW | buy_and_hold | 238.63% | 41.89% | 0.583 | 12 | 100.00% | 99.86% |
| 2882.TW | sma_8_21 | 35.53% | 25.03% | 0.0122 | 87 | 57.12% | 57.01% |
| 1301.TW | tu_strategy | -16.01% | 17.49% | -0.4173 | 38 | 24.23% | 5.87% |
| 1301.TW | adaptive_tu_strategy | -20.91% | 17.49% | -0.3982 | 38 | 24.23% | 7.43% |
| 1301.TW | buy_and_hold | 10.15% | 55.05% | 0.1539 | 12 | 100.00% | 99.65% |
| 1301.TW | sma_8_21 | -63.82% | 46.04% | -0.4594 | 86 | 54.56% | 54.38% |
| 2002.TW | tu_strategy | -15.78% | 24.83% | -0.4875 | 31 | 17.90% | 3.93% |
| 2002.TW | adaptive_tu_strategy | -13.27% | 23.55% | -0.4675 | 31 | 17.90% | 4.25% |
| 2002.TW | buy_and_hold | -5.00% | 34.49% | -0.0081 | 12 | 100.00% | 99.92% |
| 2002.TW | sma_8_21 | -52.98% | 37.15% | -0.5948 | 95 | 49.20% | 49.15% |
| 1101.TW | tu_strategy | -6.70% | 6.43% | -0.3627 | 36 | 24.23% | 5.34% |
| 1101.TW | adaptive_tu_strategy | -6.80% | 6.43% | -0.3609 | 36 | 24.23% | 6.71% |
| 1101.TW | buy_and_hold | 16.10% | 39.76% | 0.262 | 12 | 100.00% | 99.87% |
| 1101.TW | sma_8_21 | -16.97% | 23.23% | -0.0679 | 80 | 53.68% | 53.61% |
| 2105.TW | tu_strategy | -12.80% | 9.30% | -0.6633 | 28 | 19.08% | 4.16% |
| 2105.TW | adaptive_tu_strategy | -12.93% | 9.30% | -0.63 | 28 | 19.08% | 4.54% |
| 2105.TW | buy_and_hold | -40.45% | 41.07% | -0.0526 | 12 | 100.00% | 99.82% |
| 2105.TW | sma_8_21 | -65.29% | 33.50% | -0.5727 | 83 | 48.17% | 48.08% |
| 1216.TW | tu_strategy | -7.70% | 3.83% | -0.4467 | 41 | 28.10% | 5.80% |
| 1216.TW | adaptive_tu_strategy | -7.70% | 3.83% | -0.4467 | 41 | 28.10% | 5.80% |
| 1216.TW | buy_and_hold | 120.11% | 19.86% | 0.4833 | 12 | 100.00% | 99.76% |
| 1216.TW | sma_8_21 | -56.16% | 24.49% | -0.4614 | 85 | 55.95% | 55.79% |
| 2412.TW | tu_strategy | -2.13% | 2.09% | -0.2125 | 45 | 37.98% | 7.51% |
| 2412.TW | adaptive_tu_strategy | -2.98% | 2.09% | -0.2092 | 45 | 37.98% | 8.73% |
| 2412.TW | buy_and_hold | 119.26% | 16.29% | 0.7604 | 12 | 100.00% | 99.49% |
| 2412.TW | sma_8_21 | 5.56% | 12.21% | 0.1008 | 80 | 61.85% | 61.55% |
| 2912.TW | tu_strategy | -6.39% | 3.70% | -0.5625 | 31 | 22.74% | 4.45% |
| 2912.TW | adaptive_tu_strategy | -6.82% | 5.24% | -0.5617 | 31 | 22.74% | 5.40% |
| 2912.TW | buy_and_hold | 25.05% | 20.09% | 0.1926 | 12 | 100.00% | 99.09% |
| 2912.TW | sma_8_21 | -62.66% | 22.16% | -0.649 | 93 | 48.88% | 48.36% |

## Adaptive research gate

| 標的 | 狀態 | 報酬優於 legacy | MDD <= 12% | 熊市報酬 > -5% | 升格預設 |
|---|---|---|---|---|---|
| 0056.TW | candidate_for_broader_validation | True | True | True | False |
| 006208.TW | candidate_for_broader_validation | True | True | True | False |
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
- Usable symbols: 14 / 14
- Gate pass rate: 14.29%
- Maximum adaptive MDD: 23.55%
- Promotion to default: false

## 行情分層

| 標的 | 行情 | 模型 | 視窗數 | 分層複合報酬 |
|---|---|---|---:|---:|
| 0056.TW | bull | tu_strategy | 9 | 24.38% |
| 0056.TW | bull | adaptive_tu_strategy | 9 | 35.80% |
| 0056.TW | bull | buy_and_hold | 9 | 457.06% |
| 0056.TW | bull | sma_8_21 | 9 | 227.54% |
| 0056.TW | bear | tu_strategy | 1 | -1.56% |
| 0056.TW | bear | adaptive_tu_strategy | 1 | -2.73% |
| 0056.TW | bear | buy_and_hold | 1 | -17.55% |
| 0056.TW | bear | sma_8_21 | 1 | -3.84% |
| 0056.TW | sideways | tu_strategy | 2 | -1.24% |
| 0056.TW | sideways | adaptive_tu_strategy | 2 | -2.72% |
| 0056.TW | sideways | buy_and_hold | 2 | -10.30% |
| 0056.TW | sideways | sma_8_21 | 2 | -15.13% |
| 006208.TW | bull | tu_strategy | 9 | 21.05% |
| 006208.TW | bull | adaptive_tu_strategy | 9 | 32.17% |
| 006208.TW | bull | buy_and_hold | 9 | 1157.15% |
| 006208.TW | bull | sma_8_21 | 9 | 362.98% |
| 006208.TW | bear | tu_strategy | 1 | -0.56% |
| 006208.TW | bear | adaptive_tu_strategy | 1 | -1.01% |
| 006208.TW | bear | buy_and_hold | 1 | -23.90% |
| 006208.TW | bear | sma_8_21 | 1 | -5.29% |
| 006208.TW | sideways | tu_strategy | 2 | -4.20% |
| 006208.TW | sideways | adaptive_tu_strategy | 2 | -6.66% |
| 006208.TW | sideways | buy_and_hold | 2 | -17.48% |
| 006208.TW | sideways | sma_8_21 | 2 | -23.76% |
| 2317.TW | bull | tu_strategy | 9 | 22.51% |
| 2317.TW | bull | adaptive_tu_strategy | 9 | 20.65% |
| 2317.TW | bull | buy_and_hold | 9 | 529.35% |
| 2317.TW | bull | sma_8_21 | 9 | 320.53% |
| 2317.TW | bear | tu_strategy | 1 | 0.33% |
| 2317.TW | bear | adaptive_tu_strategy | 1 | 0.58% |
| 2317.TW | bear | buy_and_hold | 1 | -1.57% |
| 2317.TW | bear | sma_8_21 | 1 | -6.03% |
| 2317.TW | sideways | tu_strategy | 2 | -2.79% |
| 2317.TW | sideways | adaptive_tu_strategy | 2 | -2.79% |
| 2317.TW | sideways | buy_and_hold | 2 | -41.59% |
| 2317.TW | sideways | sma_8_21 | 2 | -35.96% |
| 2454.TW | bull | tu_strategy | 9 | 60.20% |
| 2454.TW | bull | adaptive_tu_strategy | 9 | 66.27% |
| 2454.TW | bull | buy_and_hold | 9 | 4154.57% |
| 2454.TW | bull | sma_8_21 | 9 | 1136.51% |
| 2454.TW | bear | tu_strategy | 1 | -2.16% |
| 2454.TW | bear | adaptive_tu_strategy | 1 | -2.16% |
| 2454.TW | bear | buy_and_hold | 1 | -38.51% |
| 2454.TW | bear | sma_8_21 | 1 | -0.39% |
| 2454.TW | sideways | tu_strategy | 2 | -4.49% |
| 2454.TW | sideways | adaptive_tu_strategy | 2 | -5.99% |
| 2454.TW | sideways | buy_and_hold | 2 | -59.34% |
| 2454.TW | sideways | sma_8_21 | 2 | -50.39% |
| 2308.TW | bull | tu_strategy | 9 | 158.95% |
| 2308.TW | bull | adaptive_tu_strategy | 9 | 149.79% |
| 2308.TW | bull | buy_and_hold | 9 | 1160.85% |
| 2308.TW | bull | sma_8_21 | 9 | 547.24% |
| 2308.TW | bear | tu_strategy | 1 | -0.53% |
| 2308.TW | bear | adaptive_tu_strategy | 1 | -0.96% |
| 2308.TW | bear | buy_and_hold | 1 | -4.44% |
| 2308.TW | bear | sma_8_21 | 1 | -20.03% |
| 2308.TW | sideways | tu_strategy | 2 | -4.22% |
| 2308.TW | sideways | adaptive_tu_strategy | 2 | -5.90% |
| 2308.TW | sideways | buy_and_hold | 2 | -19.42% |
| 2308.TW | sideways | sma_8_21 | 2 | -52.28% |
| 2881.TW | bull | tu_strategy | 9 | 8.41% |
| 2881.TW | bull | adaptive_tu_strategy | 9 | 9.47% |
| 2881.TW | bull | buy_and_hold | 9 | 629.06% |
| 2881.TW | bull | sma_8_21 | 9 | 123.06% |
| 2881.TW | bear | tu_strategy | 1 | -3.63% |
| 2881.TW | bear | adaptive_tu_strategy | 1 | -5.88% |
| 2881.TW | bear | buy_and_hold | 1 | -15.74% |
| 2881.TW | bear | sma_8_21 | 1 | 6.78% |
| 2881.TW | sideways | tu_strategy | 2 | -3.91% |
| 2881.TW | sideways | adaptive_tu_strategy | 2 | -6.43% |
| 2881.TW | sideways | buy_and_hold | 2 | -18.00% |
| 2881.TW | sideways | sma_8_21 | 2 | -6.48% |
| 2882.TW | bull | tu_strategy | 9 | 9.32% |
| 2882.TW | bull | adaptive_tu_strategy | 9 | 9.93% |
| 2882.TW | bull | buy_and_hold | 9 | 521.11% |
| 2882.TW | bull | sma_8_21 | 9 | 99.79% |
| 2882.TW | bear | tu_strategy | 1 | 1.22% |
| 2882.TW | bear | adaptive_tu_strategy | 1 | 1.75% |
| 2882.TW | bear | buy_and_hold | 1 | -29.67% |
| 2882.TW | bear | sma_8_21 | 1 | -0.86% |
| 2882.TW | sideways | tu_strategy | 2 | -2.11% |
| 2882.TW | sideways | adaptive_tu_strategy | 2 | -3.87% |
| 2882.TW | sideways | buy_and_hold | 2 | -22.48% |
| 2882.TW | sideways | sma_8_21 | 2 | -31.58% |
| 1301.TW | bull | tu_strategy | 9 | -9.86% |
| 1301.TW | bull | adaptive_tu_strategy | 9 | -11.43% |
| 1301.TW | bull | buy_and_hold | 9 | 19.05% |
| 1301.TW | bull | sma_8_21 | 9 | -50.69% |
| 1301.TW | bear | tu_strategy | 1 | -2.79% |
| 1301.TW | bear | adaptive_tu_strategy | 1 | -4.94% |
| 1301.TW | bear | buy_and_hold | 1 | -10.92% |
| 1301.TW | bear | sma_8_21 | 1 | -14.12% |
| 1301.TW | sideways | tu_strategy | 2 | -4.15% |
| 1301.TW | sideways | adaptive_tu_strategy | 2 | -6.07% |
| 1301.TW | sideways | buy_and_hold | 2 | 3.86% |
| 1301.TW | sideways | sma_8_21 | 2 | -14.58% |
| 2002.TW | bull | tu_strategy | 9 | -15.05% |
| 2002.TW | bull | adaptive_tu_strategy | 9 | -12.52% |
| 2002.TW | bull | buy_and_hold | 9 | 43.64% |
| 2002.TW | bull | sma_8_21 | 9 | -34.07% |
| 2002.TW | bear | tu_strategy | 1 | 0.75% |
| 2002.TW | bear | adaptive_tu_strategy | 1 | 0.75% |
| 2002.TW | bear | buy_and_hold | 1 | -4.42% |
| 2002.TW | bear | sma_8_21 | 1 | 1.06% |
| 2002.TW | sideways | tu_strategy | 2 | -1.59% |
| 2002.TW | sideways | adaptive_tu_strategy | 2 | -1.59% |
| 2002.TW | sideways | buy_and_hold | 2 | -30.80% |
| 2002.TW | sideways | sma_8_21 | 2 | -29.43% |
| 1101.TW | bull | tu_strategy | 9 | -7.23% |
| 1101.TW | bull | adaptive_tu_strategy | 9 | -7.33% |
| 1101.TW | bull | buy_and_hold | 9 | 112.68% |
| 1101.TW | bull | sma_8_21 | 9 | 16.97% |
| 1101.TW | bear | tu_strategy | 1 | -0.53% |
| 1101.TW | bear | adaptive_tu_strategy | 1 | -0.53% |
| 1101.TW | bear | buy_and_hold | 1 | -20.22% |
| 1101.TW | bear | sma_8_21 | 1 | -3.64% |
| 1101.TW | sideways | tu_strategy | 2 | 1.10% |
| 1101.TW | sideways | adaptive_tu_strategy | 2 | 1.10% |
| 1101.TW | sideways | buy_and_hold | 2 | -31.58% |
| 1101.TW | sideways | sma_8_21 | 2 | -26.33% |
| 2105.TW | bull | tu_strategy | 9 | -8.20% |
| 2105.TW | bull | adaptive_tu_strategy | 9 | -6.34% |
| 2105.TW | bull | buy_and_hold | 9 | 1.30% |
| 2105.TW | bull | sma_8_21 | 9 | -36.44% |
| 2105.TW | bear | tu_strategy | 1 | -4.23% |
| 2105.TW | bear | adaptive_tu_strategy | 1 | -6.27% |
| 2105.TW | bear | buy_and_hold | 1 | -1.63% |
| 2105.TW | bear | sma_8_21 | 1 | -16.69% |
| 2105.TW | sideways | tu_strategy | 2 | -0.82% |
| 2105.TW | sideways | adaptive_tu_strategy | 2 | -0.82% |
| 2105.TW | sideways | buy_and_hold | 2 | -40.24% |
| 2105.TW | sideways | sma_8_21 | 2 | -34.46% |
| 1216.TW | bull | tu_strategy | 9 | -3.64% |
| 1216.TW | bull | adaptive_tu_strategy | 9 | -3.64% |
| 1216.TW | bull | buy_and_hold | 9 | 78.24% |
| 1216.TW | bull | sma_8_21 | 9 | -39.00% |
| 1216.TW | bear | tu_strategy | 1 | -1.84% |
| 1216.TW | bear | adaptive_tu_strategy | 1 | -1.84% |
| 1216.TW | bear | buy_and_hold | 1 | 0.71% |
| 1216.TW | bear | sma_8_21 | 1 | -11.01% |
| 1216.TW | sideways | tu_strategy | 2 | -2.42% |
| 1216.TW | sideways | adaptive_tu_strategy | 2 | -2.42% |
| 1216.TW | sideways | buy_and_hold | 2 | 22.62% |
| 1216.TW | sideways | sma_8_21 | 2 | -19.24% |
| 2412.TW | bull | tu_strategy | 9 | -1.19% |
| 2412.TW | bull | adaptive_tu_strategy | 9 | -1.69% |
| 2412.TW | bull | buy_and_hold | 9 | 90.64% |
| 2412.TW | bull | sma_8_21 | 9 | 19.29% |
| 2412.TW | bear | tu_strategy | 1 | -0.48% |
| 2412.TW | bear | adaptive_tu_strategy | 1 | -0.48% |
| 2412.TW | bear | buy_and_hold | 1 | 1.49% |
| 2412.TW | bear | sma_8_21 | 1 | -2.69% |
| 2412.TW | sideways | tu_strategy | 2 | -0.47% |
| 2412.TW | sideways | adaptive_tu_strategy | 2 | -0.84% |
| 2412.TW | sideways | buy_and_hold | 2 | 13.32% |
| 2412.TW | sideways | sma_8_21 | 2 | -9.06% |
| 2912.TW | bull | tu_strategy | 9 | -3.70% |
| 2912.TW | bull | adaptive_tu_strategy | 9 | -4.34% |
| 2912.TW | bull | buy_and_hold | 9 | 30.23% |
| 2912.TW | bull | sma_8_21 | 9 | -44.60% |
| 2912.TW | bear | tu_strategy | 1 | 0.03% |
| 2912.TW | bear | adaptive_tu_strategy | 1 | 0.03% |
| 2912.TW | bear | buy_and_hold | 1 | 1.50% |
| 2912.TW | bear | sma_8_21 | 1 | -12.00% |
| 2912.TW | sideways | tu_strategy | 2 | -2.82% |
| 2912.TW | sideways | adaptive_tu_strategy | 2 | -2.62% |
| 2912.TW | sideways | buy_and_hold | 2 | -5.39% |
| 2912.TW | sideways | sma_8_21 | 2 | -23.40% |

## 訓練窗參數敏感度

| 標的 | 候選數 | 可用訓練窗 | 平均報酬範圍 | 正平均報酬候選比率 |
|---|---:|---:|---:|---:|
| 0056.TW | 5 | 11 | 1.0018 個百分點 | 1.0 |
| 006208.TW | 5 | 11 | 1.0646 個百分點 | 1.0 |
| 2317.TW | 5 | 11 | 2.4491 個百分點 | 1.0 |
| 2454.TW | 5 | 11 | 6.9209 個百分點 | 1.0 |
| 2308.TW | 5 | 11 | 3.3909 個百分點 | 1.0 |
| 2881.TW | 5 | 11 | 2.1209 個百分點 | 0.2 |
| 2882.TW | 5 | 11 | 1.6072 個百分點 | 1.0 |
| 1301.TW | 5 | 11 | 2.6209 個百分點 | 0.0 |
| 2002.TW | 5 | 11 | 1.0764 個百分點 | 0.0 |
| 1101.TW | 5 | 11 | 1.7555 個百分點 | 0.0 |
| 2105.TW | 5 | 11 | 2.2191 個百分點 | 0.0 |
| 1216.TW | 5 | 11 | 0.6109 個百分點 | 0.0 |
| 2412.TW | 5 | 11 | 0.0691 個百分點 | 0.2 |
| 2912.TW | 5 | 11 | 1.7636 個百分點 | 0.0 |

## 資料品質

| 標的 | 狀態 | 筆數 | 範圍 | 最大單日變動 | 價格還原契約 |
|---|---|---:|---|---:|---|
| 0056.TW | available | 3069 | 2014-01-02 ~ 2026-07-31 | 9.9801% | yfinance auto_adjust=True; actions=False; repair=False |
| 006208.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 9.9807% | yfinance auto_adjust=True; actions=False; repair=False |
| 2317.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 10.4846% | yfinance auto_adjust=True; actions=False; repair=False |
| 2454.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 2308.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 2881.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 9.9861% | yfinance auto_adjust=True; actions=False; repair=False |
| 2882.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 9.9692% | yfinance auto_adjust=True; actions=False; repair=False |
| 1301.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 11.2544% | yfinance auto_adjust=True; actions=False; repair=False |
| 2002.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 1101.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 9.9702% | yfinance auto_adjust=True; actions=False; repair=False |
| 2105.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 10.0% | yfinance auto_adjust=True; actions=False; repair=False |
| 1216.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 9.0354% | yfinance auto_adjust=True; actions=False; repair=False |
| 2412.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 6.1033% | yfinance auto_adjust=True; actions=False; repair=False |
| 2912.TW | available | 3063 | 2014-01-02 ~ 2026-07-31 | 9.932% | yfinance auto_adjust=True; actions=False; repair=False |

## 解讀限制

- 資料涵蓋期間以本次快照與 data_provenance.csv 記錄的實際範圍為準。
- Walk-forward 視窗重設初始資金；彙總報酬以各連續視窗報酬率複合計算。
- 尚未納入股利、除權息還原差異、流動性衝擊與個股漲跌停成交限制。
- yfinance 為非官方資料供應路徑；完整抓取契約與資料雜湊見 data_provenance.csv。
- 供應商可能回溯修訂調整價；本次報告以內附 CSV 與 normalized_snapshot_sha256 固定版本。
- 參數敏感度不會自動挑選或套用最佳參數，避免驗證資料洩漏。
- adaptive profile 只改變配置比例，legacy 進退場訊號與風控規則維持不變。
