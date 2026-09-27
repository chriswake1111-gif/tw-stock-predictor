# 研究助理引導與缺項處理第一版

2026-09-28｜1.1.0 研究引導規格。開發驗收與後續安裝交付分別記錄；不以功能發布代表替使用者核准假設或保存個人研究。

## 行為與責任

正式個股頁先呈現「資料是否足夠」「目前研究發現」及一個主要下一步，再分為候選與選擇、完整證據。原始金融結果維持原契約，獨立的 `research_guidance_v1` 負責分工：

| 責任 | 處理內容 | 不可取代的邊界 |
|---|---|---|
| program | 既有資料更新、來源日期及失敗狀態 | 不填假值、不無限重試 |
| assistant | 查找與閱讀來源，保留查證結果 | 不自行採用、核准或計算目標價 |
| user | 閱讀具體選項，建立草稿後明確核准 | 核准不等於資料已證實 |
| engineering | 股數口徑等資料能力缺口 | 不要求使用者猜數字 |

已有適用情境可閱讀，不因其他年度缺項而遮蔽。每個主題最多三張可審閱候選；不可讀、衝突、日期不適用或無法完整帶入限制者沒有草稿入口。查無結果後，優先繼續閱讀或保存部分研究。稍後處理只是收起本次引導。歷史頁維持原唯讀模式。

流程：候選閱讀 → 具體假設預覽 → 建立待核准草稿 → 使用者核准 → 重新讀取程式結果 → 完整保存預覽 → 確認保存。手動編輯留在完整證據的進階入口。

## 資料格式與介面

新增遷移 `20260928_27_research_evidence`，僅建立 `research_evidence_records` 與 `research_evidence_requests` 及其索引、禁止覆寫／刪除的觸發器。不更改既有金融資料、假設或研究列。

查證列保存 canonical symbol、系列、修訂、前版、伺服器查證時間、結構化內容及 SHA-256。來源說法 `summary` 與助理解讀 `interpretation` 分開，來源網址僅保留連結，伺服器不抓取。`source_access` 是閱讀狀態；`review_status` 是可審閱資格，兩者都不代表獲利預估成立。

| 介面 | 用途 |
|---|---|
| GET `/api/v2/research/evidence/{symbol}` | 最新版本；history=true 包含舊版；limit≤100，before 游標分頁 |
| POST 同路徑 | 追加查證；必須同本機實例、合法來源、防偽造請求與 Idempotency-Key |
| GET `/{symbol}/reuse` | 相同年度／scope 24 小時內查無合格來源的重查提示；force 或 new_information 不沿用 |
| GET `/{symbol}/{record_id}/candidate` | 回傳既有假設格式與候選版本；未符合資格拒絕 |
| GET journal `/{symbol}/preview?research_year=2026` | 原始研究 + 引導 + 完整內容指紋 + 忽略查詢時鐘的比較指紋 |
| POST assumptions preview／draft | 可攜 candidate_id，核對標的、現行版本及完全相符的 values（含來源與限制） |
| POST journal | 新流程 include_research_context=true 必須附預覽內容指紋；選用年度也納入請求身分 |

正式研究快照固定保存引導、候選、目前查證頁及所有當時有效版本的 ID／SHA-256；超過一頁的完整紀錄由不可改寫的查證版本追溯。`assumption_evidence` 另固定目前假設原先引用的查證版本（即使來源已有新版本），並區分核准狀態；不會以最新來源取代原先依據。舊正式研究不重新解讀或覆寫。回應遺失時使用相同請求重送，先回傳已保存的同一結果；不同內容共用請求 ID 拒絕。預覽後資料、核准、查證或前次研究改變，回傳 409，畫面展示前後差異並要求重新預覽。

原有金融計算、證據 A/B/C/U、核准資格及回測入口未修改。候選選為個人假設仍透過既有服務：核准不升格證據；多來源不平均、不自動撤銷其他系列。

## 工具與技能

`doctor/connect` 新增 research_guidance_contract。研究工具新增：

