# 第二版驗證紀錄（2026-09-30）

範圍：[官方交易日與停復牌證據補強](WAVE_SESSION_EVIDENCE_V2.md)。起始 `main` 為 `6e8b769c`。這是開發測試交付，不是安裝升級或發布證據。正式自動候選、人工核准與保存邊界保持不變。

## 實際變更

| 檔案 | 責任 |
|---|---|
| `src/collectors/wave_session_sources.py` | 七個官方來源的精確請求、股票／年月綁定、保守解析與容量限制。 |
| `src/collectors/installed_egress_client.py` | 只擴充上述端點的固定網址白名單，沿用傳輸保護。 |
| `src/repositories/wave_session_repository.py` | 不可覆寫紀錄、雜湊、修訂、重送、總容量及唯讀交易選取。 |
| `migrations/20260930_29_wave_session_evidence.sql` | 獨立新增表、索引與不可修改觸發器。 |
| `src/repositories/migration_runner.py` | 註冊新增式遷移。 |
| `src/services/evidence_backup_service.py` | 將新增證據納入重要資料備份清單。 |
| `src/services/wave_session_coverage.py` | 原區間內的日期分類、停復牌配對、有限樣本及下一步。 |
| `src/services/wave_session_refresh.py` | 明確執行、單股票、限定來源與區間的更新工作；沒有背景工作。 |
| `src/services/wave_qualification_service.py` | 相同唯讀交易整合可選摘要；保留既有未通過判定與證據。 |
| `tools/update_wave_session_evidence.py` | 預設唯讀預覽，明確 `--execute` 才更新獨立證據。 |
| `config/model_rules.yaml` | C 級 `SESSION-EVIDENCE-01`，非杜金龍已驗證核心。 |
| `frontend/src/api/waveQualificationClient.ts` | 可選版本化摘要的前端型別及檢查。 |
| `frontend/src/components/WaveQualificationPanel.tsx` | 摘要、中文狀態、來源展開、取得時間與歷史可用性區分、複製需求。 |
| `frontend/src/components/WaveQualificationPanel.css` | 響應式閱讀、數字及鍵盤焦點樣式。 |
| `frontend/src/test/wave-qualification.test.tsx` | 純文字、取消、重試、歷史模式及剪貼簿失敗等測試。 |
| `frontend/e2e/wave-qualification.spec.ts` | 四種寬度、鍵盤、無障礙、唯讀請求及惡意來源文字。 |
| `tests/test_wave_session_evidence.py` | 固定匿名來源、失敗保守處理、資料隔離、完整性、來源重試與備份還原。 |
| `tests/test_phase13_second_review_remediation.py` | 精確遷移清單加入第 29 版，保留重跑及內容校驗。 |
| 本文件及 `DOCS/WAVE_SESSION_EVIDENCE_V2.md` | 來源限制、工具、驗證、回復與後續範圍。 |

沒有修改 `DOCS/NEXT_TODO.md`。它在開始時已有使用者變更，驗證時內容雜湊仍為 `754e5431a901957df41965aeaa5924b7b479f084b82a5c0c29caa5e99377f645`。

## 測試證據

| 檢查 | 實際結果 |
|---|---|
| Python 焦點 | `python -X utf8 -m pytest -q tests/test_wave_session_evidence.py tests/test_wave_qualification.py -p no:cacheprovider --basetemp=.tmp_wave_v2_focus6`：50 通過。 |
| 完整 Python 回歸 | `python -X utf8 -m pytest -q -p no:cacheprovider --basetemp=.tmp_wave_v2_full_final`：1,432 項通過、1 則既有棄用警告，219.62 秒。 |
| 完整前端單元測試 | `npm.cmd test -- --configLoader runner`：24 檔、170 項通過。 |
| 前端程式檢查 | `npm.cmd run lint`：exit 0。 |
| 型別檢查／正式建置 | `npm.cmd run build`：TypeScript、Vite 及正式 bundle 機密檢查皆通過；產物未安裝。 |
| 完整瀏覽器流程 | `npm.cmd run test:visual -- --output <TEMP>/wave_session_v2_all_visual_final`：64 項通過。 |
| 波段面板畫面 | 上述包含 360、768、1024、1440 像素各 desktop/mobile，共 8 項；鍵盤開啟展開區、複製與重試、無橫向溢出及自動無障礙檢查通過。另測 320 CSS 像素文字間距及面板 2 倍縮放；不是實體手機或螢幕閱讀器人工認證。 |
| 保護來源文字 | 惡意圖片／事件文字只呈現文字，沒有來源連結／圖片載入；面板測試無 POST、無測試惡意來源請求，API 測試禁止來源採集。 |
| 備份與還原 | 新證據實際放入備份，再還原至新測試資料庫，內容及雜湊相符。 |
| 語法與差異檢查 | 六個本批主要 Python 正式模組／工具的語法解析通過；`git diff --check` 通過（只有既有 Windows 換行提示）。未暫存或提交檔案。 |

焦點案例涵蓋：來源市場／股票／月份不符、欄位漂移、回應筆數不符、重複日期、缺價格的正量列、櫃買日曆標題年度、未知／空結果、休市與成交衝突、缺行情、初次上市、配對與未配對停復牌、同時間相反事件、時間不明、盤中狀態、收盤邊界、未來預定事件、未來成交日期、最新失敗／撤銷與重疊區間、可知時間截點、內容損壞不回退、重送與額滿、同一交易中途追加、讀取無寫入、無自動建立／遷移、單一來源重試、既有未通過狀態不被新正向證據遮蔽。

