# 專案進度與下階段待辦 (NEXT_TODO.md)

## 📌 當前開發進度 (Current Progress - 機構級金融終端全數上線!)
- [x] **Phase 1: 基礎設施與數據採集模組**
  - [x] 專案目錄結構建置與 Local-First SQLite 數據快取層 (`data/cache.db`)
  - [x] 實作 [TWSECollector](file:///d:/Tools/tw-stock-predictor/src/collectors/twse_collector.py) 與 [FinMindCollector](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py)

- [x] **Phase 2: 技術面與波浪/扣抵計算核心**
  - [x] 實作 [src/engine/wave_fibonacci.py](file:///d:/Tools/tw-stock-predictor/src/engine/wave_fibonacci.py) (浪 3 / 浪 5 目標價推導與費氏時間轉折視窗)
  - [x] 實作 [src/engine/ma_deduction.py](file:///d:/Tools/tw-stock-predictor/src/engine/ma_deduction.py) (費氏均線群、向量化扣抵與多空共振檢測器)

- [x] **Phase 3: 基本面估值與市場熱度模組**
  - [x] 實作 [src/collectors/cbc_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/cbc_collector.py) (央行 M1B 快取與 270,000 億基準值降級備援)
  - [x] 實作 [src/engine/valuation_eva.py](file:///d:/Tools/tw-stock-predictor/src/engine/valuation_eva.py) (主人與小狗估值、雙軌 EPS、EVA 長線底盤與二低一高/破底翻選股器)
  - [x] 實作 [src/engine/market_sentiment.py](file:///d:/Tools/tw-stock-predictor/src/engine/market_sentiment.py) (成交量/M1B 天量過熱與全市場融資槓桿過熱)

- [x] **Phase 4: 交易策略整合與 Backtrader 歷史回測**
  - [x] 實作 [src/strategy/capital_allocation.py](file:///d:/Tools/tw-stock-predictor/src/strategy/capital_allocation.py) (整張 1,000 股換算、20/30/50 建倉與 >1.5% 向下跳空開盤防禦)
  - [x] 實作 [src/strategy/backtester.py](file:///d:/Tools/tw-stock-predictor/src/strategy/backtester.py) (台股手續費打折與 0.3% 證交稅模型，完成 +160.75% / 勝率 83.33% 回測戰績)

- [x] **Phase 5 (方案 A): 機構級金融終端 (FastAPI + HTML5 / Tailwind + TradingView)**
  - [x] 實作 [src/api/main.py](file:///d:/Tools/tw-stock-predictor/src/api/main.py) (FastAPI REST API 端點 `/api/analysis/{symbol}`、Lifespan 整合 14:30 APScheduler 排程器、CORS 與靜態目錄 `/` 根掛載)
  - [x] 實作 [src/ui_alert/web/index.html](file:///d:/Tools/tw-stock-predictor/src/ui_alert/web/index.html) (Tailwind CSS 雙主題高質感面板、KPI 指標牌與 TradingView 圖表容器)
  - [x] 實作 [src/ui_alert/web/app.js](file:///d:/Tools/tw-stock-predictor/src/ui_alert/web/app.js) (TradingView Lightweight Charts K 線、均線扣抵標記、費氏時間帶與估值通道渲染)
  - [x] 實作 [src/ui_alert/web/style.css](file:///d:/Tools/tw-stock-predictor/src/ui_alert/web/style.css) (Glassmorphism 視覺美化)
  - [x] 編寫單元測試 [tests/test_api.py](file:///d:/Tools/tw-stock-predictor/tests/test_api.py) (26/26 PASS)
  - [x] 啟動 uvicorn 高效能 API 伺服器 ([http://localhost:8000](http://localhost:8000))
