# API_OUTPUT_SPEC.md

## Phase 3 liquidity section

`GET /api/v2/analysis/{symbol}` additively returns a symbol-invariant `liquidity` section with its own status, market components in TWD, M1B period／availability, percentage ratio, 20／60-day observations, 5／10-year deterministic percentiles, alert, reference-case limitation, Rule Trace and data quality. Section failure does not turn another section into HTTP 500. Only LIQ-01 and LIQ-02 may appear; no sell or market-top boolean is emitted.

## 1. 新增平行 API

```text
GET  /api/v2/analysis/{symbol}
POST /api/v2/analysis/{symbol}/refresh
GET  /api/v2/analysis/snapshots/{snapshot_id}
POST /api/v2/anchors
GET  /api/v2/anchors/{symbol}
POST /api/v2/forward-eps
POST /api/v2/pe-scenarios
GET  /api/v2/model-rules
POST /api/v2/deployment-plan
```

舊 `/api/analysis/{symbol}` 暫時保留，標示 `legacy=true`。

---

## 2. v2 Analysis Response

```json
{
  "status": "partial",
  "symbol": "2330.TW",
  "as_of_date": "2026-08-01",
  "model": {
    "name": "Du-public-methods evidence-based assistant",
    "version": "2.0.0",
    "official_affiliation": false
  },
  "data_quality": {
    "available_sections": [],
    "missing_sections": [],
    "stale_sections": [],
    "needs_human_input": []
  },
  "market_context": {},
  "valuation": {
    "status": "available",
    "forward_eps": [],
    "pe_scenarios": [],
    "target_matrix": [],
    "historical_ttm_reference": {},
    "invalidation_conditions": []
  },
  "liquidity": {
    "status": "available",
    "turnover_twd": {},
    "m1b_twd": {},
    "turnover_m1b_ratio_pct": 0.0,
    "historical_percentile": null,
    "alert_level": "normal",
    "reference_case": {
      "range_pct": [3.3, 3.4],
      "meaning": "A publicly cited extreme-heat case, not a universal sell rule"
    }
  },
  "technical_support": {
    "year_line": {},
    "generic_ma_tools": {
      "status": "experimental",
      "du_method_verified": false
    }
  },
  "wave_scenarios": {
    "status": "needs_human_input",
    "pivot_candidates": [],
    "approved_anchor_set": null,
    "realtime_only": true,
    "hindsight_visualization_separate": true
  },
  "fibonacci_scenarios": {
    "retracement_0382": null,
    "equal_move": null,
    "time_windows": []
  },
  "target_confluence": {
    "overlap_ranges": [],
    "independent_method_count": 0,
    "evidence_strength": "low",
    "shared_dependencies": []
  },
  "deployment_plan": null,
  "invalidation": [],
  "rules_used": [],
  "unsupported": [
    "eva_formula",
    "margin_return_8_percent_formula"
  ],
  "snapshot_id": "..."
}
```

---

## 3. Rule Trace

每一段輸出均須附：

```json
{
  "rule_id": "VAL-01",
  "evidence_level": "A",
  "implementation_mode": "verified_core",
  "project_operationalization": false,
  "source_data_as_of": "2026-08-01"
}
```

---

## 4. 語言限制

允許：

- 推算區間。
- 觀察區。
- 成立條件。
- 失效條件。
- 證據強度。
- 資料不足。

禁止：

- 必然上漲。
- 精準命中。
- 保證獲利。
- 官方杜金龍模型。
- 100% production-ready（除非具完整證據，且仍不建議使用）。

---

## 5. Partial Result

某一資料來源失敗時，不得讓整份分析失敗。回傳：

```json
{
  "status": "partial",
  "reason": "forward_eps_missing",
  "available_sections": ["price", "liquidity"],
  "missing_sections": ["forward_valuation"]
}
```

---

## Phase 4 technical support section

受保護的寫入端點：

```text
POST /api/v2/anchors
POST /api/v2/anchors/{anchor_revision_id}/approval
GET  /api/v2/anchors/{symbol}
```

寫入預設關閉，沿用 `EVIDENCE_V2_WRITES_ENABLED`、管理 API key 與 server-controlled actor。`GET /api/v2/analysis/{symbol}` 的 `technical_support` 獨立回傳已核准 FB-03／FB-04 scenario。每個 scenario 包含公式、方向、TWD per share 計算層級、明確角色 anchors、anchor revision ID、approval ID 及完整 Rule Trace。

`formula` 是穩定的 machine-readable identifier：FB-03 為 `equal_move`，FB-04 為 `retracement_0382`。`formula_expression` 分別保存 `C + (B - A)` 與 `B - 0.382 * (B - A)`，兩者不得混用。

沒有 anchor 或只有 draft 時回 `needs_human_input`；technical section 失敗不使 valuation 或 liquidity 回傳 HTTP 500。`wave_scenarios`、費氏時間窗與自動波浪仍未實作。所有 scenario 都明示僅為參考、不是預測、保證目標或交易指令。

Top-level `data_quality.needs_human_input` 對應如下：`manual_anchor_required` → `manual_anchor`；`approved_manual_anchor_required` 或 `anchor_approval_revoked` → `approved_manual_anchor`。有有效 scenario 時不得保留這兩個缺口。
