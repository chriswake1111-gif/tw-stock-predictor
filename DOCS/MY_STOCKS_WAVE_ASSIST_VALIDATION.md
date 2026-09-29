# 我的股票與波段錨點輔助：開發交付紀錄

2026-09-30；基於 main / 04b2c338。此為開發與驗證交付，尚未替換安裝版、提交、推送或發布。

## Assessment

原首頁只保留瀏覽器最近搜尋，無法涵蓋從助理建立的研究，也會受啟動網址變化影響。既有自選清單沒有獨立的持有標記。波段來源候選已有預覽及核准能力，但錨點主要呈現為文字或資料結構，價格來源限制與人工假設容易混淆。

現在首頁可以直接點選本機持有、收藏或已保存研究；先列出股票，再提供搜尋。取消標記不刪除研究。波段候選顯示日期、角色、價格及示意圖，與來源資格分開；候選修訂時要求重新閱讀。範圍依 [介入計畫](MY_STOCKS_WAVE_ASSIST_V1.md)。

## Modified Files

| 檔案 | 修改目的 |
|---|---|
| `src/services/research_library_service.py` | 唯讀彙整本機清單、獨立持有標記、原收藏相容、原子交易、版本衝突及重送／容量保護 |
| `src/api/routes/research_library.py` | 本機清單讀取與明確標記命令，嚴格輸入驗證 |
| `src/api/main.py`、`src/api/workflow_security.py` | 註冊路由並沿用安裝實例、來源、Host、防偽造請求與大小限制 |
| `migrations/20260930_28_research_library.sql`、`src/repositories/migration_runner.py` | 新增兩張表，不改舊表結構 |
| `src/services/evidence_backup_service.py` | 備份檢查納入新紀錄 |
| `src/services/wave_anchor_guidance.py`、`src/services/research_guidance_service.py` | 波段資料資格、缺項責任、來源日期與人工候選分離；補上助理查證需求 |
| `frontend/src/components/MyStocks.tsx`、`MyStocks.css` | 分類、分頁、一鍵研究、標記、回應遺失重試及讀回 |
| `frontend/src/pages/SearchHomePage.tsx`、`StockResearchPage.tsx` | 首頁清單優先、搜尋入口保留、個股標記；歷史模式不顯示寫入控制 |
| `frontend/src/components/WaveAnchorAssist.tsx`、`WaveAnchorAssist.css` | 價格來源限制、人工錨點示意圖、日期價格清單與可讀標示 |
| `frontend/src/components/GuidedResearchWorkspace.tsx` | 圖卡與候選預覽、比較及既有草稿核准串接，候選修訂時撤下舊預覽 |
| `frontend/src/api/guidanceClient.ts`、`researchClient.ts` | 新引導型別、清單命令的防偽造請求路由及錯誤說明 |
| `tests/test_research_library.py`、`test_wave_anchor_guidance.py` | 清單、來源資格、候選及核准邊界測試 |
| `tests/test_phase13_second_review_remediation.py` | 將新增遷移列入既有完整清單斷言 |
| `frontend/src/test/my-stocks.test.tsx`、`wave-anchor-assist.test.tsx`、`waveAssistFixture.ts` | 新流程固定匿名案例 |
| `frontend/src/test/guided-search.test.tsx`、`research-csrf-routing.test.ts` | 搜尋錯誤定位及清單命令驗證 |
| `frontend/e2e/my-stocks-wave.spec.ts` | 清單 → 波段候選 → 草稿 → 核准 → 保存的匿名瀏覽器驗收 |
| `.gitignore` | 排除本批私有資料副本、測試紀錄與截圖 |

既有使用者變更 `DOCS/NEXT_TODO.md` 未修改；SHA-256 保持 `754e5431a901957df41965aeaa5924b7b479f084b82a5c0c29caa5e99377f645`。未操作其他工作樹或既有暫存產物。舊程式碼圖譜指向過去副本，本批以目前工作樹原始碼與測試核實。

## Behavior / Data Contract Changes

- `GET /api/v2/research/library`：分類 `all`、`held`、`favorites`、`researched`；游標分頁，介面每頁 8 檔，伺服器最多 100 檔。只查本機名稱與保存日期，不對清單每檔執行金融分析或資料更新。
- `GET /api/v2/research/library/{symbol}`：讀取持有、收藏、上次保存日期與標記版本。
- `POST /api/v2/research/library/{symbol}`：只接受 `label`、`value`、`version`，另須重複請求鍵；已有結果優先讀回，錯配版本拒絕寫入。使用已知股票或已有研究／收藏紀錄的股票。
- 收藏沿用 `research_watchlist_items`；`research_holding_labels` 保存獨立持有狀態及版本；`research_library_commands` 保存有上限的操作重送結果。
- 引導增加 `wave_support`，內含版本 `wave_anchor_guidance_v1`、來源日期、價格口徑、缺項與責任。經既有確認保存流程寫入的引導內容隨研究固定；沒有獨立的自動保存或核准入口。
- 圖示只連接已整理的人工候選點，不是完整價格曲線、機器確認轉折或預測路徑。原錨點值、模型公式、Rule ID 與證據等級未改；沒有新增金融計算規則。

