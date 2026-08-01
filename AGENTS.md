# AGENTS.md

本文件適用於整個 repository。若子目錄另有更具體的 `AGENTS.md`，在不違反本文件產品邊界、資料真實性與安全規則的前提下，以較具體規則為準。

## 1. 專案定位

本專案是一套以杜金龍台股分析理論為核心的：

**台股研究、情境推演與決策支援系統。**

主要目的：

1. 蒐集並標準化台灣大盤與個股公開資料。
2. 將杜金龍的均線扣抵、波浪、費波南希、成交量、市場資金及估值方法程式化。
3. 提供可隨時查詢的大盤與個股分析報告。
4. 提供價格區間、成立條件、失效條件及風險提示。
5. 保存每次分析結果，供後續回顧與模型驗證。
6. 協助使用者自行判斷，不取代使用者的投資決策。

本系統不是保證獲利或精準預言未來價格的工具。

執行任何工作前，必須同時遵守：

- `DOCS/PRODUCT_BOUNDARY.md`
- `DOCS/RESEARCH_DATA_CONTRACT.md`
- `DOCS/NEXT_TODO.md`

文件、程式與測試不一致時，不得自行選擇最方便的解釋；必須查證實際行為並指出差異。

---

## 2. 產品核心目標

系統應回答：

1. 現在市場與個股發生什麼變化？
2. 目前處於哪一種趨勢與波段階段？
3. 哪些資料支持目前判斷？
4. 依不同模型推算的合理價格區間為何？
5. 樂觀、基準與保守情境分別為何？
6. 哪些條件成立時，原分析可信度提高？
7. 哪些條件發生時，原分析失效？
8. 歷史上相似條件的結果分布如何？

系統不得只輸出未附理由的「買進、賣出、看多、看空」結論。

---

## 3. 杜金龍模型定位

本專案以杜金龍台股分析理論作為研究主軸，但任何規則是否具備核心資格，一律以 `config/model_rules.yaml` 的 Rule ID、證據等級與實作模式為準。不得因舊程式、舊文件或歷史回測存在，就將規則視為已驗證的杜金龍核心。

目前模型定位如下：

- 固定費氏均線群、固定均線共振、20／30／50、7%～11% 與 EVA 均屬 v1 legacy、experimental 或 unsupported，不得描述為 verified core。
- Forward EPS × PE 僅能依 `VAL-01`、`VAL-02`、`VAL-03`、`VAL-04` 各自的證據分類、核准條件及實作階段，決定是否進入 verified core。
- 已核准錨點後的 0.382／等幅推算，僅能依 `FB-04`／`FB-03` 的 Rule ID 與實作階段決定是否進入 verified core。
- 三等份 planner 僅能依 `ENT-02` 的 Rule ID 與實作階段決定是否進入 verified core，不得與 v1 legacy 的 20／30／50 配置混為一談。

現行 v1 legacy 部位語意是 **20%／50%／100% 累積配置**，等同分段增加約 20%／30%／50%；不得將累積比例誤解為每一階段各自投入比例，也不得據此宣稱為已驗證核心。Adaptive profile 只屬研究候選，未通過正式升格前不得取代 legacy 預設。

不得在沒有明確需求與驗證的情況下，擅自改變原模型語意、公式、階段條件或失效條件。

任何模型修改必須：

1. 說明修改原因。
2. 列出與原模型的差異。
3. 新增對應測試。
4. 保留舊版本、feature flag 或其他可回滾方式。
5. 不得為了提高歷史績效而任意調整參數。
6. 不得使用目前驗證窗選參數後，再把相同驗證窗宣稱為樣本外結果。

---

## 4. 預測與價格區間原則

系統可以提供：

- 技術目標區間
- 估值區間
- 支撐與壓力區間
- 樂觀、基準、保守情境
- 可能的時間轉折視窗

但不得將推算結果表述成確定事件。

正確表述：