```text
evidence-list 3491.TWO [--history] [--before RECORD_ID]
evidence-reuse 3491.TWO --year 2026 --scope "2026 全年估值" [--force|--new-information]
evidence-record 3491.TWO --input WORK.json --request-id UUID
evidence-candidate 3491.TWO --id RECORD_ID
review 3491.TWO --year 2026
```

範例為格式說明，不自動操作實際標的。新增命令沒有核准、撤銷或任意網址抓取能力。查證 JSON 詳見 `skills/tw-stock-research/references/research-guidance-v1.md`。兩份技能及引用文件均隨 1.1.0 安裝包交付；更新活躍技能前須備份原檔。程式「交給助理查證」只複製研究需求，明示沒有啟動背景工作。重新查證即使得到相同結果，也帶 previous_id 建立新查證版本以保留新時間；單純重送沿用原請求。

## 容量與磁碟寫入稽核

| 寫入點 | 觸發／頻率 | 大小與保留 | 風險 |
|---|---|---|---|
| 查證版本 | 正在進行的研究中，一次明確追加命令 | 每請求 16 KiB；不可覆寫版本，相同內容去重 | Medium：保留至使用者決定後續管理 |
| 重送請求對照 | 每個新的 request ID | 計入同一配額；同 ID 重送不新增 | Low |
| 引導研究快照 | 使用者預覽後確認一次保存 | 固定當時內容；沿用既有 journal 保留策略 | Medium：隨明確保存增加 |
| 空閒／背景 | 無新增排程、輪詢寫入或自動搜尋 | 無查證寫入 | Low |

查證總配額 64 MiB 是**邏輯容量**：序列化內容加每列 4 KiB 索引／列預留、每個請求對照另預留 512 bytes 及鍵長度；不是整個共用 SQLite／WAL 檔案的物理上限。超過即停寫，不刪紀錄，讀取及同請求重送仍可用。正式 journal 與既有工具收據各自維持原有保留邊界。未新增自動清理、壓縮或閒置重寫。整體風險 Medium，原因是使用者明確保存的研究與查證會持續累積；沒有聲稱磁碟永不增長。

## 遷移、回復與測試方式

隔離舊版資料庫建立假設、核准及正式研究後，使用 SQLite backup 複製並執行新增遷移；逐表比較原有內容，只允許遷移版本表新增紀錄。測試不讀寫正式使用者資料庫。

設定 `RESEARCH_GUIDANCE_ENABLED=false` 後重啟可退回原入口，停止新增查證；原查證仍可讀、已保存研究不變。不刪表、不改舊版 migration、不回填假資料。畫面也提供「切回原研究入口」。正式安裝升級與遷移使用者資料另行處理。

開發驗證使用現有命令：

```text
python -m pytest -q --tb=short --basetemp=<新的隔離目錄> -o cache_dir=<測試快取目錄>
cd frontend
npm.cmd test -- --reporter=dot
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
npm.cmd run test:visual -- research-guidance.spec.ts --project=desktop --workers=1 --output=../output/playwright/<新的驗證目錄>
```

瀏覽器驗證使用實際頁面與去識別固定介面回應，涵蓋首頁搜尋至部分保存、候選預覽至使用者核准；不宣稱已驗證正式安裝版與真實外部資料的連線。後端資料庫／介面測試獨立驗證實際保存與防錯；人員易用性驗收仍待使用者試用。實際結果見 [驗證紀錄](RESEARCH_GUIDANCE_V1_VALIDATION.md)。

## 首批保留的限制

- 過去十二個月每股盈餘（Trailing Twelve-Month Earnings per Share）的股數口徑補強與波浪自動辨識未開發，明確列為資料能力後續工作。
- 無合格原始報告或可讀轉述時可能仍無完整估值，允許保存部分研究。
- 引導不宣稱來源已證實、不增加自動下單、券商帳戶、交易連線或背景搜索。
- 未打包新版安裝程式、未升級現有安裝、未提交或發布。既有 `DOCS/NEXT_TODO.md` 本機變更未修改。
