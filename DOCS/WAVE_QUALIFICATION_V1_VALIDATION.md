# 自動波段資格第一版：交付與驗證紀錄

日期：2026-09-30。基準：`main` / `774c6590b87f57d743d6aa16e29be68807669430`。本文件記錄工作目錄測試版；未替換安裝版、提交、推送或發布。

## Assessment 與行為差異

原波段說明只有固定限制，無法區分實際價格異常與證據缺口。新增獨立唯讀資格服務及個股資料狀態面板，顯示六項三態檢查、完整要求區間、實際行情區間、責任、證據和下一步。錯誤保留為不可檢查，不以較舊快照替代。

轉折偵測只使用既有方法與固定參數做合成資料逐段重播。實際股票只診斷資格；沒有正式自動候選、波浪序號、價格目標或交易訊號。

新增 `wave_qualification_v1` 唯讀回應與離線 `wave_pivot_lab_v1` 報告；既有正式研究、假設、核准、保存指紋及金融資料契約不變。沒有資料庫遷移。

## Modified Files

| 檔案 | 修改目的 |
|---|---|
| `src/services/wave_qualification_service.py` | 單一唯讀交易、快照及證據選取、六項診斷、輸出界限。 |
| `src/services/daily_public_data_service.py` | 抽出既有雜湊驗證；非物件或損壞內容明確失敗。 |
| `src/api/routes/wave_qualification.py`、`src/api/main.py` | 本機實例／標的檢查與禁止快取的唯讀入口。 |
| `src/services/wave_pivot_lab.py`、`tools/audit_wave_qualification.py` | 固定合成資料重播及明確輸出位置的報告工具。 |
| `config/model_rules.yaml` | 增列 `PIVOT-EXP-01`，C 級專案實驗，不升格核心。 |
| `frontend/src/api/waveQualificationClient.ts` | 驗證版本、標的、日期、狀態與自動候選關閉；可取消讀取。 |
| `frontend/src/api/guidanceClient.ts` | 相容擴充可選取的快取模式，新介面使用 `no-store`。 |
| `frontend/src/components/WaveQualificationPanel.tsx`、`.css` | 摘要、要求區間、三態細節、責任、重試、錯誤與功能關閉。 |
| `frontend/src/components/GuidedResearchWorkspace.tsx` | 個股載入唯讀取得，完整證據呈現；候選區導引連結。 |
| `tests/test_wave_qualification.py`、`tests/test_wave_pivot_lab.py` | 資格、修訂／撤銷、交易一致性、時間、重播與隔離邊界。 |
| `tests/fixtures/wave_pivots/{manifest,synthetic}.json`、`.gitattributes` | 匿名固定資料、SHA-256 與跨平台 LF 換行。 |
| `frontend/src/test/wave-qualification.test.tsx` | 8 項取消、錯檔、錯誤、歷史／關閉、重試與不輪詢檢查。 |
| `frontend/e2e/wave-qualification.spec.ts`、`frontend/e2e/my-stocks-wave.spec.ts` | 資格面板四寬度、鍵盤、無寫入及原人工候選保存相容。 |
| `DOCS/WAVE_QUALIFICATION_V1.md`、本文件、`DOCS/WAVE_QUALIFICATION_DESIGN_REVIEW.md` | 功能、介面、工具、驗證、設計審閱與後續順序。 |

原有 `DOCS/NEXT_TODO.md` 未修改：SHA-256 保持 `754e5431a901957df41965aeaa5924b7b479f084b82a5c0c29caa5e99377f645`。其既有未提交差異不屬本批。未清理其他暫存、工作樹、研究檔案或既有輸出。

## Test Evidence

下列指令均在專案根目錄執行；前端指令在 `frontend` 目錄。暫存與日誌採本批專用、Git 忽略目錄。