```text
依目前已確認波浪錨點，基準推算區間為 980～1,030 元。
若突破前高且量能確認，樂觀延伸區可觀察 1,080 元附近。
若跌破 880 元及 SMA55，原主升段推算失效。
```

禁止表述：

```text
股價一定會漲到 1,080 元。
明天必然上漲。
系統準確預測最高點。
```

所有價格區間必須附上：

- 計算方法
- 使用資料日期
- 模型模式與版本
- 成立條件
- 失效條件
- 資料品質狀態

---

## 5. 不可變更的產品邊界

本系統只提供：

- 大盤與個股資料整理
- 技術面分析
- 基本面分析
- 籌碼分析
- 總體與市場資金分析
- 估值與情境推演
- 歷史回測
- 虛擬資金配置
- 分析報告與風險通知

本系統不得新增：

- 券商 API
- 真實證券帳戶登入
- 真實委託單送出
- 自動交易
- 跟單功能
- 客戶資金管理
- 保證報酬功能

Backtrader 中的 `buy()`、`sell()`、`close()` 與 `order` 僅限歷史回測及虛擬交易模擬，不得連接真實券商 API 或真實交易帳戶。

任何依賴、設定、環境變數或程式碼若出現券商連線或真實下單能力，必須停止工作並向使用者報告，不得默認保留。

---

## 6. 核心優先原則

在核心完成前，禁止優先擴充大型外層功能。

核心完成標準包括：

1. EPS 單季標準化與 TTM 計算正確。
2. M1B 不含固定代理值。
3. TWSE＋TPEx 市場成交金額來源正確。
4. 所有重要資料具備來源、日期、單位與狀態。
5. Realtime confirmed wave 與 hindsight visualization 完全分離。
6. 回測不存在已知 Look-Ahead Bias。
7. Stage 1／2／3 與平倉生命週期測試完整。
8. SQLite 缺口補抓、交易日曆與資料完整性判定正確。
9. API 資料不足時回傳 `insufficient_data`。
10. CI 在乾淨環境執行所有測試。

不得假定上述條件目前已全部通過；實際狀態以 `DOCS/NEXT_TODO.md`、最新測試與報告證據為準。目前 yfinance 長期快照仍存在交易日缺口與零量合成列，Phase A audited gate 尚未取得品質合格樣本。

只有核心驗收通過後，才能優先增加：

- 市場廣度
- 產業輪動
- 同業比較
- 新聞與重大事件
- 完整財務品質分析
- AI 自然語言報告
- PWA 進階介面
- 多使用者功能

---

## 7. 資料可信度原則

任何分析結果都不得使用未標示的假值、固定代理值或推測值。

禁止：

- 股價除以固定倍數作為 EPS。
- 資料抓取失敗時使用固定成交額。
- 無 M1B 資料時使用設定檔預設值。
- 將成交股數標示為 TWD 成交金額。
- 將衍生推算值標示為官方財報值。
- 使用尚未公布的未來財報進行歷史分析。
- 使用完整歷史區間高低點產生即時訊號。
- 將 yfinance 或其他第三方資料標示為 TWSE 官方資料。
- 未經稽核即自動填補缺失行情或覆寫既有快照。

資料缺失時必須回傳：

```json
{
  "status": "insufficient_data",
  "value": null,
  "reason": "..."
}
```

資料存在但完整性可疑時，必須回傳 `quality_warning` 或等效的 fail-closed 狀態，不得為了保持畫面完整而虛構數值。

---

## 8. 資料 Contract 要求

每個重要資料欄位應盡可能包含：

```json
{
  "value": 123.45,
  "status": "available",
  "source": "TWSE",
  "dataset": "dataset_name",
  "period": "2026-Q2",
  "data_date": "2026-06-30",
  "available_at": "2026-08-14",
  "fetched_at": "2026-08-15T10:00:00+08:00",
  "unit": "TWD",
  "is_cached": true,
  "is_stale": false
}
```

分析引擎不得直接從外部網站抓資料。外部資料必須先經過 Collector 或明確的資料稽核／匯入工具：

