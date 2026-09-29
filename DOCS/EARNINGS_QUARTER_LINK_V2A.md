# 第二版第一批：直接單季資料與連續四季對照

2026-09-28｜接續 [配股與修訂銜接](EARNINGS_REVISION_LINK_V2A.md)，屬已核准的 [第一批來源驗證](RESEARCH_GUIDANCE_V2_PLAN.md)。**聯電四個連續季度已有直接公布、可逐欄核對的基本每股盈餘及加權平均股數；跨季調整涵蓋範圍與上櫃正向案例仍待補齊，未啟用合計或正式頁面接入。**

## Assessment 與行為變化

前一步找到聯電第四季業績線索，但承載網站的本機下載失敗。本次從聯電公開網站取得季報與財務明細兩份原始檔，並擴充到 2025 第三季、第四季及 2026 第一季、第二季。未重試或繞過原先失敗的網站；聯電索引頁有一次 HTTP 403，已使用可公開讀取的索引與其列出的原始檔連結，沒有登入或解除存取限制。

季報的文字段落列出基本加權平均股數；同次發布的財務表明示基本每股盈餘（Basic Earnings per Share）、歸屬母公司股東的盈餘，以及季度與幣別。新解析器將兩份檔案配對，核對期間、欄序、單位、原值、重複揭露及公布精度。

第四季使用來源直接公布的三個月資料。全年、前三季累計、稀釋股數、期末股數、美國存託股票及美元欄位都不能替代這些輸入。未知版型、缺值、欄位錯置及不相容的重述註記會停止解析；同期間數字不一致則保留原值與原因，不選定贏家。

## 公開來源實證

