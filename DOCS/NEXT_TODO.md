# 專案進度與下階段待辦 (NEXT_TODO.md)

## 2026-08-01 研究模式產品邊界與分析核心改進

- [x] 正式建立 `DOCS/PRODUCT_BOUNDARY.md`：僅允許市場研究、歷史回測與虛擬模擬；禁止券商 SDK、真實委託、跟單及客戶資金管理。
- [x] 新增 `src/analysis_service.py`，統一 FastAPI、Streamlit 與每日排程器的分析契約。
- [x] 移除固定波浪點、固定成交量、股價推造 EPS/EVA 及前端假預設值；資料不足改回傳 `insufficient_data` / `not_applicable`。
- [x] 修復即時波浪 `as_of_date` 未完整傳遞到費氏時間窗的未來資料問題。
- [x] TTM EPS 改為驗證連續四季，並加入 150 日快取新鮮度檢查。
- [x] 負 EPS 不再產生負的 PE 目標價。
- [x] FinMind 殖利率加入 `percent` / `ratio` 明確單位 migration，內部統一使用比例。
- [x] CBC M1B 依 `available_at` 執行 as-of 查詢，不再假設期末日即可取得資料。
- [x] 均線扣抵欄位不完整時採 fail-closed，不產生共振訊號。
- [x] Backtrader 進場訊號納入 8/13/21/55/144 日扣抵斜率，結果明示 `historical_backtest_only` 與 `simulated_orders_only`。
- [x] FastAPI 靜態前端與 Streamlit 均顯示研究模式及非投資建議聲明。
- [x] 建立離線優先的固定快照與 walk-forward CLI：`tools/run_backtest.py`。
- [x] 同窗比較 TU 策略、買進持有及 SMA 8/21，納入手續費、證交稅、滑價、整張交易與訊號次日開盤執行。
- [x] 報告輸出 `report.json`、彙總／視窗／交易／品質 CSV、資料快照及人類可讀摘要；檔名包含資料與設定指紋。
- [x] 產出基準報告：`reports/backtest/baseline-v2-20260801-5cb3f28be410`（模擬資金 1,000 萬元，未進行參數最佳化）。
- [x] 驗證：`52 passed`；僅保留既有 FastAPI TestClient 第三方棄用警告。
- [x] 擴充 2014-01-02 至 2026-07-31 的長期調整價快照，三個標的各約 3,060 筆。
- [x] 新增 `DOCS/RESEARCH_DATA_CONTRACT.md`，保存來源、套件版本、抓取參數、雙 SHA-256 與供應商資料可變性契約。
- [x] Walk-forward 擴充為 12 個年度驗證窗，並依同窗 `^TWII` 報酬分為 9 個牛市、1 個熊市、2 個盤整窗。
- [x] 完成五組訓練窗參數敏感度；排名不套用驗證窗，避免資訊洩漏。
- [x] 長期報告：`reports/backtest/long-history-v1-20260801-eee3948d6fa6`。
- [x] 新增固定 snapshot 重播與執行前 SHA-256 驗證，避免供應商回溯修改污染比較。
- [x] 新增平均資金使用率，區分「有持倉天數」與「實際投入資金比例」。
- [x] 保留 legacy 訊號，完成訓練窗配置選型 `adaptive v1.1 balanced cap`。
- [x] adaptive v1.1 長期報告：`reports/backtest/adaptive-v1-1-20260801-193192967aed`。
- [x] aggressive adaptive v1 因 2330 最差 MDD 達 18.17%，僅保留 `reports/backtest/adaptive-v1-20260801-8353b098396a` 作失敗實驗證據。
- [x] adaptive v1.1 三標的報酬均高於 legacy，最差 MDD 分別為 4.82%、6.43%、11.63%；僅列為擴大驗證候選，未升格預設。

### 尚未完成／不得誤解為已完成

- [ ] CBC M1B 目前只有本地 SQLite 讀取與手動寫入，尚未實作官方遠端自動採集。
- [ ] EVA 因缺少完整 NOPAT、投入資本與流通股數資料契約，API 明確回傳 `unsupported`。
- [ ] 融資模組目前只計算觀察期融資餘額成長率；若要實作「融資相對大盤報酬率」，仍需確認正式業務公式。
- [ ] 長期資料、參數敏感度與行情分層已完成；目前只有一個熊市年度窗，熊市結論仍需更多歷史或其他標的驗證。
- [ ] yfinance 為非官方供應路徑，實測重新抓取時調整價末位小數可能回溯改變；重現既有結果須使用報告內固定快照。
- [ ] `^TWII` 無法直接交易，報告使用 ETF 稅率與單位的「指數代理成本模型」，不是可成交商品的精確重現。
- [ ] 回測尚未納入股利現金流、個股漲跌停、成交量容量與市場衝擊。
- [ ] adaptive v1.1 尚未跨更多 ETF、產業與中小型股驗證；legacy 仍是正式預設策略。
- [ ] 測試仍有 FastAPI TestClient 對 `httpx` 的第三方棄用警告，暫不影響功能。

### 建議下一步

1. 以 TWSE 原始行情抽樣交叉查核 snapshot，並評估是否建立官方原始價＋公司行動的自有調整流程。
2. 以固定 snapshot 擴充 ETF、產業龍頭及中小型股標的池，驗證 adaptive v1.1 是否跨標的成立。
3. 擴充熊市樣本，再判斷 adaptive 的低回撤效果是否穩健；通過前不得替換 legacy。
4. 完成 CBC 官方資料採集及融資指標的業務公式確認後，再擴充總體／籌碼判定。

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
