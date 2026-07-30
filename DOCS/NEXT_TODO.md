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
  - [x] 參數解耦：[config/config.yaml](file:///d:/Tools/tw-stock-predictor/config/config.yaml) 設定 $P_0, P_1, P_2$ 波浪點位與歷史基準日
  - [x] 實作 [src/engine/wave_fibonacci.py](file:///d:/Tools/tw-stock-predictor/src/engine/wave_fibonacci.py) (浪 3 / 浪 5 目標價推導與費氏時間轉折視窗判定，分交易日/日曆月)
  - [x] 實作 [src/engine/ma_deduction.py](file:///d:/Tools/tw-stock-predictor/src/engine/ma_deduction.py) (費氏均線群 8, 13, 21, 55, 144, 233、向量化扣抵預判 $P(t) > P(t-N+1)$ 與多空共振檢測器)
  - [x] 編寫單元測試 [tests/test_wave_calc.py](file:///d:/Tools/tw-stock-predictor/tests/test_wave_calc.py) 與 [tests/test_ma_deduction.py](file:///d:/Tools/tw-stock-predictor/tests/test_ma_deduction.py) (9/9 PASS)
  - [x] 編寫 CLI 驗證輔具 [tools/test_engine.py](file:///d:/Tools/tw-stock-predictor/tools/test_engine.py)

---

## 🎯 下階段待辦 (NEXT_TODO: Phase 3)
- [ ] **Phase 3: 基本面估值與市場熱度模組**
  - [ ] 實作 `src/engine/valuation_eva.py`
    - 主人與小狗估值模型：$\text{合理目標價} = \text{法人預估未來一年 EPS} \times \text{近 5 年歷史平均 PE}$
    - EVA 長線價值底盤推算 (企業稅後淨利, WACC 資金成本)
    - 二低一高選股器 ($\text{PE} < \text{市場平均}$ 且 $\text{PB} < 1.5$ 且 $\text{殖利率} > 4\%$)
  - [ ] 實作 `src/engine/market_sentiment.py`
    - 大盤頭部天量預警 (上市櫃總成交量 / 央行 M1B 貨幣供給)
    - 槓桿過熱指標 (全市場融資餘額增幅與大盤漲幅比率)
  - [ ] 編寫 Phase 3 單元測試與 CLI 診斷工具
