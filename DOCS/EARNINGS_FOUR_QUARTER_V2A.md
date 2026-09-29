# 第二版第一批：四季獲利資料、研究頁與保存驗證

2026-09-28｜開發交付紀錄；非安裝／發布紀錄。基底 `main@8147ef6d`。

## 完成範圍

承接 `RESEARCH_GUIDANCE_V2_PLAN.md` 與前四份來源稽核。新增兩個可由原始公開文件重跑的正向案例，將合格結果接到受控更新、不可覆寫公開資料快照、三層研究頁與既有保存預覽。第一版入口與核准／保存邊界保留。

**這是有限來源範圍的第一批實作，尚不是所有股票、所有後續季度都能自動取得的通用服務。** 第一批內容登錄在 `src/collectors/earnings_sources_v2.py`，每份文件有固定網址、公司、期間、用途與 SHA-256。新增季度或來源修訂需重新核對、測試及更新登錄；不能把下載成功或使用者勾選當成資料資格。

## 公開案例與核對依據

| 案例 | 2025 第三季 | 2025 第四季 | 2026 第一季 | 2026 第二季 | 程式合計（元／股） |
|---|---:|---:|---:|---:|---:|
| 聯電 2303.TW（上市） | 1.20 | 0.81 | 1.29 | 3.39 | 6.69 |
| 譜瑞 4966.TWO（上櫃） | 10.45 | 6.97 | 6.77 | 7.66 | 31.85 |

以上是特定來源版本的計算驗證，涵蓋 2025-07-01 至 2026-06-30。四季已公布比率相加，不等於公司以全年加權平均股數計算的年度每股盈餘；不作全年預估獲利、目標價或回測輸入。

### 聯電