| 驗證 | 結果 | 證據 |
|---|---|---|
| `python -B -m pytest -q -p no:cacheprovider tests/test_wave_qualification.py tests/test_wave_pivot_lab.py --basetemp=.tmp_library_wave_qualification_focus_final` | **30 passed** | `.tmp_library_wave_qualification_focus_final.log` |
| `python -B -m pytest -q -p no:cacheprovider --basetemp=.tmp_library_wave_qualification_full_final` | **1,402 passed**，1 個既有 Starlette/httpx 淘汰警告 | `.tmp_library_wave_qualification_full_final.log` |
| 隔離報告最後增列 C 級、專案操作化及無官方關係欄位後，重跑 `tests/test_wave_pivot_lab.py` | **10 passed** | `.tmp_library_wave_qualification_lab_final.log` |
| `npm.cmd test -- --configLoader runner` | **165 passed / 24 files** | `.tmp_library_wave_qualification_frontend_accepted.log` |
| 最後中文標籤、空數量與窄版間距整理後重跑資格元件 | **8 passed** | `.tmp_library_wave_qualification_frontend_panel.log` |
| `npm.cmd run typecheck`；最後 `npm.cmd run build` 亦執行 `tsc -b` | **PASS** | `.tmp_library_wave_qualification_types.log`、`.tmp_library_wave_qualification_build_delivery.log` |
| `npm.cmd run lint` | **PASS**，無錯誤 | `.tmp_library_wave_qualification_lint_delivery.log` |
| `npm.cmd run build` | **PASS**，正式組合包機密檢查通過 | `.tmp_library_wave_qualification_build_delivery.log` |
| `npx.cmd playwright test --workers=2 --output=.library-wave-visual-qualification-delivery` | **64 passed** | `.tmp_library_wave_qualification_visual_delivery.log` |
| 最後資格區文字整理及補驗焦點遮蔽後，重跑資格瀏覽器案例 | **8 passed** | `.tmp_library_wave_qualification_visual_focus.log` |
| `git diff --check` | **PASS** | 僅 Windows 換行提示，沒有空白格式錯誤。 |

Python 使用 `-B` 不產生位元碼快取，`-p no:cacheprovider` 只停用測試快取，沒有減少回歸範圍。

初次型別／瀏覽器檢查受既有暫存目錄 EPERM 阻擋；經限定本機測試權限重跑通過。Vitest 使用 `--configLoader runner` 避開設定載入暫存權限問題。初始測試資料嘗試改寫不可覆寫快照及重複修訂序號，已改成新增壞樣本／不同版本；未放寬正式資料不可覆寫規則。瀏覽器測試攔截器曾攔到前端模組載入，修正為只攔 `/api/`；開發模式 StrictMode 首次會取消後重讀，明確重試仍只增加一次讀取。這些初次失敗均未列為通過證據。

### 固定案例涵蓋

- 缺資料庫不建檔、缺快照、雙雜湊損壞、最新壞版不回退、來源／股票／市場不符、異常型別與超限。
- 異常價位、缺值、非有限數字、重複日期、零量、排除列、來源重新解析對帳。
- 已知缺日、部分日曆、開休市衝突、跨市場禁止替代、共享休市證據不能證明特定市場開市。
- 停牌、恢復交易、日期精度不足、來源修訂、撤銷、未來證據不可使用。
- 缺公司行動完整覆蓋維持未知，價格口徑衝突不得通過。
- 逐筆歷史可用性維持未知，取得時間不回填成過去；末端未確認、相同高低價、同日雙向與前綴重播。
- 讀取途中新增快照，單次交易維持舊版本一致；下一次讀取取得新版本。
- 前端錯檔／錯契約阻擋、取消舊請求、單次失敗與重試、開關／歷史模式、不因視窗焦點輪詢。
- 閱讀沒有修改、採集或來源網址請求；惡意來源文字只以文字顯示。人工候選、核准及保存由既有瀏覽器案例驗證。

### 畫面與操作

Microsoft Edge，桌面與行動裝置模擬兩組；360、768、1024、1440 像素皆通過。主代理實際檢視四種截圖；最後局部整理再檢視 360 像素。日期、長雜湊及來源文字可換行，無水平溢出。

檢核細節與重試皆可鍵盤操作；以實際焦點中心命中測試確認未被固定導覽完全遮住。資格區 axe WCAG A/AA 掃描無違規。另測 320 CSS 像素重排、行高／字距／段距覆寫、區塊 200% CSS 縮放。