[聯電季度業績索引](https://www.umc.com/zh-TW/Download/quarterly_results/QuarterlyResults) 列出各季公司報告與財務明細。每組完整抽文，另檢閱季報第 3 頁、財務明細第 2 頁的實際圖像，共 8 頁。這是公司業績發布資料，不宣稱等同完整查核財報或具會計師查核意見。

| 直接公布的季度 | 基本每股盈餘（元） | 歸屬母公司盈餘（百萬元） | 基本加權平均股數（股） | 原始檔 |
|---|---:|---:|---:|---|
| 2025 第三季 | 1.20 | 14,982 | 12,485,162,809 | [季報](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2025/Q3_2025/UMC25Q3_report.pdf)／[財務明細](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2025/Q3_2025/UMC25Q3_financial_statements-E.pdf) |
| 2025 第四季 | 0.81 | 10,055 | 12,487,002,150 | [季報](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2025/Q4_2025/UMC25Q4_report.pdf)／[財務明細](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2025/Q4_2025/UMC25Q4_financial_statements-E.pdf) |
| 2026 第一季 | 1.29 | 16,171 | 12,491,206,358 | [季報](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2026/Q1_2026/UMC26Q1_report.pdf)／[財務明細](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2026/Q1_2026/UMC26Q1_financial_statements-E.pdf) |
| 2026 第二季 | 3.39 | 42,260 | 12,475,080,302 | [季報](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2026/Q2_2026/UMC26Q2_report.pdf)／[財務明細](https://www.umc.com/upload/media/08_Investors/Financials/Quarterly_Results/Quarterly_2020-2029_English_pdf/2026/Q2_2026/UMC26Q2_financial_statements-E.pdf) |

四組文件各有本季、前季及去年同期，共 12 列核對相符。在最近四季視窗中，共 7 次揭露，分布為 2／2／2／1；同季重複揭露的盈餘、股數及每股盈餘一致。比較結果保留所有版本及雙文件雜湊，`selected_version=null`。

**四季都有數字不等於已證明可直接相加。** 本次 `four_quarter_coverage_complete=true` 只表示四個季度均有相符觀測；`cross_period_basis_verified=false`、`calculation_eligible=false`、`value=null` 仍然成立。普通股發行、買回等造成加權股數變動，不必然需要把每股盈餘調成相同分母；但配股、分割及追溯修訂需檢查對應基準，不能由股數相近或重複揭露一致推定整段期間無需調整。

取得紀錄與原檔雜湊只綁定本機實際讀取內容，不自動驗證來源真實性。`available_at` 使用兩份檔案較晚的取得時間；`source_published_at=null`，不能以檔名、季末或頁面日期倒填公告時間，也不取得歷史回測資格。

### 上櫃來源仍保留具體缺口

本次另取得 [穩懋 2025 第四季簡報](https://www.winfoundry.com/en-US/Base/DownLoadFile/525?filename=4Q%202025%20Presentation.pdf&TargetTable=QuarterlyAttachment)，共 41 頁，抽取文字檢查盈餘與股數相關揭露，並實際檢閱第 13、16 頁圖像：第 13 頁季度表列 2.43 元，但沒有明示基本或稀釋口徑；第 16 頁的股數說明用於每股淨值，不能代替期間加權平均股數。第四季表另標示未經查核。沒有生成可計算輸入，也沒有為穩懋新增自動解析器。

原檔 3,388,906 位元組，SHA-256 為 `c4b87c27ff345b5df8ffc75a0566307c6109f3bf8860dd50df924bb708b91909`。本次助理閱讀紀錄另存 `quarter-source-review-2.json`，保留前版而非覆寫。這不是完整的上櫃市場來源搜尋，也不代表穩懋所有財報都缺此資料。

## Modified Files 與資料契約

- `src/collectors/earnings_quarter_audit.py`：新增受限的雙文件解析、直接單季核對及連續四季比較。正式版型僅涵蓋此次檢驗的聯電格式；純記憶體處理，沒有網路或資料庫操作。
- `tools/audit_earnings_sources.py`：新增可獨立使用或與原入口並用的 `--quarter-releases-manifest`。核對檔案雜湊、取得紀錄、可選大小與公司／季度，拒絕多餘資格欄位及重複文件組。
- `tests/test_earnings_quarter_audit.py`：新增 45 項去識別固定案例，驗證基本／稀釋與幣別分離、負值及零值、欄位與期間錯配、雙文件一致性、觀測時間、版本衝突、缺季、輸入限額與失敗不寫入。
- `DOCS/RESEARCH_GUIDANCE_V2_PLAN.md`、`DOCS/EARNINGS_REVISION_LINK_V2A.md` 及本文件：銜接當前進度、重現證據與未完成事項。

離線新增格式為 `earnings-quarter-release-reconciliation-v1`。輸出增列 `quarter_release_audits` 及 `quarter_release_comparisons`，與原結構化財報、附註資料並列，不能被自動採用。頁碼、原文偏移量及數字欄位位置可回查；「相符」只表示讀取與交叉核對一致。

正式金融資料、資料表、研究預覽與保存介面無契約變更。沒有修改模型規則、核准、研究保存或第一版顯示行為。

## 重現與驗證

原檔及取得紀錄位於忽略提交的 `.tmp_earnings_v2_20260928/`。`quarter-releases-1.json` 有 4 組，各組形式如下，數字只能來自文件，不接受清單提供的盈餘或「已驗證」宣告：

```json
[
  {
    "symbol": "2303.TW",
    "year": 2025,
    "quarter": 4,
    "report": {"path": "umc-2025q4-report.pdf", "sha256": "原檔內容雜湊", "url": "原始檔網址", "observed_at": "實際取得時間"},
    "statements": {"path": "umc-2025q4-statements.pdf", "sha256": "原檔內容雜湊", "url": "原始檔網址", "observed_at": "實際取得時間"}
  }
]
```

以上是欄位示意，不是可直接執行的來源紀錄。兩份檔案須在清單同一目錄，`bytes` 可選但提供時必須符合實際大小。

```powershell
python -X utf8 tools/audit_earnings_sources.py --quarter-releases-manifest .tmp_earnings_v2_20260928/quarter-releases-1.json
python -X utf8 tools/audit_earnings_sources.py --manifest .tmp_earnings_v2_20260928/xbrl-sources-final.json --notes-manifest .tmp_earnings_v2_20260928/note-sources-2.json --quarter-releases-manifest .tmp_earnings_v2_20260928/quarter-releases-1.json
python -X utf8 -m pytest -q tests/test_earnings_source_audit.py tests/test_earnings_note_audit.py tests/test_earnings_revision_audit.py tests/test_earnings_quarter_audit.py --basetemp .tmp_earnings_v2_20260928/pytest-next-unused -p no:cacheprovider
```

工具預設只輸出；只有指定 `--output` 才新建結果檔，既有檔不可覆寫。退出碼 `1` 代表稽核完成但整體來源關卡未過，`2` 代表輸入或寫入失敗；不得把 `1` 解讀為來源已可計算。測試暫存目錄每次使用新名稱。

- 焦點測試：**173 passed**，包含 36 項結構化財報、47 項附註、45 項修訂及本次 45 項直接單季案例；目錄 `pytest-quarter-focus-final`。
- 單獨實際核對：`audit-quarters-2.json`，4 組、12 列相符、最近四季 7 次揭露。40,326 位元組，SHA-256 `5fab620437e82027f879b05b40bf2fd2f65133befd22c869a7fa5937424d11eb`。
- 清單 `quarter-releases-1.json`：3,487 位元組，SHA-256 `29aaaebdb00b13e2da435f23cc756a3b20b72a4017427b2a9147e71db29e9cfe`，含 8 份檔案的完整網址、大小、取得時間及內容雜湊。
- 完整 Python 回歸：**1254 passed、1 個既有 Starlette 棄用警告，205.37 秒**。指令為 `python -X utf8 -m pytest -q --basetemp D:\Tools\tw-stock-predictor\.tmp_earnings_v2_20260928\pytest-quarter-full-final -p no:cacheprovider`，日誌 `python-quarter-regression-final.log`。測試使用隔離目錄，未連入正式使用者資料庫。
- 三種來源並用：`audit-quarter-final.json`，7 份結構化財報、6 份完整財報、4 組季度發布資料；38 列附註及兩條配股修訂核對保持原值，另有 12 列直接單季核對相符。原有 `audit-revision-final.json` 的每個欄位深度比較完全相同，新季度欄位也與獨立執行結果相同。新結果 207,624 位元組，SHA-256 `af55d6f1664e329b83f70952bb8c891744dcb2bcfb084229011d93040a0529c8`。退出碼為 `1`，保留來源關卡未過的狀態。PDF 讀取另有既有文件的 `/Perms` 驗證警告，沒有因此宣稱文件已驗真或取得歷史資格。
- 已執行 Python 語法、空白檢查及本次修改範圍檢視；原待辦文件雜湊未變，新結果分檔保存，沒有覆寫原檔或先前輸出。
- 本次未改前端、正式服務或資料表；前端測試、型別、程式檢查及建置未重跑，沿用前一步的證據。360／768／1024／1440 像素、鍵盤及新版安裝驗收尚未執行，待來源接入頁面後驗證。

## 磁碟寫入檢查與回復

| Item | Result |
|---|---|
| Local write paths found | 解析器不寫檔；工具僅寫使用者明確指定的新結果檔。研究樣本、圖像及測試日誌位於上述隔離目錄 |
| High-frequency write risks | 無事件循環、重試循環或逐欄寫入 |
| Idle write risks | 無排程、計時器或背景工作，待機不寫入 |
| Log / telemetry risks | 無新增正式日誌或遙測；工具每次輸出一次結果或錯誤 |
| SQLite / IndexedDB risks | 新路徑無資料庫操作；測試使用隔離資料，未接觸使用者資料庫 |
| Cache / temp file risks | 開發用原檔、圖像與測試產物保留本機；手動清理，無新增正式快取 |
| Autosave / history risks | 無自動保存研究、核准或全文歷程 |
| Retention limits | 單份原檔 4 MiB、清單 16 KiB、最多 6 組 12 份季報 PDF、結果 2 MiB；每份最多 120 頁、每頁文字 50,000 字元、合計 1,000,000 字元 |
| Cleanup strategy | 不覆寫、不自動刪除；開發樣本與測試產物另待使用者授權清理 |
| Production debug status | 未接入安裝版，無新增正式除錯寫入 |
| Overall risk | 工具寫入 Low；開發樣本與測試產物保留為 Medium，因清理為手動 |
| Required fixes before completion | 無高頻寫入修正項；不宣稱任意惡意 PDF 的記憶體解壓縮已全面防護 |

可停止使用新離線參數而回到前一步稽核；第一版完全不依賴此入口。無資料遷移或資料表刪除。新舊樣本、輸出均分檔保留。使用者原有 `DOCS/NEXT_TODO.md` 保持不動，SHA-256 為 `754e5431a901957df41965aeaa5924b7b479f084b82a5c0c29caa5e99377f645`。

## Remaining Limitations 與下一步

1. 聯電已補齊直接單季原值，接續核對完整期間的股數調整及財報修訂涵蓋範圍；單季相符與四季可比性分開驗收。
2. 上櫃案例仍需明示基本口徑、盈餘分子及期間加權平均股數的第四季資料。昇達科全年及前三季不能直接相減；穩懋簡報尚不足以補此缺口。這是來源與工程工作，不要求使用者猜值或核准資料正確性。
3. 合格公開案例到位後，才接入不可覆寫快照、四季合計、原研究頁及保存指紋，並執行完整操作驗收。第二版第一批尚未達完成標準。

未保存正式研究、核准假設、替換安裝版、提交、推送或發布。未新增真實下單、自動交易或其他超出產品邊界的功能。
