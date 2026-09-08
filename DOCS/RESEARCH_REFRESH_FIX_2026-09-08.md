# Installed research refresh 修正與驗證（2026-09-08）

## Assessment

基準 main@0862d6558838d58ace596323da0bdd6365793e8b。使用者已核准修正資料更新、官方交易日證據、部分完成重試、切換標的輪詢與相應驗證。

安裝版日誌出現 `source session 2026-09-07 is not an authorized trading session: status=missing`。唯讀查核當時本機 9/4–9/8 僅有 9/4 交易日證據；本機 EOD 亦停在 9/4。Bootstrap 只檢查 close available，前端將 partial 當成可重試終態，導致舊行情直接 ready 或重複建立作業。

## Modified Files / Behavior Changes

- `src/services/research_bootstrap_service.py`、`src/api/routes/v2_research.py`：bootstrap 新增可選 `refresh`（預設 false，相容原呼叫）。明確更新要求不再因舊本機 close available 而直接 ready；仍使用既有 enable_symbol 作業與授權。
- `src/services/installed_data_sync_service.py`：當指定日期沒有有效交易日證據，使用既有白名單的對應市場成交端點，驗證唯一指定日期、有限且正值的成交金額。僅追加對應 venue 的已觀察交易日證據；保留來源、SHA-256、日期、TWD 單位、取得時間與作業 lineage。既有休市、撤銷或衝突不被覆蓋，不從星期或假日表缺席推算交易日。抓取仍受既有 deadline、capability 與 resource lock 控制。
- `frontend/src/api/phase20Client.ts`、`frontend/src/api/dataOperationsClient.ts`：傳遞 refresh 與 AbortSignal，包括 CSRF、bootstrap、operation read。
- `frontend/src/pages/StockResearchPage.tsx`：更新操作明確連線檢查；partial 停止自動重新啟動；只有無關作業成功後才允許標的專屬接續。HTTP 等待受總期限約束，輪詢採序列等待；換標的／離頁中止舊請求並忽略遲到結果。更新失敗保留已讀取的本機摘要並標示失敗。
- `frontend/src/components/ResearchSummaryCard.tsx`：以「本機行情日期」呈現資料日期，不暗示官方來源一定已提供今日行情。
- `tests/test_research_refresh_regression.py`、`frontend/src/test/research-refresh.test.tsx`：新回歸測試；既有 `phase20-usability.test.tsx` 對應移除多餘終態 bootstrap 的預期。
- `.gitignore`：排除本次本機驗證暫存目錄及可能含使用者資料副本的診斷產物。

## Data Contract Changes

API request 僅增加可選 `refresh: boolean = false`；無 schema migration、無既有欄位刪除。新的交易日證據使用現有 calendar revision / raw revision / ingestion ledger，market 明確為 TWSE 或 TPEX。`available_at` 與 `ingested_at` 採本次取得時間，不回填歷史公開時間。歷史研究模式不觸發 refresh。

官方來源：
- TWSE 每日市場成交資訊：https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK
- TWSE 資料說明：https://www.twse.com.tw/zh/trading/historical/fmtqik.html
- TPEx 既有白名單：https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index

## Test Evidence

| 驗證 | 結果 |
|---|---|
| 新後端回歸：日期精確比對、非正／非有限金額、重複日期、HTML、撤銷／休市、PIT、市場隔離、取消、逾時、舊行情 refresh | 15 passed |
| `python -m pytest -q --basetemp=.tmp_refresh_full_01 -o cache_dir=.tmp_refresh_cache --tb=short` | 930 passed，1 既有 Starlette deprecation warning，165.00 秒 |
| `npm.cmd test -- --reporter=dot` | 10 files / 54 tests passed |
| `npm.cmd run lint` | PASS |
| `npm.cmd run build`（含 TypeScript） | PASS；production_bundle_admin_secret_gate=PASS assets=2 |
| `npm.cmd run test:visual` | 6 passed；受限環境第一次未正常退出，停止後在允許瀏覽器執行的環境重跑成功 |
| 本次研究頁面 Playwright 人工場景腳本 | 1280 / 360 寬度 × success / partial，共 4 場景通過；每場景僅 1 次 bootstrap，無水平溢出；已查看手機 partial 截圖 |
| `git diff --check` | PASS |

首輪 Python 的預設暫存目錄出現 Windows 權限問題，獨立 basetemp 重跑成功；未削弱測試。

### 真實官方連線，獨立安裝資料副本

使用安裝版 2026-09-08 19:39 的 pre-upgrade backup 複製到專案診斷目錄；不修改原安裝資料。副本的舊 active operation 以既有 repository API 標記 interrupted 後測試。受限環境 initially WinError 10013，經允許執行同一受治理流程後連線成功。

- 2330.TW：operation succeeded；行情從 2026-09-04 到 2026-09-07；close available。
- 2408.TW：operation succeeded；2026-09-07 close available。
- 第二檔完成後再讀兩檔，均為 2026-09-07 available。
- 官方端點當次提供到 9/7；未宣稱取得 9/8 行情。
- 本機產物：`reports/diagnostics/research-refresh-local/live-results.json`、`validation.db`、`refresh-*.png`。全數已忽略，不提交資料副本。

## Disk Write Audit

| Item | Result |
|---|---|
| Local write paths found | 指定資料庫的 calendar/raw revisions、ingestion runs/items、installed operations；本機診斷副本 |
| High-frequency write risks | 已移除 partial 反覆建立作業；成功標的作業亦不再重新 bootstrap |
| Idle write risks | 無新增排程或 idle 寫入；請求由使用者研究／更新操作觸發 |
| Log / telemetry risks | 無新增逐輪錯誤日誌；原 server.log 為 INFO FileHandler，未見輪替設定，屬既有限制 |
| SQLite / IndexedDB risks | 已有證據不重新抓取或重寫；新證據僅針對缺失日期；取消後有寫入授權檢查；無 IndexedDB 變更 |
| Cache / temp file risks | 本機測試產物已 gitignore；非安裝版 runtime 快取 |
| Autosave / history risks | 無 autosave；保留既有不可變證據歷史 |
| Retention limits | 證據歷史不自動刪除；既有 DiagnosticLogger 10 MiB/檔、5 backups、60 MiB logical cap；server.log 無同等界限 |
| Cleanup strategy | 測試副本人工清理；未刪除使用者既有資料或目錄 |
| Production debug status | 未開啟 DEBUG；既有 server INFO logging 維持 |
| Overall risk | Medium：操作有界，仍有既有 server.log 與長期證據保留的容量限制 |
| Required fixes before completion | 本次高頻作業重試已修復；完整磁碟量測與既有日誌輪替不在本次範圍 |

## Remaining Limitations / Rollback

- 本次完成原始碼及驗證，未重新產生或安裝 Windows installer，未 commit、push 或部署。
- 真實外部連線僅驗證上述 TWSE 兩檔；TPEx 以可重現 fixture 驗證，未宣稱 live TPEx 通過。
- 官方端點未提供該日期、日期衝突或連線失敗時仍停止並揭露原因，不能保證每次操作都可取得新資料。
- 切換頁面取消的是前端請求／輪詢；已授權的後端作業仍遵守既有 lease/deadline 或顯式取消流程，不因前端離頁而取消其他使用者操作。
- rollback 可回復本次精確檔案 patch；API refresh default false 保留原呼叫相容性，新附加證據保留，不需破壞性資料回滾。

## Product Boundary Check

未新增券商、真實下單、自動交易、背景排程、外部來源白名單或模型公式變更。無假行情、假交易日、Forward EPS 或波浪錨點生成；不改寫歷史快照。
