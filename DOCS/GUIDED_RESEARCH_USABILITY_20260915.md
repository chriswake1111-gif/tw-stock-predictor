# 每日研究引導：第一批改善與驗證

日期：2026-09-15。基底 HEAD：1752089；工作樹含先前尚未提交的本機助理與假設引導變更。本文件記錄本次增量，並不代表這些變更已提交、發布或安裝。

## 問題與範圍

使用者已完成第一份台積電研究與筆記，接著授權依「新手研究首頁 → 家庭研究檢視流程 → 依缺口評估資料來源」改善。第一批以既有 API 整理閱讀及操作，不新增金融規則、資料表、付費來源或帳戶連線。

Codebase Memory MCP 的 list_projects 回傳 Transport closed，本次依實際檔案與測試定位。DOCS/NEXT_TODO.md 的頂端仍是舊 Phase 20 交班，不作為本次安裝驗收完成證據。

## 本次修改

- `frontend/src/components/ResearchOverview.tsx`、`ResearchOverview.css`：四個問題的摘要、日期與來源分列、明示缺項、術語說明、直接展開相關區段。手機先呈現行情，桌面並列事實與情境摘要。
- `frontend/src/components/ResearchSummaryCard.tsx`：保留既有標頭、審計入口與原呼叫相容性，允許新的引導內容。
- `frontend/src/pages/StockResearchPage.tsx`：串接摘要；假設設定預設收合；直接連結與圖表選點仍能展開；更新失敗時區分保留資料與全頁載入失敗。
- `frontend/src/components/ResearchModelResults.tsx`：依服務回傳的實際可用情境顯示；按 EPS 身分、年度、來源與數值篩選，不合併目標價、不改計算、不排除保存内容。補充模型與輸入版本。
- `frontend/src/pages/SearchHomePage.tsx`：區分搜尋失敗與零筆結果；可重試；忽略換字後的舊回應；搜尋結果與最近標的可使用鍵盤開啟。
- `frontend/src/components/DailyResearchJournal.tsx`：可選的空白研究筆記提示，包含時間尺度、採用情境、依據、缺項與重新評估條件；追加而不覆蓋既有文字，不自動保存。讀取中不再顯示成沒有前次研究。
- `frontend/src/components/HumanDecisionQueue.tsx`：無待確認事項採中性提示，避免暗示資料完整。
- `frontend/src/components/DailyPublicDataPanel.tsx`：價量、比率及資料日期依日期排序，不更動原資料；避免倒序回應把舊價標成最新；品質警告改用中文。
- 測試：新增 `guidedResearchFixture.ts`、`research-overview.test.tsx`、`guided-search.test.tsx`；擴充 `daily-public-data.test.tsx` 的倒序情境。

未改 API 契約、保存欄位、資料庫、金融公式、證據等級、核准或撤銷規則。已有核准不自動等同本次適用。自訂 PE 敏感度仍不能稱為已驗證的杜金龍固定倍數。沒有券商連線或交易能力變更。

## 驗證證據

| 檢查 | 實際結果 |
|---|---|
| `npm.cmd test -- --reporter=dot` | 15 個檔案、91 項通過 |
| `npm.cmd run build` | TypeScript、Vite 及 production bundle admin secret gate 通過 |
| `npm.cmd run lint` | 通過 |
| `python -m pytest -x -q --basetemp=.tmp_guided_diagnostic_20260915` | 完整收集並執行：1018 passed；沒有觸發 fail-fast |
| `node output/playwright/guided-research-smoke.cjs` | 1440、1024、768、360 px 全部通過 |
| 瀏覽器檢查 | 鍵盤搜尋、情境篩選、假設區段、直接連結、筆記追加、不自動核准／保存；無 pageerror，收合與展開皆無水平溢出 |
| 新摘要無障礙掃描 | axe WCAG 2 A/AA 規則無違規；只涵蓋 `.research-overview`，不宣稱整站已完成無障礙驗證 |

環境事項：最初測試／建置遇到既有快取權限限制，前端透過受控權限執行後通過；Python 改用新的隔離暫存路徑後通過。Python 的兩個警告為 Starlette/httpx 棄用提示與舊 pytest cache 不可寫，沒有測試失敗。未修改全域 ACL 或依賴版本。

Playwright CLI 未安裝且 npm registry 受限，改用專案既有 Playwright 套件執行隔離腳本；未新增依賴。所有 API 回應皆為 synthetic fixture，沒有讀写目前安裝版；筆記及核准寫入次數為零。只有既有 bootstrap 呼叫被測試攔截。

本機視覺證據：`output/playwright/guided-results.json`、`guided-*-first-screen.png`、`guided-*.png`。截圖為隔離測試資料，不能當成即時台積電研究。產物應留在本機，後續提交須精確選取範圍。

## 下一個檢查點

1. 安裝檔已產出，接著進行安裝版驗收；覆蓋目前安裝版與 commit/push 仍依交付時的明確授權執行。本次沒有更動目前安裝版。
2. 讓使用者與太太以自己的持有期間與觀察條件，完成新標的 → 看缺項 → 確認假設 → 保存 → 找回前次筆記。現在只提供可選筆記引導，沒有新增買賣建議或資金配置模型。
3. 補齊真實來源矩陣：上市、上櫃、短歷史、無估值及來源故障。中文簡稱來源更新、實際財報股數基準／TTM、歷史事件時間證據等仍需各自驗證；本次改善閱讀，不宣稱資料缺口已補齊。
4. 維持原先至少三個連續交易日、休市／尚未發布及使用者獨立操作驗收門檻，再判斷每日可用程度。

## 資料訂閱評估順序

先處理已公布財報、股數基準、日期與修訂的公開來源接入及核對；不能以購買資料代替標準化驗證。外部全年 EPS 預估與歷史實績分開管理。

只有公開來源確實不足時，才比較候選服務的：上市／上櫃與標的涵蓋、預估年度、原始發布機構、更新與修訂頻率、歷史版本保存、程式存取與保存授權、成本。尚未購買或選定服務；目前沒有足夠證據表示必須申請券商帳號。

## 安裝檔交付（2026-09-15）

依使用者要求產出安裝檔，未執行安裝、覆蓋目前程式或提交程式碼。建置識別為 `1752089-guided-working-20260915`，明確包含尚未提交的已驗證工作樹內容；AppVersion 沿用 `1.0.0`。

- 安裝檔：`dist/windows-guided-research-20260915/installer/tw-stock-predictor-setup.exe`。
- 大小：136,037,124 bytes。
- SHA-256：`f7d74ba2ef018d496441d5649fec57a282613bf96ceabd63c83b016ba9a0a6da`。
- 沿用既有 Python／PyInstaller／Inno Setup 環境，輸出至新目錄；前端沿用上列測試通過且未再修改的正式建置資源。
- 三個執行檔的 `--help` 均 exit 0，三份包內前端各 3 個檔案與已驗證版本 SHA-256 一致；證據為輸出目錄的 `executable-checks.json`。
- `tools/validate_windows_package.py` 搭配 `distribution-manifest.json` 回傳 exit 0、`status: valid`；驗證 30 個資源、25 個 migration、研究工具與 Skill、前端 secret gate 及安裝檔／執行檔雜湊。

本次未啟動使用者安裝版，未改動資料庫、研究紀錄或筆記。套件驗證不等同乾淨機器安裝、既有資料升級或跨日驗收通過；這些檢查仍保留。安裝前應先使用開始選單的 `Stop TW Stock Predictor` 停止舊程式，再執行新安裝檔。