## Test Evidence

| 驗證 | 實際結果 |
|---|---|
| 完整 Python 回歸：`python -m pytest -q`，指定本批隔離暫存與記錄位置 | **1,354 passed**，一項既有 Starlette 測試客戶端棄用警告 |
| 最終路由整理後的本機清單／假設焦點回歸 | **22 passed** |
| 前端完整測試：`npm.cmd test -- --reporter=dot` | **153 passed / 22 files** |
| 型別檢查：`npm.cmd run typecheck`；最終建置再執行 TypeScript 檢查 | 通過 |
| 程式檢查：`npm.cmd run lint` | 通過 |
| 建置：`npm.cmd run build` | 通過，正式套件秘密資料檢查 PASS |
| 完整瀏覽器驗收 | **48 passed**；360、768、1024、1440 像素，桌面及行動裝置設定 |
| 新清單、候選預覽無障礙及鍵盤操作 | 補驗 8 項通過；包含新清單及候選區的 WCAG 自動檢查、Enter／Space、焦點與水平溢出 |
| 隔離資料副本遷移 | 65 → 67 張表；64 張既有表內容雜湊不變，僅新增式遷移登錄多一筆；重跑結果相同，SQLite integrity 為 ok |
| 實際備份副本唯讀彙整 | 既有研究與收藏正確彙整；未自動推定任何持股 |

首次完整回歸發現舊遷移清單測試未列入第 28 次遷移，已補正並重跑完整套件。開發中修正型別、重送狀態渲染及測試攔截範圍問題，以上為修正後結果。環境對既有前端暫存目錄的沙箱寫入限制，透過範圍限定的本機測試權限完成，未降低產品的驗證條件。

完整 Python 紀錄在本機 `.tmp_library_wave_full2.log`；遷移證據在 `.tmp_library_wave_delivery/migration-result.json`；完整畫面驗收在 `frontend/.library-wave-visual-acceptance/`。這些產物皆不納入 Git。畫面評估見 [Design Review](MY_STOCKS_WAVE_ASSIST_DESIGN_REVIEW.md)。

## Disk Write Audit

| 寫入點 | 觸發與頻率 | 上限／保留 | 評估與實證 |
|---|---|---|---|
| 持有狀態表、原收藏表 | 使用者按一次標記才執行；一次交易更新 | 新標記表最多 5,000 個標的，原資料不刪除 | 低；兩種標記獨立，內容未變的命令不改標記 |
| 清單命令表 | 明確命令首次成功才追加；相同命令重送直接讀回 | 20,000 筆、內容合計 32 MiB、單筆命令及結果合計 16 KiB，任一先到即停止新增 | 低；額滿原子回復本次標記修改，測試涵蓋交易回復與額滿重送 |
| 清單閱讀、分類、分頁、波段資格、圖卡 | 只讀或記憶體運算 | 無新持久紀錄 | 低；清單連線採 SQLite 唯讀，連續閱讀前後檔案雜湊一致；無背景計時寫入 |
| 假設與研究保存 | 沿用明確草稿、使用者核准及確認保存 | 既有資料保留規則；波段引導只增加精簡內容 | 本批未新增自動寫入路徑，不保存整份原始報告 |
| 開發驗證副本、測試紀錄、截圖、建置 | 本批人工啟動的驗證 | 隔離目錄、Git 排除，未設背景重跑 | 非執行期行為；原備份內容雜湊不變 |

32 MiB 是命令內容上限，不包含 SQLite 頁面與索引開銷，也不是整個既有金融資料庫的大小限制。沒有增加自動清除、反覆存檔、輪詢寫入或逐檔更新工作；無未處理的高風險新增寫入點。

## Remaining Limitations / 回復

1. FinMind 歷史價格尚未具備完整交易日、停牌、除權息與轉折確認證據。此版不能自動產生可採用的波浪錨點；使用者核准不會改變資料資格。這些資料能力及自動確認器仍屬後續工作。
2. 持有只是一個手動標記，沒有股數、成本、損益、交易同步或券商連線。收藏沿用原自選語意，已保存研究與最近搜尋分開。
3. 瀏覽器驗收使用固定匿名資料；後端另有真實服務與隔離資料庫測試。本開發驗收未包含 Windows 安裝包、正式安裝升級或乾淨機器安裝驗收；發布時另附套件與安裝驗證摘要。
4. `RESEARCH_LIBRARY_ENABLED=false` 可隱藏清單並拒絕新標記命令；`RESEARCH_WAVE_ASSIST_ENABLED=false` 回復原候選呈現。保留新表與紀錄，不執行破壞性回滾。關閉功能不撤銷假設，也不改寫歷史研究。

## Product Boundary Check

未加入真實交易、券商帳戶、持股金額、自動交易、假資料、模型升格、自動核准或正式研究自動保存。沒有對使用者的持有、研究或核准紀錄執行測試寫入。
