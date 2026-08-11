# Evidence Model V2 Phase 9 — Read-Only Evidence Workspace

Phase 9 將 Phase 1–8 的後端權威輸出呈現為 React + TypeScript + Vite 研究工作區。它不建立新的金融模型語意，也不使用 legacy v1 金融 DTO。

## 產品邊界

- 瀏覽器只發出同源 GET；不包含 admin key、approval、refresh 或 evaluation-run 寫入。
- 不整合 current price，不計算 PE × EPS、Fibonacci、percentile、confluence 或歷史績效。
- 不提供排行、機率、建議、PWA、券商或真實委託。
- FastAPI 優先提供 `frontend/dist`，若尚未 build 才保留 legacy static UI fallback。

## API dependency matrix

| Feature | Backend Source | Status |
| --- | --- | --- |
| Stock Analysis | `/api/v2/analysis/{symbol}` | supported |
| Market Overview | `/api/v2/market-overview` | supported |
| Snapshot History | `/api/v2/analysis/snapshots` | supported |
| Snapshot Detail | `/api/v2/analysis/snapshots/{snapshot_id}` | supported |
| Validation Runs | `/api/v2/evaluations/runs` | supported |
| Validation Detail | exact run + `/api/v2/performance/summary` | supported |
| Rule Library | `/api/v2/model-rules` | supported |

## Authority flow

```text
Backend immutable/stateful evidence contracts
        ↓
typed v2 GET-only API client
        ↓
TanStack Query cache
        ↓
presentation components
        ↓
user inspection
```

Server owns status, valuation, target confluence, evidence grade, method confluence, screening, liquidity, snapshot state, historical validation and approval state. React only formats and presents them.
