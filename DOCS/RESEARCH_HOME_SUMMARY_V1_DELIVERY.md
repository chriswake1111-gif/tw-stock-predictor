# 啟動首頁一致化與研究變化摘要：開發交付

日期：2026-09-30。基準提交：`cc36a11ab21c0780e457b8fcc297aefb2758fc78`。

本文件記錄使用者已核准計畫的開發驗收。開發驗收時尚未替換安裝版、提交、推送或發布；未操作正式研究資料。後續 1.4.0 安裝與發布須另行核對同一提交的套件、資料保留及遠端驗證結果。

## Assessment

原首頁已有持有、收藏及已保存研究清單，但尚不能直接辨識相對於最近保存研究的變化。啟動器仍指向每日研究頁；個股頁一般入口會自動準備外部資料。後者與本批「摘要點擊只導向閱讀」不相容，因此首頁連結新增明確的本機閱讀模式。

沿用 `DailyResearchJournalService.preview` 的一致性讀取、最近紀錄雜湊檢查、`compare_entries` 與 `build_guidance`。沒有新增金融計算或選值規則。

## Behavior Changes

- 啟動器準備完成後開啟 `/`；`/research/daily` 及原有路徑保留。
- 股票卡先顯示清單，再取得當頁摘要；保持四個分類、每頁八檔。同時最多兩個摘要請求，切換頁面取消未開始的請求；已送出請求的回應不會覆蓋其他股票。
- 摘要優先級：讀取問題、原情境不再可用、可確認變化、既有引導要求的必要選擇、無基準／缺項、可比較內容相同。
- 同日數字修訂與跨日期變動分開；日期較新但數字相同仍顯示日期更新。來源或可比較性不符時保留前後資料，不計算差值。
- 過期、更新失敗、來源品質限制保留在摘要旁；缺项分工可展開。不以「沒有變化」描述資料不足。
- 最近保存研究損壞或讀取失败時顯示「暫時無法比較」，保留重試與股票入口，不退回舊研究作基準。
- 優先沿用最近研究的明示年度；沒有年度時仍由原引導規則判定，多年度不擅自選定。
- `?view=local` 的首頁連結只讀本機。`research-changes`、`research-candidates`、`research-data` 三個固定定位在資料載入後展開並移入鍵盤焦點。核准、草稿、保存與來源更新仍需各自明確操作。
- 手動「更新資料」仍可執行原更新流程；普通搜尋／原書籤的既有行為保持不變。歷史模式仍不更新或寫入。

## Data Contract Changes

新增 `GET /api/v2/research/library/{symbol}/summary`，保護邊界沿用本機實例與標準股票識別檢查，回應 `Cache-Control: no-store`。

| 欄位 | 意義 |
|---|---|
| `contract_version` | `research_home_summary_v1` |
| `enabled`, `symbol`, `local_only` | 功能開關、股票識別、僅本機內容 |
| `prepared_at` | 本次一致性讀取的整理時間 |
| `baseline` | 最近保存紀錄的識別、保存時間與資訊截止時間；無基準或無法驗證時為空 |
| `selected_year` | 原引導採用的年度，未知時為空 |
| `status`, `headline` | 比較狀態與主要摘要 |
| `dates` | 五項原比較欄位各自的前後資料日 |
| `changes` | 既有比較結果的精簡投影；可比較時沿用既有差值 |
| `unavailable_scenarios` | 原先可用組合未出現在本次結果及已知原因；不推論使用者動作 |
| `limitations` | 資料限制、影響及責任歸屬 |
| `next_step` | 固定區塊定位、文字與責任歸屬，沒有任意網址或執行命令 |

比較狀態：`unavailable`、`scenario_unavailable`、`changed`、`choice_required`、`no_baseline`、`incomplete`、`unchanged`。`unchanged` 僅指可比較的本機內容。

既有清單回應新增相容的可選欄位 `summary_enabled`；原標記版本、重送保護與寫入端點不變。關閉時摘要端點僅傳版本、股票與 `enabled:false`。

摘要不回傳完整筆記、候選原文、假設核准憑證或保存確認指紋。金融欄位、資料來源、單位、公式、資料表與遷移均未更動。

## Modified Files

| 檔案 | 變更 |
|---|---|
| `src/services/research_home_summary_service.py` | 唯讀摘要服務、優先級與投影 |
| `src/services/daily_research_journal_service.py` | 在原一致性鎖內選擇最近保存的年度，預設預覽行為不變 |
| `src/api/routes/research_library.py` | 唯讀端點、功能開關能力與禁止快取回應 |
| `src/runtime/launcher.py` | 預設啟動首頁 |
| `frontend/src/api/homeSummaryClient.ts` | 回應檢查、兩席請求佇列、取消及本機閱讀連結 |
| `frontend/src/components/StockHomeSummary.tsx` | 個別卡片摘要、限制、錯誤與重試 |
| `frontend/src/components/MyStocks.tsx`、`MyStocks.css` | 分頁摘要、重新讀取及原清單入口 |
| `frontend/src/components/GuidedResearchWorkspace.tsx` | 固定區塊定位、展開及焦點；原入口備援 |
| `frontend/src/pages/StockResearchPage.tsx` | 首頁連結只讀本機、保留明示更新 |
| `tests/test_research_home_summary.py` | 匿名案例、一致性、完整性、年度及零資料異動 |
| `tests/test_phase18_runtime.py` | 隔離的首次啟動與重啟目的頁驗證 |
| `frontend/src/test/home-summary.test.tsx`、`my-stocks.test.tsx`、`research-refresh.test.tsx` | 佇列、取消、文字安全、重試及本機閱讀 |
| `frontend/e2e/home-summary.spec.ts` | 四種寬度、鍵盤定位、返回後重新讀取、單檔失敗、舊路徑及零寫入請求 |

