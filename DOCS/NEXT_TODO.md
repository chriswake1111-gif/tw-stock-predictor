# 專案進度與下階段待辦 (NEXT_TODO.md)

## 📌 當前開發進度 (Current Progress - Code Review 98%+ 生產級重構完成!)
- [x] **Code Review 全面重構與邏輯鎖定 (Production-Ready Refactoring)**
  - [x] 解鎖 Stage 1 循環依賴：實作 `pending_campaign_base_value` 雙階鎖與 `notify_order()` 成交確認機制。
  - [x] 修正 Stage 3 突破條件：實作 `pre_pullback_high` 拉回前高凍結機制與 `pullback_invalidated` 11% 門閥防護。
  - [x] 實作 Campaign-Base 累計持倉算術 ([capital_allocation.py](file:///d:/Tools/tw-stock-predictor/src/strategy/capital_allocation.py))，包含台股 1,000 股倍數轉換與 0.5% 現金/滑價緩衝。
  - [x] 實作無 Repaint 前視偏誤波浪轉折演算法 ([wave_fibonacci.py](file:///d:/Tools/tw-stock-predictor/src/engine/wave_fibonacci.py))，包含 `confirmed_at` 延遲發布。
  - [x] 升級 API JSON Contract ([main.py](file:///d:/Tools/tw-stock-predictor/src/api/main.py))：規範 `TWD_per_share` 與 `TWD` 單位、`TWSE+TPEx` 成交額範疇、`sum_latest_four_reported_quarter_eps` 算式，並將 EVA 標註為 `unsupported`。
  - [x] 升級 SQLite 交易日曆補抓 ([twse_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/twse_collector.py))：基於 `^TWII` 權威交易日曆比對 `expected_dates - cached_dates` 集合差集，並自動套用 yfinance `+1 day` 邊界。
  - [x] 升級單元測試集 ([tests/](file:///d:/Tools/tw-stock-predictor/tests/))：全套 25 項測試 **100% PASS**。

- [x] **GitHub 遠端公開倉庫同步**
  - GitHub Repository URL: [https://github.com/chriswake1111-gif/tw-stock-predictor](https://github.com/chriswake1111-gif/tw-stock-predictor)