測試命令使用專案暫存目錄及 `-p no:cacheprovider`。第一輪完整 Python 的唯一失敗是測試列舉尚未更新第 29 版遷移；修正清單後通過。前端一項舊標籤預期在「來源版本」改為更精確的「來源類別」後已同步修正並完整重跑。未降低資格條件來使測試通過。

既有 Python 環境有一則 Starlette/httpx 棄用警告；本批未升級依賴。前端預設 TypeScript 快取在沙箱內遭 EPERM，原建置命令於授權的提升執行環境通過，未放寬產品檢查。Vitest 使用 Vite 原有的 runner config loader 避開 `.vite-temp` 權限問題。測試用 4173 服務完成後已停止。

## 遷移與正式資料保護

1. 正式安裝資料庫透過 SQLite 備份讀入隔離副本，沒有忽略 WAL。只在副本套用第 29 版。
2. 原有 67 張非內部資料表逐表比對：副本只改變 `additive_schema_migrations`；新增 `wave_session_evidence`。其餘原表資料不變，完整性檢查 `ok`。
3. 明確指定既有三檔研究股票及各自原要求期間，共 77 份來源查詢；保留一次失敗，修正櫃買年度辨識後只重試受影響的單一來源，副本共 78 筆不可覆寫證據。
4. 三檔均取得部分日期佐證，仍有未知日；兩檔有官方成交但本機缺行情，一檔找到明確配對停復牌。全部維持 `quality_warning` 及 `automatic_candidates_eligible=false`。不公開私人股票清單或原始盤點。
5. 來源追加後再次比較：正式資料庫原表內容全數不變、沒有新增表；副本舊表仍只有遷移紀錄變更。三檔資格讀取前後副本所有表內容相同。

私人證據留在忽略的 `.tmp_library_wave_v2_20260930/`，包括遷移報告、前後逐表雜湊、來源結果與資格報告。沒有提交此目錄或原始股票研究。隔離副本只是驗證資料，安裝版仍是第一版；後續正式升級應在明確授權後依正常備份遷移流程進行。

## 磁碟寫入盤點

| Item | Result |
|---|---|
| Local write paths found | 指定既有資料庫中的 `wave_session_evidence`、SQLite journal/WAL（依該庫原模式）；開發測試的副本、報告、前端建置產物與測試暫存。 |
| High-frequency write risks | 無 UI 事件、價格逐筆或串流逐字寫入。每次明確工具工作最多 120 份來源，各自一筆交易。 |
| Idle write risks | 本批無計時器、背景輪詢或閒置追加；網頁與資格介面只讀。 |
| Log / telemetry risks | 不新增常駐日誌或遙測；命令結果輸出到終端，只有操作者明確導向檔案才保存。失敗例外文字與憑證不入庫。 |
| SQLite / IndexedDB risks | 一張獨立追加表，唯一鍵防重、交易及不可覆寫觸發器；不新增 IndexedDB。保留既有 SQLite 模式／checkpoint，不另開長期寫入連線。 |
| Cache / temp file risks | 正式功能不新增磁碟快取；測試副本與報告只在明確驗證時生成，沒有反覆自動清理／重建循環。 |
| Autosave / history risks | 沒有自動保存研究；只有明確啟動查證後追加來源修訂。 |
| Retention limits | 原始來源 2 MiB、單筆含保守額外開銷最多 3 MiB、證據邏輯總量 64 MiB、每次最多 120 請求／180 秒。傳輸層另有既有有限回應容量。邏輯上限不等於整個資料庫或 WAL 的實體檔案上限。 |
| Cleanup strategy | 額滿停止追加、不自動刪除。資料保留依本批授權；本機驗證副本／報告日後可另行清理，本批沒有刪除使用者檔案。 |
| Production debug status | 無本批新增 production debug／trace 持久化。 |
| Overall risk | Medium：明確操作且容量有界、無閒置寫入；證據與測試副本採人工保留，不做自動刪除。 |
| Required fixes before completion | 沒有尚待修復的高頻或無界寫入；容量／日誌／讀取唯讀行為已有固定測試。 |

## 委派與獨立審查

依專案允許的原生子代理流程，`/root/wave_coverage_ui_v2` 接受五個前端檔案的有界工作；requested model 為 `gpt-6-luna`、reasoning effort 為 `high`，工具回報建立成功，但沒有可獨立驗證的實際執行模型資訊，不推定 runtime 設定。子代理修改上述五檔、回報 13 項焦點前端測試與 8 項瀏覽器驗證通過；未改後端或資料庫。另提供一次唯讀後端邊界檢視，未作最終決策。

主代理獨立檢查來源契約、資料庫寫入及唯讀交易、修訂／撤銷處理、既有失敗不被遮蔽、實際差異、前端文字及一張 360 像素畫面；修正取得時間語意、來源標籤、末端停復牌歧義及來源格式辨識，再執行完整前後端測試與瀏覽器回歸。沒有依賴子代理自評判定完成，也沒有宣稱量測不到的成本節省。

## 尚未驗證或不在本批範圍

沒有宣稱官方交易日、所有停止買賣原因、公司行動或逐筆歷史可用時間已完整覆蓋。尚未替換安裝版、執行新安裝器、提交／推送／發布；沒有實體手機、人工螢幕閱讀器或所有股票／所有官方歷史格式驗收。

未新增真實券商登入、下單、自動交易、報酬承諾或正式自動波段候選。回復只關閉第二版功能，保留資料表與紀錄。