- 沿用前批四組公司季度發布說明及財務明細，核對普通股基本每股盈餘、母公司股東損益、精確加權平均股數與相鄰季度重複揭露。見 [直接單季原值對照](EARNINGS_QUARTER_LINK_V2A.md)。
- [2025 年完整合併財報查詢](https://doc.twse.com.tw/server-java/t57sb01?step=9&kind=A&co_id=2303&filename=202504_2303_AIA.pdf)：第 11 頁權益變動表、第 63–67 頁股本與員工股份附註、第 78 頁基本及稀釋每股盈餘。
- [2026 年上半年完整合併財報查詢](https://doc.twse.com.tw/server-java/t57sb01?step=9&kind=A&co_id=2303&filename=202602_2303_AIA.pdf)：第 7 頁權益變動表、第 37–38 頁股本與庫藏股、第 53–54 頁每股盈餘附註。
- 程式完整消耗已支援的權益表列項，核對普通股股本期初＋變動＝期末，以及年末與翌年期初連續性。員工股份變動與庫藏股行動不能被誤解為一律需要追溯調整的股票分割。
- 官方下載頁使用舊式中文編碼；已實測一般公開查詢可取得文件連結。採集器只解析固定文件的 ASCII 路徑，不執行網頁指令、不接受任意下載網址。

### 譜瑞

- 公司 [季度資料索引](https://www.paradetech.com/quarterly-results/) 的四份財務明細，每份第 1 頁直接列出新臺幣單季基本每股盈餘、對應盈餘與加權平均股數，另有美元及累計欄。程式核對欄位順序，不混加美元、稀釋或累計資料。
- [2025 年完整合併財報](https://www.paradetech.com/wp-content/uploads/2026/03/2025Q4-FR_Eng.pdf)：第 36 頁完整普通股／庫藏股變動表，第 44–45 頁每股盈餘附註。
- [2026 年上半年完整合併財報](https://www.paradetech.com/wp-content/uploads/2026/08/2026Q2-FR_English.pdf)：第 36 頁股數變動表，第 43–44 頁每股盈餘附註。
- 程式逐列核對受限股份歸屬／註銷、庫藏股購入、轉予員工、註銷及表內加總，並核對年末與翌年期初。不是以「未搜到公司行動」推定股數基準不變。
- 季度發布資料包含未經查核的報表，不能把整條資料鏈統稱經查核財報。英文翻譯的法律效力依原文件說明；本批沒有替譯文背書。

### 資格限制及拒算案例

完整揭露表、來源內容指紋、欄位核對及重複期間對照共同構成此批資格；內容指紋不是金融數字或使用者核准。核對範圍僅及這些文件與報表期間，不是全市場公司行動監控，也沒有證明其後沒有新報告。

聯亞配股修訂案例仍依 [前批驗證](EARNINGS_REVISION_LINK_V2A.md) 保留調整證據，不因兩個期間的差異可解釋就自動選定四季基準。昇達科及其他未登錄公司維持缺項，交由開發端補強格式或助理查證，不能用自訂值填補。

## 實作、資料契約與回復

- `earnings_four_quarter.py`：新契約 `earnings-four-quarter-v1`，直接單季解析、完整股本／股數表核對、四季連續性、來源版本衝突與十進位合計。零與負值有效；缺季、基本／稀釋、幣別、報表範圍與來源衝突均拒算。
- `earnings_sources_v2.py`：16 份已核對公開文件（聯電 10、譜瑞 6）。不是股價或獲利數字白名單；任何一份位元組改變，都不能沿用既有資格。
- `earnings_public_data_service.py`：只經既有明確研究更新作業、外連客戶端與有效寫入能力執行；每次下載前和寫入前重新檢查。保留 TLS、禁止轉址、期限及有限重試。分析與摘要讀取不抓網站。
- 沿用 `daily_public_snapshots`、`daily_public_attempts`，**無資料表或遷移變更**。新資料集為 `VerifiedQuarterlyEarnings`；原有 FinMind 季資料不改寫、不冒充公司原始財報。
- 原始 PDF 以 Base64 納入不可覆寫快照；標準化內容、內容指紋、解析器版本、來源、頁碼、每季輸入版本及本機觀測時間一併保留。發布時間未知時為空；今天收到的資料不倒填成歷史可知資料。
- 新收到但尚未核對的修訂以 `quality_warning`／空值保存原始內容與 `unreviewed_source_versions`，保留前版逐季資料及 `previous_snapshot_id`，不解析新文件、不產生可採用數字。若後續恢復為原先內容，以最新作業引用既有快照，避免重複與讀錯版本。
- 合計 `value`、原值與分母使用十進位字串；單位 `TWD_per_share`。`period_start`／`period_end` 與資料取得時間分開；`sources`、`basis`、`rows[].versions` 可追溯。`historical_eligibility=false`、`forward_eps_eligible=false`。
- `sources[].observed_at` 是各文件下載完成時間，`observed_at` 是整組文件的觀測時間，`ingested_at` 是標準化內容準備寫入的時間，`available_at` 不早於上述時間。讀取截止時間早於匯入時不得選到新資料；重查同一版本保留原首次匯入時間，另記 `last_checked_at`。
- FIN-01 登錄為 C 級專案操作設計；不得進入已驗證核心、杜金龍主張、預估估值或回測訊號。既有估值、波浪及核准語意不變。
- 研究頁顯示值／缺項、日期、責任與下一步。來源細節預設收合，不新增數字表單。保存預覽及變更比較顯示本次獲利內容；繼續使用原內容指紋與重送保護。歷史內容固定。
- 研究工具 `connect`／`doctor` 增加 `earnings_research_contract`，既有 `review` 自動攜帶新資料；沒有助理核准、撤銷或自動保存命令。兩份**專案內技能副本**已更新，未改使用者已安裝技能。

開關：`RESEARCH_EARNINGS_V2_ENABLED=true` 才接入新採集與當前研究。原始碼執行預設關閉；1.2.0 封裝程式啟動時預設開啟，明確設定 `false` 仍可關閉。啟動與唯讀查閱不自動採集。回復只關閉此開關，保留新快照、不刪表、不覆蓋使用者資料。保存過的新研究仍能依固定內容閱讀。

資料期末超過 135 日時以 `earnings_period_requires_refresh` 停止顯示目前可用合計；這是保守的產品提示，不是公告期限，也不代表 135 日內已證明來源最新。成功重查同一批文件只更新查核時間，不改變其涵蓋期間。

## 重跑與驗證

開發來源工具新增 `--four-quarter-manifest`，每次一家公司、最多 12 份文件；每項包含 `symbol`、`key`、`path`、`sha256`、`url`、`observed_at`。文件必須在清單同目錄，網址與內容必須符合登錄。清單最多 16 KiB，拒絕重複項、越界路徑及額外核准欄位。

```powershell
python -X utf8 tools/audit_earnings_sources.py --four-quarter-manifest <manifest.json> --output <new-result.json>
```

回傳 0 代表指定的本機樣本算出結果；原來未通過來源關卡的模式仍回傳 1；無效輸入回傳 2。輸出拒絕覆寫。`calculation_enabled=false` 表示沒有啟用安裝版；本機檔案的取得紀錄不能冒充程式自行驗證的網路傳輸。

本機原始 PDF、取得紀錄、來源報告、測試資料庫及長日誌位於 `.tmp_earnings_v2_20260928/`，不納入 Git。可重現的來源網址、雜湊、程式與去識別測試保留於原始碼。

| 驗證 | 實際結果 |
|---|---|
| 原始 PDF 重跑（10＋6 份） | 聯電 6.69、譜瑞 31.85；四季為 2025 第三季至 2026 第二季 |
| 受控網路採集 → 隔離資料庫 → 讀回 | 兩例通過；聯電下載頁編碼問題修正後重跑成功 |
| 原有表比對 | 隔離來源驗證其餘 63 表內容未變；另以含舊研究、假設與核准的固定資料測試保存不變 |
| 焦點回歸 | 首輪 268 項通過；最後時間邊界及相關儲存／工具回歸 105 項通過，新增案例均納入下列完整回歸 |
| 完整 Python 回歸 | 1320 項通過，180.44 秒；1 項既有 Starlette 相依套件棄用警告，無失敗 |
| 前端單元測試 | 20 個檔案、139 項通過，8.94 秒 |
| 型別、程式檢查、建置 | 通過；建置含正式產物秘密檢查 |
| 瀏覽器 | 360／768／1024／1440 各驗證可用與缺項狀態，8 個流程通過；鍵盤閱讀來源、預覽與部分研究保存通過 |
| 可及性與畫面 | 同上流程無橫向溢出、無應用錯誤；自動可及性檢查通過，另實際閱讀截圖 |
| 安裝包、正式安裝、提交與發布 | 未執行；依核准計畫另行處理 |

瀏覽器環境為本機開發頁 `http://127.0.0.1:4173`、Edge／Playwright、虛構財務資料。未提供 Browser skill，使用既有 Playwright 流程。首次測試攔截到開發模組網址，修正測試路由只攔 `/api/` 後通過；不是安裝版操作證據。截圖保存於本次 Codex 視覺產物目錄。

前端最初因既有快取檔權限無法啟動檢查；使用所需檔案權限重跑後通過，未改資料夾存取規則。Python 測試如有既有相依套件警告，與失敗分開報告。

最後證據均位於 `.tmp_earnings_v2_20260928/`：

- `python-v2-final.log`：完整回歸；指令 `python -X utf8 -m pytest -q --basetemp .tmp_earnings_v2_20260928/pytest-v2-final -p no:cacheprovider`。
- `frontend-unit-final.log`、`frontend-build-final.log`、`frontend-browser-complete.log`：前端單元測試、型別與建置、瀏覽器流程；另執行 `npm.cmd run lint` 通過。
- `live-v2-final-collection-result.json`：兩例受控採集、來源取得與匯入時間、隔離資料庫讀回、63 個其他資料表內容雜湊比對通過；資料庫約 17.1 MiB。
- `umc-four-quarter-receipted.json`、`parade-four-quarter-receipted.json`：使用原始下載取得紀錄時間的離線來源重跑。

## 修改檔案範圍

| 檔案 | 用途 |
|---|---|
| `src/collectors/earnings_four_quarter.py`、`earnings_sources_v2.py` | 四季解析、資格核對及已閱讀來源目錄 |
| `src/services/earnings_public_data_service.py` | 明確更新、不可覆寫儲存、修訂拒算、讀取時間與容量限制 |
| `src/services/daily_public_data_service.py`、`current_research_service.py`、`installed_data_sync_service.py`、`research_guidance_service.py` | 沿用快照選取介面，接入受控更新、研究內容與缺項分工 |
| `src/collectors/installed_egress_client.py`、`src/domain/installed_data_operations.py` | 限定來源網路入口及既有作業授權 |
| `src/api/routes/runtime.py`、`src/research_assistant/client.py`、`cli.py` | 工具能力宣告 |
| `frontend/src/components/EarningsResearchPanel.tsx`、`DailyPublicDataPanel.tsx`、`GuidedResearchWorkspace.tsx` | 四季內容、完整證據、保存預覽及變更比較 |
| `config/model_rules.yaml`、`requirements.txt` | FIN-01 專案規則登錄與固定版本 PDF 解析依賴 |
| `tools/audit_earnings_sources.py` | 延伸既有離線來源驗證入口 |
| `tests/test_earnings_four_quarter.py`、`test_earnings_public_storage.py`、`test_earnings_four_quarter_cli.py` | 解析、拒算、時序、權限、去重、舊資料保留與工具驗證 |
| `frontend/src/test/earnings-research.test.tsx`、`earningsResearchFixture.ts`、`frontend/e2e/earnings-research.spec.ts` | 去識別畫面、鍵盤、保存與四種寬度案例 |
| `skills/tw-stock-research/SKILL.md`、`skills/du-jinlong-research-method/SKILL.md` | 專案內研究技能的資料資格與保存說明 |
| 本文件、`DOCS/RESEARCH_GUIDANCE_V2_PLAN.md` | 交付狀態、界線、驗證與回復方式 |

前批來源稽核程式、測試與文件仍保留於工作區，未在此批重寫；既有 `.gitignore` 的本機來源產物排除規則保留。`DOCS/NEXT_TODO.md` 不屬於本次修改。

## 磁碟寫入稽核

| Item | Result |
|---|---|
| Local write paths found | 既有資料庫的兩個公開資料表；隔離開發產物；使用者確認才寫正式研究 |
| High-frequency write risks | 無逐事件／逐字寫入；同版本去重，成功後 24 小時內沿用，失敗後 30 分鐘內不重試 |
| Idle write risks | 無背景搜尋、計時寫入或讀取時自動收集 |
| Log / telemetry risks | 不新增遙測；錯誤不保存網頁指令、登入資料或原始網路錯誤字串 |
| SQLite / IndexedDB risks | 快照與作業紀錄在同一交易追加；沒有 IndexedDB 或新資料表 |
| Cache / temp file risks | 生產採集在記憶體處理，不另寫暫存 PDF；開發原始樣本留本機 |
| Autosave / history risks | 原始金融文件的本機保留與正式研究分開；不自動核准／保存研究 |
| Retention limits | 每文件 8 MiB、每組原始資料 16 MiB、120 頁／100 萬文字限制；本資料集原始＋標準化內容共 128 MiB，更新紀錄最多 4096 筆；既有查證區 64 MiB 不變 |
| Cleanup strategy | 額滿停止新增並說明，不自動刪除舊版本；後續整理須明確選擇範圍 |
| Production debug status | 未新增生產除錯日誌；公開 PDF 格式提示不含使用者研究內容 |
| Overall risk | Medium：有硬上限且無閒置寫入，但原始財報占用空間，版本整理為人工決策 |
| Required fixes before completion | 無未處理的高頻／無界寫入路徑 |

## 尚未涵蓋

其他公司、未登錄季度、不同會計年度、非普通股及尚未釐清的修訂仍保留缺項。此批不做波浪自動辨識、不改舊 `analysis_service` 財報路徑、不宣稱行情品質或回測關卡通過。無券商 API、真實下單或自動交易能力。

`DOCS/NEXT_TODO.md` 的既有使用者修改保持原樣；本次未保存或核准任何使用者研究，未替換安裝版。
