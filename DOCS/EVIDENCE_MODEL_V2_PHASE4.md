# Evidence Model V2 Phase 4 — Manual Anchor + Fibonacci Scenario

## Scope and rules

Phase 4 只實作 A 級 `verified_core` 的 FB-03 與 FB-04，且兩者均要求人工核准。它不包含自動找高低點、自動數浪、時間窗、FB-05 倍率、買賣訊號或交易功能。

Scenario 是可重現的價格幾何參考，不是預測、保證目標或買賣指令。

## Anchor definition and roles

Anchor set 必須由人員輸入，綁定單一 symbol、Rule ID 與 immutable revision：

- `origin`：A，原始位移起點。
- `swing_end`：B，原始位移終點。
- `projection_origin`：C，FB-03 的等幅投影起點。

唯一 core anchor type 是 `manual_price_anchor`；不宣稱已確認頭部、底部或波浪級別。價格單位為 `TWD_per_share`。

## Approval, revision and as-of semantics

Import 建立未核准 revision；寫入 API 預設關閉並要求管理 API key，`created_by`／`approved_by` 由伺服器環境指定。Approval 是綁定 `(anchor revision, Rule ID)` 的 immutable event，不是可覆寫 boolean。新 revision 必須重新核准。

Anchor 查詢要求 `available_at`、`ingested_at` 均不晚於 `knowledge_cutoff_at`。Approval 同時要求 `approved_at`、`ingested_at` 不晚於 cutoff。`market_date` 只代表價格日期，不能取代人工知悉時間。最新 revision 或最新 approval revoked 時先排名再解讀，不回退舊值。

## Deterministic formulas and direction

FB-03：

```text
amplitude = B - A
calculated_level = C + amplitude
```

輸出使用穩定識別碼 `formula=equal_move`，數學表示式另存於 `formula_expression=C + (B - A)`。

保留 amplitude 正負方向，不使用 `abs`。

FB-04 正式規格只允許完整上升波 `B>A`：

```text
calculated_level = B - 0.382 * (B - A)
```

輸出使用穩定識別碼 `formula=retracement_0382`，數學表示式另存於 `formula_expression=B - 0.382 * (B - A)`。

向下 A/B 關係不使用通用 TA 推導，直接 fail closed。

計算使用 Decimal 完整精度；僅在 API／engine output 邊界以 `ROUND_HALF_UP` 固定到小數四位。此 rounding 是專案工程 convention，不是杜金龍規則。

## API and status

`GET /api/v2/analysis/{symbol}` 的 `technical_support` 獨立輸出 scenario 與 Rule Trace。Rule Trace 包含 Rule ID/version、證據等級、implementation mode、cutoff、approval ID 與 anchor revision ID。

- 無 anchor 或缺核准：`needs_human_input`。
- Top-level `data_quality.needs_human_input` 分別使用 `manual_anchor` 或 `approved_manual_anchor`；approval revoked 亦要求新的 `approved_manual_anchor`。
- revoked：fail closed，不產生 scenario。
- 不支援 ratio/rule：validation error 或 `unsupported`，不計算 verified scenario。
- technical failure 不影響 valuation／liquidity section。

## Legacy separation and limitations

v1 `wave_fibonacci.py` 的 pivot、hindsight、多倍率與既有策略維持原狀，但不會流入 v2 verified core。Phase 4 尚無自動 anchor、波浪、時間窗、情境整合、immutable full-analysis snapshot 或失效條件工作流。

## Product boundary

本階段未新增券商 API、真實帳戶、委託、部位配置、自動交易、買賣訊號、保證價格或保證報酬。
