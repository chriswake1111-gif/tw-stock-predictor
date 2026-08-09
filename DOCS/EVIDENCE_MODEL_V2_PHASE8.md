# Evidence Model V2 Phase 8

Phase 8 是「歷史情境表現觀察」MVP，不是預測機率、模型排名或參數最佳化。

## 固定流程

```text
Phase 7 immutable snapshot
  + approved EvaluationProfile revision
  + hash-verified fixed Phase 2 outcome manifest
  -> deterministic Phase 8 evaluator
  -> immutable ScenarioEvaluation rows
  -> coverage and historical descriptive summary
```

Evaluator 只讀 snapshot 內已保存的 `target_confluence.supporting_methods` 與
`overlap_ranges`。它不查詢或重跑 Forward EPS、PE、anchor、screening、liquidity、
approval、Rule Registry 或 target synthesis。GET API 只取回已保存結果。

## MVP 邊界

- Universe：Phase 2 固定 14 檔研究 cohort；不是全台股驗證。
- Outcome：`phase2-gap-adjusted-v1-20260801-ec781a02134f`，應用層核准 dataset hash
  `0c2754abae952b145e22bb2b36f80fec8aa00122cc1d9e9acb93092dc4cb4745`。
- Benchmark：同一 manifest 的 TAIEX price return；不是 total return。
- Horizons：20、60 個 frozen calendar sessions，start session 為 index 0，end 為 index H。
- Timing：knowledge cutoff 所在的 Asia/Taipei 日期之後，第一個有可用 adjusted bar 的 session。
- Target：closed-boundary intraday touch。起始價已在區間內為 `already_in_range`，不算 future hit。
- FB-04：只保存 support touched／held／breached context，不進 target reach rate。
- INV-01：本階段固定 `not_applicable`，不得從 Phase 7 文字推導新公式。

006208.TW 與 2308.TW 保留在 coverage，但 `quality_warning` 不進 target rate 分母。
`pending`、`insufficient_future_data` 與 `already_in_range` 也不視為 miss。

## API

- `POST /api/v2/evaluations/runs`：預設關閉，需 `EVIDENCE_V2_WRITES_ENABLED=true`、
  `X-Admin-API-Key`、server-side actor、`Idempotency-Key`，並明示
  `evaluation_profile_acknowledgement=phase8_mvp_v1`。
- `GET /api/v2/evaluations/runs/{run_id}`
- `GET /api/v2/evaluations/runs/{run_id}/results`
- `GET /api/v2/analysis/snapshots/{snapshot_id}/evaluations`
- `GET /api/v2/performance/summary?evaluation_run_id=...`

`live_refresh` 映射為 `prospective_snapshot`；`historical_reconstruction` 保持同名。
兩者在 storage、fingerprint 與 summary 中分開，不提供合併的預設成功率。

## 限制

- 14 檔為人工限制研究 cohort，可能有 selection／survivorship bias。
- Historical reconstruction 不代表該分析當時曾實際發布。
- Historical observed frequency 不等於未來 probability。
- Evidence strength 是獨立方法成分描述，不是預測信心機率。
- 本階段不升降 A/B/C/U，TGT-01 保持 C 級 project operationalization。
- 不使用 `daily_ohlcv`，manifest 缺失、異常或 hash mismatch 一律 fail closed。