`DOCS/NEXT_TODO.md` 為使用者既有修改，本批未編輯；SHA-256 保持 `754e5431a901957df41965aeaa5924b7b479f084b82a5c0c29caa5e99377f645`。

## Test Evidence

全部測試只使用隔離、固定、去識別資料。初輪測試修正包含暫存父目錄缺少、既有編譯暫存權限、测试型別、瀏覽器模擬攔截範圍及重試案例時序；未放寬產品驗證。

| 驗證 | 實際結果 |
|---|---|
| Python 完整回歸 | 1,372 passed，1 項既有 Starlette 棄用警告；之後的資料庫消失防護另跑相關焦點回歸 |
| 後端最終焦點回歸 | 41 passed，涵蓋摘要、引導、清單；包含資料庫消失防護、保存／核准競態及多年度 |
| 前端完整測試 | 23 個檔案、157 passed |
| 型別檢查 | 通過 |
| 程式檢查 `npm.cmd run lint` | 通過 |
| 正式建置及既有機密掃描 | 通過，`production_bundle_admin_secret_gate=PASS` |
| 完整瀏覽器回歸 | 56 passed；桌面及行動設定，360／768／1024／1440 寬度 |
| 首頁無障礙自動檢查 | 四種寬度無 WCAG A／AA 掃描違規；焦點與鍵盤另有操作驗證 |
| 啟動器 | 首次啟動、正常重啟與新鮮／既有資料啟動焦點測試通過；使用隔離控制面，未替換或啟動正式安裝版 |
| 原路徑 | 瀏覽器驗證 `/research/daily` 可閱讀；其他原流程包含在完整回歸 |
| 資料保護 | 摘要前後 SQLite 全資料內容相同；損壞的最新紀錄不回退；無外部採集或寫入請求 |

可重現命令：

```powershell
python -X utf8 -B -m pytest -q -p no:cacheprovider --basetemp .tmp_library_wave_home_summary/full-final
# 以下在 frontend 目錄執行
npm.cmd test -- --reporter=dot
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
npx.cmd playwright test --output .library-wave-visual-home-final --workers=2
```

本機記錄：`.tmp_library_wave_home_summary/python-final.log`、`focus-final.log`、`browser-final.log`；截圖位於 `frontend/.library-wave-visual-home-final/`。這些產物沿用忽略規則，不提交私人研究或大型執行產物。

## 回復與剩餘限制

設定 `RESEARCH_HOME_SUMMARY_ENABLED=false` 並重啟來源版本，即回到原簡單清單；不刪表或資料。啟動目的頁可独立還原 `/research/daily`。年度沿用參數預設關閉，既有研究預覽與保存呼叫保持原語意。

一致性沿用原預覽的短期 SQLite 寫入保留鎖；雖不修改資料，仍可能短暫等待其他寫入，逾時會顯示不能比較。此批沒有新增大型資料量壓力測試；本文件所列開發驗收不包含後續實機安裝升級、發布或使用者驗收證據。

財報來源覆蓋、自動波段資料資格與自動辨識仍屬後續批次。摘要不能補足不存在的數字，也不代表所有估值情境已完整。

## Product Boundary Check

沒有券商連接、帳戶登入、真實下單、自動交易、買賣燈號、收益排名或保證價格；没有模型升格、金融公式變更、資料庫遷移、背景查證、推播、代核准或自動保存。

## Disk Write Audit

| Item | Result |
|---|---|
| Local write paths found | 新摘要無資料寫入路徑；沿用讀取與 SQLite 一致性鎖。開發產物限 `frontend/dist`、編譯暫存與上述匿名測試目錄 |
| High-frequency write risks | 無每筆摘要持久快取、事件寫入或新日誌 |
| Idle write risks | 新功能沒有輪詢、計時搜尋或閒置保存；離開頁面取消未開始請求 |
| Log / telemetry risks | 沿用既有本機執行記錄，未加入完整筆記或來源回應記錄 |
| SQLite / IndexedDB risks | 未新增表、寫入或 IndexedDB；讀取前後資料內容核對不變。既有 SQLite 側檔管理不宣稱實體位元組零寫入 |
| Cache / temp file risks | 摘要僅短暫記憶體、卸載即清；回應 no-store。開發測試截圖與日誌無自動清除，需按既有維護程序管理 |
| Autosave / history risks | 無新自動保存；原確認流程與歷史唯讀保留 |
| Retention limits | 顯示當頁八檔、同時兩個讀取；不掃其他頁。摘要不留永久紀錄 |
| Cleanup strategy | 佇列取消、記憶體卸載清除；未刪除任何既有暫存或研究檔 |
| Production debug status | 本批未啟用詳細追蹤或新增遙測 |
| Overall risk | Low（產品新增寫入風險）；開發產物採人工維護 |
| Required fixes before completion | 無高風險持續寫入 |

介面檢查另見 [Design Review](RESEARCH_HOME_SUMMARY_V1_REVIEW.md)。
