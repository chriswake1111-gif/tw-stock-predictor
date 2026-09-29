# 第二版第一批：配股與修訂銜接驗證

2026-09-28｜接續 [附註對應驗證](EARNINGS_NOTE_LINK_V2A.md)，屬已核准第一批的來源關卡。**本次完成特定股票股利事件與同期間財報差異核對；未完成完整四季來源驗收，未啟用合計或正式頁面接入。**

後續進度見 [直接單季與四季對照](EARNINGS_QUARTER_LINK_V2A.md)：已另從聯電原站取得 8 份原始檔，完成四季直接原值核對；下文保留前一步的查證與測試時點，不視為最新來源狀態。

## Assessment 與本次行為

先前能辨識「同期間盈餘相同、股數不同、新版註記追溯調整」，但未把它連到調整原因。本次從財報的資本與股利附註讀取股票股利、面額、決議日、生效核准日及增資基準日，再核對指定舊、新版本。

程式只支援已檢驗的單一普通股股票股利揭露格式。找不到、格式不支援或多個事件時，都不推定公司沒有股數變動。員工限制股票、庫藏股、可轉債及其他會計項目的追溯調整，不能冒充這個配股事件。

核對通過只表示「所揭露事件與這一對數字相符」，不是公司明示替代版本、整條修訂鏈已驗證或四季皆可比。輸出保留原值、兩份原始檔及結構化財報的內容雜湊、頁碼、事件日期、觀測時間與未解決的選版狀態；不產生自行調整的每股盈餘。

## 公開來源實證

### 聯亞：調整原因與兩組數字相符

