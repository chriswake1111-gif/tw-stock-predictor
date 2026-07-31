# 專案進度與下階段待辦 (NEXT_TODO.md)

## 📌 當前開發進度 (Current Progress - Code Review Plan 2 生產級重構完成!)
- [x] **第二次 Code Review 改進計畫 (Plan 2 Complete)**
  - [x] **P0: 零假資料與真 TTM EPS ([finmind_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py))**
    - 徹底刪除 `latest_close / pe` 假算式。實作 `get_ttm_eps()` 加總近 4 季已公告單季 EPS，並標註 `period_end` 與 `available_at`，單位 `TWD_per_share`。不足 4 季統一回傳 `insufficient_data`。
  - [x] **P0: 真實 TWSE+TPEx 總成交金額 ([market_turnover_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/market_turnover_collector.py))**
    - 新增 `MarketTurnoverCollector` 採集官方總成交金額 (`traded_value`)，範疇 `TWSE+TPEx`，單位 `TWD`。實作 `get_latest_available_turnover(end_date, lookback_days=10)` 向前追溯 10 日內最新有效交易日。
  - [x] **P0: 完全刪除固定假資料與備援 ([market_sentiment.py](file:///d:/Tools/tw-stock-predictor/src/engine/market_sentiment.py))**
    - 徹底移除 `else 4500.0` 與任何預設備援值，重構為 `turnover_m1b_ratio = market_turnover_twd / m1b_twd`。數據不足時統一輸出 `status: "insufficient_data"`。
  - [x] **P1: 波浪 No-Repaint 即時與事後模式拆分 ([wave_fibonacci.py](file:///d:/Tools/tw-stock-predictor/src/engine/wave_fibonacci.py))**
    - 實作 `get_realtime_confirmed_wave_params()` 僅使用 `confirmed_at <= as_of_date` 之已確認轉折點（Prefix Invariance 原則）。保留 `get_hindsight_wave_params()` 附帶圖表 Warning 標記。
  - [x] **P1: 修正 Stage 3 量能條件 ([backtester.py](file:///d:/Tools/tw-stock-predictor/src/strategy/backtester.py))**
    - 新增 5 日成交量均線 `sma5_vol`，Stage 3 量能門門條件修正為 `5日均量 > 20日均量` (`self.sma5_vol[0] > self.sma20_vol[0]`)。
  - [x] **P2: 測試套件升級 (27 passed, 100% PASS)**
    - 新增 `MarketTurnoverCollector` 單元測試與波浪 Prefix Invariance 確定性測試。

- [x] **GitHub 遠端公開倉庫同步**
  - GitHub Repository URL: [https://github.com/chriswake1111-gif/tw-stock-predictor](https://github.com/chriswake1111-gif/tw-stock-predictor)
