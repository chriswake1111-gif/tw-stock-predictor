# 專案進度與下階段待辦 (NEXT_TODO.md)

## 📌 當前開發進度 (Current Progress - Fourth Code Review 完成)
- [x] **第四次 Code Review 全面巡檢與極限邊界優化 (Fourth Code Review Complete)**
  - [x] **P1: 單季 EPS 負值/單季虧損邊界扣減演算法修復 ([src/collectors/finmind_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py))**
    - 修正當公司出現單季虧損或單季 EPS 為負值時，`q3` / `q4` 扣減累計值不會因遞減而誤觸退回全年累積 EPS 的 Edge Case。
    - 補齊單單元測試 `test_finmind_normalize_quarterly_eps_with_negative_quarter` ([tests/test_collectors.py](file:///d:/Tools/tw-stock-predictor/tests/test_collectors.py))，全套 26 個單元測試 100% 通過。
  - [x] **第三次 Code Review 全面修復 (Plan 3 Complete)**
  - [x] **P0: 刪除 CBC M1B 所有假資料與預設值 ([src/collectors/cbc_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/cbc_collector.py))**
    - 徹底移除 `default_m1b_billion` 與 `270000.0` 預設值，改為回傳 Contract（含 `value`, `period`, `available_at`）。無資料時輸出 `status: "insufficient_data"`。
  - [x] **P0: 實作財報單季 EPS 標準化演算法 `normalize_quarterly_eps` ([src/collectors/finmind_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py))**
    - 正確將累計財報轉算為單季 EPS（$Q1, Q2_{cum}-Q1, Q3_{cum}-Q2_{cum}, Annual-Q3_{cum}$），保留最新更正版，須集齊連續 4 季單季紀錄方可算 TTM EPS，否則回傳 `status: "insufficient_data"`。
  - [x] **P1: 真正加入 SMA5 Volume 修正 Stage 3 ([src/strategy/backtester.py](file:///d:/Tools/tw-stock-predictor/src/strategy/backtester.py))**
    - 新增 5 日均量 `self.sma5_vol`，Stage 3 量能判斷明確改為 `self.sma5_vol[0] > self.sma20_vol[0]`。
  - [x] **P1: API 波浪目標價結構分離與頂層模糊欄位移除 ([src/api/main.py](file:///d:/Tools/tw-stock-predictor/src/api/main.py))**
    - 拆分為 `wave_analysis.realtime_confirmed` (包含其獨立 `targets` 與 `time_window`) 與 `wave_analysis.hindsight_visualization` (包含 Warning)，移除頂層未標示 `wave_targets`。
  - [x] **P1: Realtime Pivot 順序與交替 Sequence 驗證 ([src/engine/wave_fibonacci.py](file:///d:/Tools/tw-stock-predictor/src/engine/wave_fibonacci.py))**
    - 要求 $index(P0) < index(P1)$ 且 $price(P0) < price(P1)$。$P2$ 投影值顯式標註 `"type": "derived_projection"` 與 `"formula"`。刪除全區間 `12629.0`, `15475.0` 預設值。
  - [x] **P1: 官方 JSON 數據解析端點單元測試與 Fixtures ([tests/fixtures/](file:///d:/Tools/tw-stock-predictor/tests/fixtures/))**
    - 新增 `twse_fmtqik_response.json` 與 `tpex_st41_response.json` 官方格式 Mock Fixtures，驗證 `MarketTurnoverCollector` 正確解析。
  - [x] **P2: GitHub Actions CI Workflow ([.github/workflows/ci.yml](file:///d:/Tools/tw-stock-predictor/.github/workflows/ci.yml))**
    - 新增 GitHub Actions CI 設定檔，每次 push/PR 自動執行 pytest 驗證。

- [x] **GitHub 遠端公開倉庫**
  - GitHub Repository URL: [https://github.com/chriswake1111-gif/tw-stock-predictor](https://github.com/chriswake1111-gif/tw-stock-predictor)
