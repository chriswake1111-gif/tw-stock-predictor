# 第一版研究引導：驗證紀錄

2026-09-28 開發驗收時點的紀錄；基底 HEAD `06dfde9811a4b19ce2730e35abfe53c745a6becd` 加本批變更。下列尚未安裝／發布的敘述指開發驗收時點，後續 1.1.0 交付以 GitHub Release 與本機安裝證據為準。未把其他既有本機修改視為本批交付。

## Assessment

既有程式以金融引擎的資料不足／人工輸入狀態直接引導假設表單，使用者難以區分程式更新、助理查證、具體選擇及資料能力缺口。本批新增獨立呈現判定與查證工作紀錄，原金融結果和核准資格保留。

## Modified Files

| 範圍 | 檔案及作用 |
|---|---|
| 格式／遷移 | `src/domain/research_evidence.py`、`migrations/20260928_27_research_evidence.sql`、`src/repositories/migration_runner.py`：版本紀錄、請求去重、容量及欄位驗證 |
| 查證／引導 | `src/services/research_evidence_service.py`、`src/services/research_guidance_service.py`：追加／查閱、查找沿用、缺項責任、候選資格 |
| 保存／假設 | `src/services/daily_research_journal_service.py`、`src/services/local_assumption_service.py`：候選精確綁定、來源版本追溯、內容指紋與固定保存 |
| 本機介面 | `src/api/main.py`、`src/api/routes/{research_evidence,daily_journal,local_assumptions,runtime}.py`、`src/api/workflow_security.py`：路由、能力及原有本機防護 |
| 研究工具 | `src/research_assistant/{client,cli}.py`：查證命令、年度選擇、保存引導內容，沒有核准／撤銷命令 |
| 前端 | `frontend/src/components/GuidedResearchWorkspace.{tsx,css}`、`frontend/src/api/guidanceClient.ts`、`frontend/src/api/researchClient.ts`、`frontend/src/pages/StockResearchPage.tsx`：三層介面及保存流程 |
| 共用結果呈現 | `frontend/src/components/ResearchModelResults.tsx`：空結果防護；多份比較使用獨立表單識別，不改計算 |
| 文件／技能 | 本規格、資料契約、驗證紀錄、兩份研究技能及其候選／紀錄參考文件 |
| 測試 | 新增 `tests/test_research_guidance.py`、前端 fixture／component tests、`frontend/e2e/research-guidance.spec.ts`；更新遷移版本、工具與原入口更新測試 |

## Behavior / Data Contract

新增 `research_guidance_v1`、兩個獨立查證資料表、預覽的引導及比較指紋、假設的候選引用、正式研究的可選固定工作內容。原有請求保持相容；新引導保存要求預覽指紋。詳細欄位及功能回復方式見 [規格](RESEARCH_GUIDANCE_V1.md)。

查證、草稿、核准、正式研究保存各自獨立。沒有合格來源可以保存部分研究；股數口徑不足不要求使用者核准正確性。舊研究、舊假設與舊核准在隔離遷移副本中逐表保持原值。

## Test Evidence

| 驗證 | 結果／範圍 |
|---|---|
| 完整 Python 回歸 | **PASS：1,078 passed**；199.16 秒；含最終候選引用、型別驗證及年度引導；一個既有 Starlette/httpx 棄用警告 |
| 前端測試 | **PASS：19 個測試檔、130 passed**；包含無模型資料、保存重送、讀回失敗、歷史唯讀、候選版本錯誤與來源文字 |
| 四種寬度與鍵盤 | 360／768／1024／1440：首頁搜尋 → 可讀資料 → 略過候選 → 不填技術數字 → 預覽完整筆記 → 確認部分保存 |
| 候選流程 | 從工具使用的 `#local-assumptions` 入口開啟；預覽／建草稿／核准各自按鈕；未自動保存研究 |
| 瀏覽器檢查 | **PASS：五個流程通過**；頁面例外與 console error 空集合；四種寬度沒有水平頁面溢位；已測主畫面 axe 掃描無違規 |
| 遷移／回復 | 隔離舊資料庫 backup 副本升級；舊表內容對帳、遷移重跑、關閉功能保留歷史均通過 |
| 容量／安全 | 超額拒寫而可讀、不可覆寫／刪除、相同請求重送、跨股票／年度錯配、原文不可讀／衝突、來源文字不執行、來源與防偽造請求限制均有測試 |
| 前端型別／檢查／建置 | **PASS**：typecheck、lint、build；最終 build 內再次執行 TypeScript 檢查與 production_bundle_admin_secret_gate=PASS |
| 差異檢查 | **PASS**：git diff --check；既有 NEXT_TODO.md 變更及其他本機產物未納入本批修改 |