在 [2026 第二季財報](https://www.lmoc.com.tw/index.php?option=module&lang=cht&task=dfile&id=396&i=1) 第 17、18 頁：每股面額為 10 元，普通股股票股利每股 1 元，因此揭露的配股比例對應股數因子為 `1 + 1 / 10 = 1.1`。日期依原文分別為 2026-05-27、2026-06-10、2026-07-21；原文同時說明報導日登記尚未完竣，程式保留此狀態，不改成「已完成增資」。兩頁已轉成圖片檢閱。

與 [2025 第二季財報](https://www.lmoc.com.tw/index.php?option=module&lang=cht&task=dfile&id=36&i=1) 的相同期間比較：

| 期間 | 兩版盈餘分子（千元） | 舊／新加權平均股數（千股） | 舊／新基本每股盈餘（元） |
|---|---:|---:|---:|
| 2025 第二季 | 96,786 | 91,813／100,994 | 1.05／0.96 |
| 2025 上半年 | 139,055 | 91,813／100,994 | 1.51／1.38 |

在各欄已公布的小數精度內，兩組差異均與 1.1 配股因子相容。這是「來源揭露加上數值一致性」的推論，不以小數精度的相容性代替正式版本替代聲明。新版註記追溯調整、同期間、盈餘分子相同、報表範圍相同及股數相符都必須成立；稀釋值不套用此核對。

六份附註仍有 **38 列成功對應**。新結果多出兩條具事件依據的修訂核對，但 `selected_version` 仍為空、`cross_quarter_basis_verified=false`。沒有把上半年累計當成單季。

### 第四季來源：保留找到什麼及為何仍不適用

| 原始來源 | 實際讀取範圍 | 本次結論 |
|---|---|---|
| [台積電 2025 第四季財務摘要](https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2026-02/cb73d5f4e019a8f6d7a494a0f8f6c6da2dfc4ee2/FS.pdf) | 全 4 頁抽文；第 2 頁圖像 | 19.50 元列明為稀釋每股盈餘，股數也是稀釋口徑，不能用來補入基本每股盈餘系列 |
| [穩懋 2025 第四季新聞稿](https://www.winfoundry.com/en-US/Base/DownLoadFile/522?TargetTable=QuarterlyAttachment&filename=4Q+2025+Press+Release.pdf) | 全 3 頁抽文；第 1 頁圖像 | 公布每股盈餘 2.43 元，但本稿未明示基本或稀釋，也沒有對應加權平均股數；保留線索，不提供可計算輸入 |

這兩項是助理對原文的查證結果，**不是新增的自動新聞稿解析器**。原檔、取得紀錄與雜湊存於隔離資料夾，`quarter-source-review-1.json` 明列 `parser_verified=false`、`calculation_eligible=false`、讀取範圍及拒用原因，不能被當成金融資料匯入憑證。

另找到 [聯電發布的第四季業績](https://www.businesswire.com/news/home/20260128921163/en/UMC-Reports-Fourth-Quarter-2025-Results) 與原始報告線索，網頁讀取可見基本每股盈餘與股數欄位；但本機下載該承載網站時發生名稱解析失敗，尚未取得可核對雜湊的原檔。它是下一個優先來源，不是已驗收的上市正向案例。本次未完成所有公司的全面來源搜尋。

## Modified Files 與資料契約

- `src/collectors/earnings_revision_audit.py`：新增單一股票股利揭露擷取與同期間基本值比較，純記憶體處理，不進行下載或寫入。
- `src/collectors/earnings_note_audit.py`：將事件綁定本次讀取的原始檔、公司與財報，加入 `share_event_evidence` 及 `revision_links`；資料格式由 `earnings-note-reconciliation-v1` 升為 `v2`，事件核對格式為 `earnings-share-event-link-v1`。
- `tests/test_earnings_revision_audit.py`：新增去識別、固定案例的事件、日期、來源、基本／稀釋、正負及零值、版本與精度測試。
- `DOCS/RESEARCH_GUIDANCE_V2_PLAN.md`、`DOCS/EARNINGS_NOTE_LINK_V2A.md` 及本文件：銜接進度、來源、驗證及剩餘限制。

既有稽核工具使用 `--notes-manifest` 就會輸出新核對結果，沒有增加命令入口。只更動開發中的離線結果格式；正式金融資料、資料庫、研究預覽與保存介面均無契約變更。未變更模型規則資格或核准流程。

## 重現、測試與寫入範圍

```powershell
python -X utf8 tools/audit_earnings_sources.py --manifest .tmp_earnings_v2_20260928/xbrl-sources-final.json --notes-manifest .tmp_earnings_v2_20260928/note-sources-2.json
python -X utf8 -m pytest -q tests/test_earnings_source_audit.py tests/test_earnings_note_audit.py tests/test_earnings_revision_audit.py --basetemp .tmp_earnings_v2_20260928/pytest-next-unused -p no:cacheprovider
```

測試暫存目錄每次使用新名稱。最終驗證結果：

- 焦點測試 **128 passed**，含 36 項結構化財報、47 項附註及 45 項修訂核對案例；最終目錄為 `.tmp_earnings_v2_20260928/pytest-revision-focus-final`。
- 完整 Python 回歸 **1209 passed、1 個既有 Starlette 棄用警告，176.09 秒**。指令為 `python -X utf8 -m pytest -q --basetemp D:\Tools\tw-stock-predictor\.tmp_earnings_v2_20260928\pytest-revision-full-final -p no:cacheprovider`，日誌 `.tmp_earnings_v2_20260928/python-revision-regression-final.log`。使用隔離測試目錄，未連入正式使用者資料庫。
- 實際來源重跑 `audit-revision-final.json`：7 份結構化文件、6 份完整財報、38 列相符、兩條事件核對相符；無選定版本、無合計。輸出 167,438 位元組，雜湊 `c786fb3850e450d4ae8fb453d53ec49ff84b7ea0a058423e2f23cd5055bdd626`。工具退出碼 `1` 表示整體來源關卡仍未過，不是所有資料已可計算。
- 已執行 Python 語法、空白檢查與變更範圍檢視；原有待辦文件雜湊未變。來源及舊版 `audit-notes-2.json` 保留，沒有覆寫。
- 本次沒有改前端、正式服務或資料表；前端測試、型別、程式檢查及建置未重跑，沿用前一步所列證據。360／768／1024／1440 像素及鍵盤驗收尚待接入頁面後執行，不能列為本次通過。

事件解析不讀寫資料庫、不寫檔、不執行來源文字、不建立計時器、背景搜尋或重試。沿用每份 4 MiB、清單 16 KiB、輸出 2 MiB 上限及只能新建輸出檔的限制；不增加全文快取，沒有待機寫入。新增程式路徑的磁碟寫入風險為低，仍不宣稱任意惡意 PDF 的記憶體解壓縮已完全防護。

回復可停用離線附註稽核；第一版研究頁完全不依賴這個開發工具。新舊輸出分檔保留，不刪表、覆寫金融資料或搬移核准。使用者原有 `DOCS/NEXT_TODO.md` 雜湊保持 `754e5431a901957df41965aeaa5924b7b479f084b82a5c0c29caa5e99377f645`。

## Remaining Limitations 與接續順序

1. 優先取得具有基本每股盈餘及股數明細的第四季原始檔，完成上市、上櫃各一套連續四季的對照。聯電線索待本機原檔核驗；穩懋需完整明細補足口徑，不能只採新聞稿。
2. 配股樣本現在可解釋特定兩版的差異，但完整修訂鏈、多事件及跨季共同比較基準尚未完成。不能自動將新值套回所有舊季。
3. 來源合格後才接入已核准的不可覆寫快照、四季合計、原研究頁與保存指紋，並做前端與實際操作驗收。第一批尚未達完成標準。

以上由工程與助理處理，不要求使用者猜數字或核准資料正確性。沒有新增真實下單、自動交易、自動採用或核准；未保存正式研究、替換安裝版、提交、推送或發布。