```text
External Source
→ Collector / Audit Import
→ Schema Validation
→ Unit Normalization
→ Database or Immutable Snapshot
→ Analysis Engine
→ API
```

研究快照必須保存 provider、抓取參數、版本、日期範圍、資料品質統計及 SHA-256。TWSE 官方補值只能逐筆新增缺失日期，必須保存原始 OHLCV、調整因子、因子錨點、推導方法及官方 URL；不得靜默覆寫既有列。

---

## 9. 分析模式分離

波浪與技術分析必須區分：

### Realtime Confirmed

- 只使用當時已知資料。
- Pivot 僅能在 `confirmed_at` 後使用。
- 適用於即時分析與歷史回測。
- 不得使用未來高低點。

### Hindsight Visualization

- 可以使用完整區間高低點。
- 僅用於事後圖表解說。
- 必須明確標示為 hindsight。
- 不得作為回測訊號或即時預測依據。

兩種模式不得共用未標示的 API 欄位。

---

## 10. 回測原則

所有回測必須：

- 避免 Look-Ahead Bias。
- 避免財報公告日期前視偏誤。
- 納入手續費、證交稅及滑價。
- 使用次日可成交價格，而非假設訊號產生前已成交。
- 使用固定、具 hash 且可重現的測試資料。
- 與買入持有及簡單基準策略比較。
- 分開統計多頭、空頭與盤整市場。
- 保存每筆交易的日期、價格、數量、費用及 Stage。
- 揭露資料完整性、供應商限制及不能成交的假設。

不得只以高報酬率宣稱策略有效。

必須同時提供：

- 樣本數
- 勝率
- 平均獲利
- 平均虧損
- 最大回撤
- Sharpe Ratio
- 持有期間
- 樣本外結果
- 模型版本
- 基準策略與成本模型
- 資料品質 gate

若資料品質 gate 未通過，即使策略完成計算，也不得宣稱模型通過驗證或升格。

---

## 11. 分析報告原則

完整報告最終應包含：

1. 資料日期與品質摘要
2. 大盤環境
3. 市場熱度與資金狀態
4. 個股基本面
5. 估值
6. 籌碼
7. 技術與波浪
8. 樂觀、基準、保守情境
9. 主要風險
10. 失效條件
11. 模型限制

在外層功能尚未完成時，可以只輸出已有模組，但必須清楚標示：

- 已完成部分
- 缺失部分
- 不可計算部分

不得以空泛文字填補缺失資料。

大型研究產物原則上保留於本機；`report.json`、交易明細、視窗明細與完整 snapshot 依 `.gitignore` 管理。Git 只提交足以重現與審查的設定、摘要、品質、provenance 與 gate 證據，除非使用者明確要求其他範圍。

---

## 12. 架構原則

推薦架構：

```text
PWA／Web Frontend
        ↓
FastAPI
        ↓
Analysis Services
        ↓
Normalized Database
        ↑
Collector Workers + Scheduler
```

PWA 只負責：

- 顯示分析
- 個股搜尋
- 自選股
- 圖表
- 快取最近報告
- 更新狀態

PWA 不得負責：

- 保存 API Key
- 爬取官方資料
- 財報標準化
- 執行完整回測
- 寫入核心金融資料庫

目前可使用 SQLite；多人或正式部署時再評估 PostgreSQL。資料庫遷移屬重大變更，必須先提出 migration、回滾與驗證計畫並取得核准。

---

## 13. Agent 工作流程

每次修改前必須：

1. 閱讀本文件及工作範圍內更具體的 `AGENTS.md`。
2. 閱讀相關規格文件。
3. 檢查 Git branch、status 與既有未提交變更。
4. 先檢查既有行為、資料契約與測試。
5. 說明問題根因與影響範圍。
6. 採用最小、可回滾修改。
7. 不得自行擴大任務範圍。
8. 不得為了通過測試而改變產品語意。

