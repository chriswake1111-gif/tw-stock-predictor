# 專案進度與下階段待辦 (NEXT_TODO.md)

## 📌 當前開發進度 (Current Progress)
- [x] **Phase 1: 基礎設施與數據採集模組**
  - [x] 專案目錄結構建置 (`config/`, `data/`, `src/collectors/`, `src/engine/`, `src/strategy/`, `src/ui_alert/`, `tools/`, `tests/`)
  - [x] 依賴管理 (`requirements.txt`) 與系統配置 (`config/config.yaml`)
  - [x] 實作 Local-First SQLite 數據快取層 (`data/cache.db`)
  - [x] 實作 [TWSECollector](file:///d:/Tools/tw-stock-predictor/src/collectors/twse_collector.py) (yfinance K 線與大盤融資)
  - [x] 實作 [FinMindCollector](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py) (個股籌碼、PE/PB 估值與 EPS 財報)
  - [x] 編寫單元測試 [tests/test_collectors.py](file:///d:/Tools/tw-stock-predictor/tests/test_collectors.py) (4/4 PASS)
  - [x] 編寫 CLI 驗證輔具 [tools/test_collectors.py](file:///d:/Tools/tw-stock-predictor/tools/test_collectors.py)

- [x] **Phase 2: 技術面與波浪/扣抵計算核心**
  - [x] 參數解耦：`config/config.yaml` 設定 $P_0, P_1, P_2$ 波浪點位與歷史基準日
  - [x] 實作 [src/engine/wave_fibonacci.py](file:///d:/Tools/tw-stock-predictor/src/engine/wave_fibonacci.py) (浪 3 / 浪 5 目標價推導與費氏時間轉折視窗判定，分交易日/日曆月)
  - [x] 實作 [src/engine/ma_deduction.py](file:///d:/Tools/tw-stock-predictor/src/engine/ma_deduction.py) (費氏均線群 8, 13, 21, 55, 144, 233、向量化扣抵預判與多空共振檢測器)
  - [x] 編寫單元測試 [tests/test_wave_calc.py](file:///d:/Tools/tw-stock-predictor/tests/test_wave_calc.py) 與 [tests/test_ma_deduction.py](file:///d:/Tools/tw-stock-predictor/tests/test_ma_deduction.py)
  - [x] 編寫 CLI 驗證輔具 [tools/test_engine.py](file:///d:/Tools/tw-stock-predictor/tools/test_engine.py)

- [x] **Phase 3: 基本面估值與市場熱度模組**
  - [x] 實作 [src/collectors/cbc_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/cbc_collector.py) (央行 M1B 快取與 270,000 億基準值降級備援)
  - [x] 實作 [src/engine/valuation_eva.py](file:///d:/Tools/tw-stock-predictor/src/engine/valuation_eva.py) (主人與小狗估值模型、預估 EPS 雙軌機制、WACC 7% EVA 長線底盤與二低一高/破底翻選股器)
  - [x] 實作 [src/engine/market_sentiment.py](file:///d:/Tools/tw-stock-predictor/src/engine/market_sentiment.py) (大盤成交金額 / M1B 比率頭部天量警戒與全市場融資槓桿過熱溫度計)
  - [x] 編寫單元測試 [tests/test_valuation_eva.py](file:///d:/Tools/tw-stock-predictor/tests/test_valuation_eva.py) 與 [tests/test_market_sentiment.py](file:///d:/Tools/tw-stock-predictor/tests/test_market_sentiment.py) (17/17 PASS)
  - [x] 編寫 CLI 驗證輔具 [tools/test_sentiment_valuation.py](file:///d:/Tools/tw-stock-predictor/tools/test_sentiment_valuation.py)

---

## 🎯 下階段待辦 (NEXT_TODO: Phase 4)
- [ ] **Phase 4: 交易策略整合與 Backtrader 歷史回測**
  - [ ] 實作 `src/strategy/capital_allocation.py` (20%/30%/50% 分批建倉、7%~11% 拉回加碼與箱型網格策略)
  - [ ] 實作 `src/strategy/backtester.py` (Backtrader 事件驅動歷史回測引擎與勝率/MDD 報告生成)
  - [ ] 編寫 Phase 4 回測驗證腳本與單元測試
