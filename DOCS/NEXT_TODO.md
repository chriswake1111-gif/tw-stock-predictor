# 專案進度與下階段待辦 (NEXT_TODO.md)

## 📌 當前開發進度 (Current Progress)
- [x] **Phase 1: 基礎設施與數據採集模組**
  - [x] 專案目錄結構建置 (`config/`, `data/`, `src/collectors/`, `src/engine/`, `src/strategy/`, `src/ui_alert/`, `tools/`, `tests/`)
  - [x] 依賴管理 (`requirements.txt`) 與系統配置 ([config/config.yaml](file:///d:/Tools/tw-stock-predictor/config/config.yaml))
  - [x] 實作 Local-First SQLite 數據快取層 (`data/cache.db`)
  - [x] 實作 [TWSECollector](file:///d:/Tools/tw-stock-predictor/src/collectors/twse_collector.py) (yfinance K 線與大盤融資)
  - [x] 實作 [FinMindCollector](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py) (個股籌碼、PE/PB 估值與 EPS 財報)
  - [x] 編寫單元測試 [tests/test_collectors.py](file:///d:/Tools/tw-stock-predictor/tests/test_collectors.py) (4/4 PASS)
  - [x] 編寫 CLI 驗證輔具 [tools/test_collectors.py](file:///d:/Tools/tw-stock-predictor/tools/test_collectors.py)

---

## 🎯 下階段待辦 (NEXT_TODO: Phase 2)
- [ ] **Phase 2: 技術面與波浪/扣抵計算核心**
  - [ ] 實作 `src/engine/wave_fibonacci.py`
    - 波浪目標價演算（黃金分割比率 1.382, 1.618, 2.0, 2.618 主升段與 5 浪目標價）
    - 費波南希時間轉折視窗判定（8, 13, 21, 34, 55, 89, 144 天/月）
  - [ ] 實作 `src/engine/ma_deduction.py`
    - 費氏多空均線群（SMA 8, 13, 21, 55, 144, 233）計算
    - 均線扣抵值預判與多空共振檢測器
  - [ ] 編寫 Phase 2 單元測試 (`tests/test_wave_calc.py`, `tests/test_ma_deduction.py`)