重大變更包括核心模型、資料格式／schema、API contract、認證安全、跨模組架構、破壞性操作及正式部署。重大變更必須先提交 Intervention Plan、風險、影響模組、回滾與驗證計畫，取得使用者明確核准後才能實作。

每次修改後必須回報：

### Assessment

問題根因與影響。

### Modified Files

逐檔說明修改內容。

### Behavior Changes

修改前後行為差異。

### Data Contract Changes

資料欄位、來源、單位及狀態變更；若無則明示無變更。

### Test Evidence

實際執行的測試、指令與結果。未執行項目不得描述為通過。

### Remaining Limitations

尚未完成或仍不可靠的部分。

### Product Boundary Check

確認未加入真實下單、自動交易或其他超出範圍的功能。

例行修改可使用較短格式，但上述證據不得省略到無法判斷結果。

---

## 14. Workspace、Git 與安全規則

- 未提交變更視為使用者所有，不得覆寫、reset 或清除。
- 不得修改與目前任務無關的檔案。
- 未經使用者要求，不得 commit、push、建立 PR、部署或修改外部服務。
- 不得提交 API key、token、帳密、個人資料或其他秘密。
- 禁止使用 `git reset --hard`、破壞性 checkout 或未經核准的資料刪除。
- Commit 必須範圍單一，提交前執行 `git diff --check`、檢查 staged scope，並確認大型 runtime 產物未誤入 Git。
- Windows 環境優先使用 `python -m pytest`、`npm.cmd` 與 `npx.cmd`；測試若因系統暫存權限失敗，必須區分環境錯誤與程式錯誤。
- 預設完整 Python 回歸指令為 `python -m pytest -q`；仍須依修改風險增加焦點測試、語法檢查、build 或手動重現。

---

## 15. 外部模型與 Antigravity 委派

Antigravity CLI 只可作為唯讀、受限範圍的顧問工具，不是最終決策者。

- 優先使用既有安全委派 skill／wrapper。
- 不得傳送 secrets、帳密、token、私人資料或未授權內容。
- 不得委派最終架構、模型語意、資料真實性、安全邊界、合併、發布或部署決策。
- 不得允許外部模型直接修改檔案、執行破壞性指令或進行外部寫入。
- 外部模型的每個重要結論都必須由本機程式碼、資料或測試獨立驗證。
- 委派失敗、未登入或受審核阻擋時，應安全地改由本機完成，不得降低保護措施。

---

## 16. 禁止宣稱

Agent 不得在沒有證據時使用：

- `100% production-ready`
- `完全無錯誤`
- `已完全解決`
- `精準預測`
- `保證獲利`
- `所有測試完整覆蓋`

只能依實際測試、人工對帳及 CI 證據描述完成程度。

---

## 17. 最高優先順序

當規則衝突時，依以下順序處理：

1. 平台／系統安全規則與明確法律、隱私限制
2. 資料真實性與不可虛構
3. 已確認的杜金龍模型語意
4. 無前視偏誤與可驗證性
5. 既有核心行為不可破壞
6. 任務明確需求
7. UI 與便利性
8. 效能優化
9. 額外功能

任何新增功能不得犧牲前五項。

---

## 18. Evidence-Based Model Rules

- Every implemented research rule must have a stable Rule ID and an entry in `config/model_rules.yaml`.
- Evidence levels are A, B, C, and U. Only A-level rules may be registered as `verified_core`.
- U-level rules must never enter verified core, a core score, or a Du-method claim.
- C-level rules must expose `project_operationalization: true`.
- Rules requiring human approval must return `needs_human_input` when no valid approval ID is supplied.
- Reports and API results must disclose model version, legacy state, evidence classification, and `official_affiliation: false` where applicable.
- Historical TTM EPS or a fixed growth rate must not be relabeled as Forward EPS.
- Existing fixed MA resonance, 20/30/50 allocation, 7%-11% pullback, and EVA floor calculations are legacy experimental or unsupported; they remain only for v1 compatibility and controlled historical research.
- Evidence reclassification requires source references, focused tests, and a versioned, reversible change. Do not overwrite an older rule version.
