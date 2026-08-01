# MODEL_EVIDENCE.md

## 1. 證據分級

| 等級 | 定義 | 核心使用方式 |
|---|---|---|
| A | 杜金龍本人或完整節目明確說出輸入、規則或可重現公式 | 可進入 verified core |
| B | 本人多次使用或明確說明方向，但缺部分公式、門檻或資料處理方法 | 只能參數化、人工核准或提示 |
| C | 由多個案例合理歸納，或為本專案工程化定義 | 必須標示「專案操作化」 |
| U | 僅二手整理、一般教學、來源矛盾或無法追溯 | 不得進入杜氏核心 |

## 2. v2 規則清單

| Rule ID | 規則 | 等級 | v2 狀態 | 是否需人工 |
|---|---|---:|---|---:|
| VAL-01 | 目標價 = Forward EPS × Selected PE | A | verified_core | PE 可能需要 |
| VAL-02 | 追蹤未來二至三年 EPS | A | verified_core | 資料來源審核需要 |
| VAL-03 | 20／21／25 倍等 PE 情境 | B | parameterized_support | 是 |
| VAL-04 | 不同公司使用不同 PE | B | parameterized_support | 是 |
| ENT-02 | 通常三次、每次約三分之一 | A | verified_core planner | 買點間距需要 |
| SEL-01 | 二低一高方向性選股條件 | A | verified concept / C operationalization | 門檻可配置 |
| FB-03 | 等幅推算 | A | verified formula / approved anchors | 是 |
| FB-04 | 已確認波段的 0.382 回檔 | A | verified formula / approved anchors | 是 |
| FB-01 | 21 個月初升段及轉折觀察 | A | observation alert only | 起點需要 |
| FB-02 | 13／21／34／55／89 時間窗 | B | parameterized alert | 起點需要 |
| WV-03 | 收復年線為多頭確認之一 | B | parameterized confirmation | 參數可配置 |
| WV-01／02 | 五升三降、重大低點起算 | B | human-in-the-loop | 是 |
| FB-05 | 1.5／1.618／2／2.382 倍率 | B | scenario generator | 倍率需人工選擇 |
| LIQ-01 | 上市櫃成交額 ÷ M1B | B；公式為 C 級重建 | verified data / contextual metric | 否 |
| LIQ-02 | 3.3%～3.4% 曾被視為極端偏熱 | A（當次案例） | reference alert only | 否 |
| LIQ-04 | 融資報酬率 8% | B、公式缺失 | manual observation only | 是 |
| TGT-01 | 多方法重疊形成目標區間 | C | project synthesis | 可覆寫 |
| INV-01 | 年線、浪起點、EPS、產業假設改變時失效 | C | required invalidation checklist | 是 |
| MA-01 | 8／13／21／55／144／233 為杜氏固定均線 | U | generic experimental only | 否 |
| MA-02 | 均線扣抵為杜氏固定公式 | U | generic technical utility | 否 |
| MA-03 | 固定均線共振條件 | U | legacy experimental | 否 |
| VAL-05 | EVA 底價公式 | 結果 B／公式 U | unsupported | 否 |
| LIQ-03 | 固定 2.5 兆頭部量 | U | forbidden | 否 |
| ENT-01 | 固定 7%～11% 回檔買點 | U | forbidden | 否 |
| ENT-03 | 固定 20／30／50 資金比例 | U | forbidden | 否 |
| ENT-05 | 爆量長黑後固定延後一至二日 | U | forbidden | 否 |
| SEL-02 | 破底翻固定公式 | B、公式缺失 | experimental C definition | 是 |
| SEL-03 | 低基期固定公式 | B、公式缺失 | research note / manual tag | 是 |

## 3. Rule Registry Schema

建立 `config/model_rules.yaml` 或資料表 `model_rule_registry`：

```yaml
rules:
  VAL-01:
    title: Forward EPS multiplied by selected PE
    evidence_level: A
    implementation_mode: verified_core
    human_approval_required: false
    source_refs:
      - research_report: "VAL-01"
    allowed_outputs:
      - valuation_scenario
      - target_range
    forbidden_uses:
      - guaranteed_price
      - automatic_order
    version: "2.0.0"

  MA-03:
    title: Moving-average resonance
    evidence_level: U
    implementation_mode: legacy_experimental
    human_approval_required: false
    allowed_outputs:
      - technical_experiment
    forbidden_uses:
      - core_score
      - du_method_claim
      - automatic_order
    version: "2.0.0"
```

## 4. 強制規則

- `evidence_level=U` 時，`implementation_mode` 不得為 `verified_core`。
- `human_approval_required=true` 且沒有有效 approval ID 時，結果必須為 `needs_human_input`。
- 規則版本更新不得覆寫舊版。
- 報告必須列出實際使用的 Rule ID。
- C 級規則必須顯示 `project_operationalization=true`。
