# Evidence Model V2 Phase 10 — Production Data Foundation & Freshness Governance

Phase 10 建立可重現、可稽核的 production-data 基礎，但不新增分析規則、不改變 Evidence Model `2.0.0`，也不讓自動化取代人工核准。

## 資料流程與信任邊界

```text
Official Source
  → explicit CLI ingestion
  → provider/resource registry
  → immutable run + run item + raw revision
  → schema / unit / availability validation
  → explicit eligibility
  → existing Evidence Model repository
  → immutable snapshot
  → read-only dependency freshness comparison
```

`fetch succeeded`、`schema validated`、`eligible` 與 `human approved` 是不同狀態。自動化可建立候選，不得建立或冒用 human approval。舊值可作 last-known-good 參考，但 last-known-good 不等於 fresh。

## 官方來源與用途

| Provider | Resource | Endpoint / authority | Phase 10 use |
| --- | --- | --- | --- |
| TWSE | Daily turnover | `exchangeReport/FMTQIK` via [TWSE OpenAPI](https://openapi.twse.com.tw/) | TWSE 官方每日成交金額 |
| TPEx | Daily turnover | `tpex_daily_trading_index` via [TPEx OpenAPI](https://www.tpex.org.tw/openapi/) | TPEx 官方每日成交金額 |
| TWSE | Trading schedule | `holidaySchedule/holidaySchedule` via [TWSE OpenAPI](https://openapi.twse.com.tw/) | 只保存官方明示的交易、假日、無交易與特殊日 |
| CBC | M1B | `EF15M01` via [CBC API instructions](https://cpx.cbc.gov.tw/Data/ExportToAPIInfo) | 月資料候選；歷史發布時間需另有官方 mapping |

TWSE 與 TPEx 的公開資料授權仍應依各資料集與使用情境逐項確認；Phase 10 不包含即時行情、轉售或再散布授權。CBC 公開資料使用亦應遵守[政府資料開放授權條款](https://www.cbc.gov.tw/tw/cp-307-36676-DE020-1.html)與來源標示。

### Provider licensing matrix

未由目前官方條款明確證明的用途一律維持 `UNKNOWN / REQUIRES REVIEW`，不得由技術上可下載推論為可再散布。

| Provider | Resource | Store raw | Store normalized | Browser display | Redistribution | Attribution | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TWSE | Daily turnover／trading schedule | 僅保存必要稽核 revision／hash | 本機研究用途 | 僅衍生 freshness／provenance | UNKNOWN / REQUIRES REVIEW | 顯示 TWSE 與 dataset | 逐資料集確認 |
| TPEx | Daily turnover | 僅保存必要稽核 revision／hash | 本機研究用途 | 僅衍生 freshness／provenance | UNKNOWN / REQUIRES REVIEW | 顯示 TPEx 與 dataset | 逐資料集確認 |
| CBC | EF15M01 M1B | 僅保存必要稽核 revision／hash | 本機研究用途 | 僅衍生 freshness／provenance | 依政府資料開放授權及來源條款另行確認 | 必須標示 CBC 與資料集 | publication-time mapping 仍需稽核 |

## `available_at` 與歷史語意

- 當次官方查詢只能證明資料在 `received_at` 時已可取得；此保守觀測時間可用於當次 daily turnover／calendar ingestion。
- 不得把 `fetched_at` 或 `received_at` 回填成歷史 CBC 月資料的發布時間。
- CBC period 沒有具時區的官方發布 timestamp mapping 時，只保存 `awaiting_review` candidate，`available_at = null`，不得寫入 eligible M1B。
- 所有 as-of 查詢同時限制 `available_at <= cutoff` 與 `ingested_at <= cutoff`。

## Immutable revision 與 failure semantics

- 相同 provider、resource、logical key 與 normalized payload hash 永久 idempotent。
- 修正資料必須以新 revision 明確 `supersedes_revision_id`，不得更新舊列。
- TWSE／TPEx 各自建立 run item；單邊失敗保存 `partial`，不以舊完整值遮蔽。
- provider timeout、HTTP error、schema drift、awaiting review 與 concurrent lock 都是可查詢狀態。
- retry 建立新 run 並保存 `retry_of_run_id`；成功重試不得複製既有 accepted revision。

## Official trading calendar

日曆只接受官方資料列明示的 session meaning，不使用 weekday、OHLCV 或第三方行情推測。未出現在已保存官方資料中的日期保持 unknown。日曆 coverage 無法證明 cutoff 當日狀態時，daily freshness 必須回傳 `unknown`，不得沿用舊 success 宣稱 fresh。

## Read APIs

```http
GET /api/v2/data-freshness
GET /api/v2/providers/status
GET /api/v2/analysis/snapshots/{snapshot_id}/dependency-status
```

這些 GET 只讀 stored state，不呼叫 provider、不建立 snapshot、不重算或覆寫歷史輸出。可用 `provider`／`resource` 與 timestamp cutoff 做最小篩選。

Snapshot freshness 狀態：

- `current`：snapshot 仍使用 cutoff 前最新 eligible dependency。
- `stale`：已有較新 eligible dependency；snapshot 仍是有效歷史紀錄，不會自動重算。
- `unknown`：依賴或官方 coverage 不足，fail closed。
- `blocked`：例如 snapshot 使用的 approval 已 revoked 或 dependency 遺失。

未核准的新候選只標示 `candidate_awaiting_review`，不會讓舊 snapshot 自動 stale。`checked_at` 預設採 comparison cutoff，確保固定 snapshot、DB state 與 cutoff 的結果可重現。

## CLI 與排程

```text
python tools/ingest_production_data.py official-daily --trade-date 2026-08-11
python tools/ingest_production_data.py calendar
python tools/ingest_production_data.py cbc --publication-map path/to/release-times.json
```

重試使用原 run ID，例如：

```text
python tools/ingest_production_data.py official-daily --trade-date 2026-08-11 --retry-of run_xxx
```

本機部署可由 Windows Task Scheduler 呼叫上述 deterministic CLI；scheduler 不屬於資料正確性的一部分。不得把 legacy `src/scheduler.py` 靜默改成 Evidence V2 ingestion runner。

## Backup / restore

先停止寫入程序，再執行：

```text
python tools/evidence_db_recovery.py backup data/cache.db backups/evidence-YYYYMMDD.db
python tools/evidence_db_recovery.py validate backups/evidence-YYYYMMDD.db
python tools/evidence_db_recovery.py restore backups/evidence-YYYYMMDD.db restored/cache.db
```

目的地必須不存在。工具使用 SQLite backup API，並驗證 `PRAGMA integrity_check`、不可替代 Evidence tables 與 Phase 10 operational provenance counts。還原後應先指向隔離路徑執行 validation，再由人工決定是否切換；工具不自動覆寫 production database。

## 產品邊界與限制

- 無 current／real-time／delayed quote、tick 或 intraday feed。
- 無 browser write、admin dashboard、auto approval、auto snapshot refresh。
- 無外部 Forward EPS collector；CBC 也不推造歷史發布時間。
- 無券商 API、帳戶連線、真實委託、自動交易或跟單。
- 無 Phase 11 功能；Phase 8 frozen outcomes 與 Phase 9 API contract 保持不變。