**限制：**CSS 縮放不等於完整瀏覽器文字縮放或輔助科技驗證；沒有執行螢幕閱讀器、實體手機或代表性使用者測試，不宣稱完整無障礙合規。

截圖位於 `frontend/.library-wave-visual-qualification-delivery/` 與最後局部驗證的 `.library-wave-visual-qualification-focus/`。測試惡意文字及固定導覽可能出現在長截圖中；實際焦點遮蔽另以操作量測驗證，不能只由截圖推斷。

## 實際資料唯讀盤點與隔離結果

明確指定既有三檔研究股票，未列舉收藏。最終私人報告在 `.tmp_library_wave_qualification_inventory/final/`，不提交。三檔仍不具正式自動候選資格；共同缺完整期間交易日／交易狀態、公司行動／還原依據、歷史可用時間；另有個別排除列或來源版本限制。**未知日數不是已知缺交易日數。**

兩次各自盤點前後對帳所有 **67 張資料表**的 schema、筆數及內容雜湊，均無變動。證據為私人 `summary.json`；只輸出雜湊及統計，不將私人筆記放入報告。

固定合成資料工具報告：`.tmp_library_wave_qualification_lab_delivery/report.json` 及 `report.md`。1 份固定資料、40 個前綴全部一致，固定參數 5／3，輸出規則版本與雜湊；未產生正式資料表紀錄、錨點或價格目標。重送既有輸出目錄會拒絕覆寫。

## 磁碟寫入稽核

| Item | Result |
|---|---|
| Local write paths found | 資格讀取無應用層寫入；離線工具只在明確 `--output-dir` 的新目錄寫 JSON、Markdown。測試／建置產物留專案忽略目錄。 |
| High-frequency write risks | 無逐事件、串流、按鍵或連續保存。 |
| Idle write risks | 無排程、背景輪詢、閒置寫入。 |
| Log / telemetry risks | 不新增記錄器或遙測；API 既有請求紀錄沿用原設定，工具只標準輸出。 |
| SQLite / IndexedDB risks | 同一唯讀交易、`mode=ro`、`query_only`；不新增表、遷移或 IndexedDB。正式內容雜湊對帳不變。 |
| Cache / temp file risks | 介面 no-store；無持久快取；測試／建置檔案按既有專案流程管理。 |
| Autosave / history risks | 不自動保存資格、對話、研究或假設。 |
| Retention limits | 單份 JSON 報告 1 MiB；清單 16 KiB／8 份；每份合成資料 256 KiB／256 筆。 |
| Cleanup strategy | 明確輸出的新報告由使用者手動管理；不自動刪除或覆寫，無總目錄容量上限。 |
| Production debug status | 本批未開啟持續 debug／trace；無新增正式日誌檔。 |
| Overall risk | **Medium**：可選報告的保留／清理由人工管理；日常唯讀介面風險低。 |
| Required fixes before completion | 無高頻或無人值守寫入風險；報告次數和新路徑需明確指定，限制及手動管理已揭露。 |

## 委派、回復與剩餘限制

Luna 子代理 `/root/wave_qualification_ui`：請求 `gpt-6-luna`／`high`，建立工具確認任務建立，未獨立核實實际執行模型設定。完成客戶端、面板、樣式及元件測試四個檔案，回報 8 項測試與型別檢查通過。未委派資料資格或最終驗收決策。

主代理逐檔檢視、整合與調整請求快取、React 生命週期、錯誤呈現及中文欄位，重新執行前端、瀏覽器與建置驗證；後端、資料邊界、正式資料前後對帳及最終判定皆由主代理負責。沒有使用外部模型服務。

關閉 `RESEARCH_WAVE_QUALIFICATION_ENABLED` 即回到原說明。沒有安裝替換或資料回復需求。下一批依功能文件順序補官方交易日／個股交易狀態，再評估公司行動與價格口徑，以及逐筆可用時間；正式候選另案核准。

產品邊界：沒有券商登入、真實委託、自動交易、波浪序號、保證報酬或自動核准／保存。合成資料通過不等於真實資料合格，使用者實際體驗及安裝版驗證仍待後續。