Python 執行命令為 `python -m pytest -q --tb=short --basetemp=.tmp_guidance_full_20260928_3 -o cache_dir=.tmp_guidance_cache_20260928`。固定資料測試未連線真實券商或操作正式研究。

前端測試命令 `npm.cmd test -- --reporter=dot`。瀏覽器使用既有 Playwright 設定與 Microsoft Edge，命令 `npm.cmd run test:visual -- research-guidance.spec.ts --project=desktop --workers=1 --output=../output/playwright/research-guidance-20260928-release-check`；四種尺寸由測試明確設定。自動測試只代表上述範圍，不代表完整輔助科技相容或使用者驗收。

初次執行發生本機測試暫存權限錯誤，調整為允許測試快取寫入的執行環境後通過。測試開發中另修正新遷移識別、瀏覽器測試網址攔截範圍及非同步資料等待；未透過停用資料驗證取得通過。

最終前端產物位於 `frontend/dist/`（未安裝），入口資產 `index-BqPGSB_Z.js`、`index-CjVlDrfa.css`。畫面截圖位於 `output/playwright/research-guidance-20260928-release-check/`。固定測試資料僅用於測試，不進入正式前端 bundle 或使用者研究。

## Remaining Limitations / Product Boundary

正式安裝版升級、真實資料連線的一體操作及使用者本人易用性驗收 **NOT_RUN**。本次畫面使用固定介面回應；後端使用實際隔離 SQLite／TestClient 驗證，兩者不冒稱為正式安裝全流程測試。

過去一年獲利口徑補強、波浪自動辨識仍為後續工程。不保證每檔股票均可完整估值。查證區 64 MiB 為含預留的邏輯配額，整個 SQLite／WAL 物理大小不是相同上限；明確保存的研究仍會增加磁碟使用，磁碟稽核為 **Medium**。

未增加真實帳戶、券商連線、自動下單、交易保證、背景搜尋或外部寫入。沒有正式安裝、提交、推送或發布。沒有替使用者核准或保存個人研究。

# Design Review Report

Overall Score: 8/10

採 Guided Professional 密度，依 design-reviewer 的 System UI 檢視。此為本機主流程的專家／自動檢視，不是一般投資人受試者驗收。

## Visual Hierarchy
Score: 8/10
Issues:
- 360 像素的研究發現與下一步需要捲動後閱讀，首屏以公司、資料日期與可用行情為主。
Recommendations:
- 保留三層導覽；使用者試用時觀察是否需要把下一步再提前，暫不隱藏日期或缺項。

## Layout & Spacing
Score: 8/10
Issues:
- `.guidance-columns` 在手機改為直向，閱讀長度增加；沒有頁面水平溢位。
Recommendations:
- 延續現有間距及卡片邊界；長表格只在自身容器捲動。

## Typography
Score: 8/10
Issues:
- 完整證據保留進階術語，快速摘要與操作按鈕使用中文說明。
Recommendations:
- 依後續使用者回饋調整說明，不把原始資料狀態隱藏成安全或中性。

## Color & Contrast
Score: 8/10
Issues:
- 沿用系統藍白色與文字狀態；已測畫面自動掃描無文字對比違規，未全面人工測量所有狀態。
Recommendations:
- 不以顏色單獨表達資料足夠或核准資格。

## Component Consistency
Score: 8/10
Issues:
- 主介面按鈕維持 44 像素最小高度；來源卡統一顯示限制與閱讀範圍。原進階編輯入口保留舊操作方式。
Recommendations:
- 維持草稿、核准、保存三個明確動作，避免增加同內容確認視窗。

## Mobile Experience
Score: 8/10
Issues:
- 360／768 下原系統固定導覽占據部分視窗；實測可捲動至保存按鈕並操作。
Recommendations:
- 以真實裝置與使用者試用再驗證觸控、字級偏好及長來源閱讀負荷。

## Accessibility
Score: 8/10
Issues:
- 已驗證 Tab／Enter 的保存操作、按鈕名稱、表單標籤與 axe 的指定規則；螢幕閱讀器及完整無障礙符合性未驗收。
Recommendations:
- 在人員驗收時加入輔助科技操作。多份結果比較使用不同表單識別，避免標籤指向錯誤控制項。

## AI Generated Smell
Score: 9/10
Issues:
- 無裝飾圖、假信心分數、保證收益或背景工作假象。
Recommendations:
- 持續以可用資料、限制與下一步作為畫面主軸。

---

Final Verdict:

- PASS（限上述本機主流程的設計檢視；正式安裝與使用者易用性驗收另行處理）
