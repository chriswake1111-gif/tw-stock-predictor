# MODEL_SPEC.md

## 1. 產品定位

v2 定位為：

> 杜金龍公開方法的證據化決策輔助系統。

輸出用途：

- 整理大盤與個股資料。
- 產生估值、流動性、趨勢與費氏情境。
- 顯示支持證據、矛盾訊號與失效條件。
- 保存分析版本以供後續驗證。

禁止：

- 保證漲跌。
- 自動送出真實委託。
- 將人工判斷偽裝為唯一演算法。
- 用後見高低點計算即時命中率。

---

## 2. 系統分層

```text
Collectors / Imported Research Data
        ↓
Normalized Data Contracts
        ↓
Deterministic Formula Layer
        ↓
Parameterized Support Layer
        ↓
Human Annotation Layer
        ↓
Scenario Synthesis
        ↓
Immutable Analysis Snapshot
        ↓
API / PWA
```

### 2.1 Deterministic Formula Layer

只包含：

- Forward EPS × PE。
- 上市櫃成交額／M1B。
- 三等份資金計算。
- 已核准錨點的 0.382 回檔。
- 已核准錨點的等幅推算。
- 費氏月份計數。
- 年線及斜率的通用計算。

### 2.2 Human Annotation Layer

必須人工確認：

- 波浪起點。
- 波浪浪級。
- 主升、修正、末升等市場階段。
- 倍率選擇。
- 個股合理 PE。
- 產業是否仍屬主流。
- 分批買點間距。
- 失效條件。

---

## 3. Forward EPS × PE 模型

### 3.1 Input

```python
class ForwardEPSObservation:
    symbol: str
    fiscal_year: int
    eps_low: float | None
    eps_base: float
    eps_high: float | None
    source_name: str
    source_type: Literal["broker_report", "company_guidance", "consensus_api", "manual"]
    published_at: date
    available_at: datetime
    analyst_count: int | None
    quality_note: str | None
```

### 3.2 禁止替代

- 歷史 TTM EPS 不得自動改名為 `forward_eps`。
- 不得用固定 10% 成長率產生杜氏 Forward EPS。
- 若沒有 Forward EPS，回傳 `insufficient_data`。
- 可另外顯示 `historical_ttm_reference`，但不得混入 Forward PE 目標。

### 3.3 PE Scenario

```python
class PEScenario:
    scenario_id: str
    symbol_scope: str
    pe_value: float
    label: Literal["conservative", "base", "optimistic", "custom"]
    evidence_level: Literal["A", "B", "C", "U"]
    rationale: str
    approved_by: str | None
    approved_at: datetime | None
```

### 3.4 Formula

```text
target_price = forward_eps × selected_pe
```

輸出每一年度、每一 EPS 情境與每一 PE 情境的矩陣，不只輸出單一價格。

### 3.5 Invalidation

- Forward EPS 下修。
- PE regime 改變。
- 產業成長假設失效。
- 利率／風險溢酬顯著改變。
- 原始資料過期。

---

## 4. M1B 流動性模型

### 4.1 Formula

```text
turnover_m1b_ratio_pct
= (TWSE_turnover_TWD + TPEx_turnover_TWD)
  / M1B_TWD
  × 100
```

### 4.2 時間對齊

對每個 `as_of_date`：

```text
只可使用 available_at <= as_of_date 的最新 M1B 資料
```

不得使用該月月底數據回填月初分析。

### 4.3 Output

- 當日比率。
- 20 日均值。
- 60 日均值。
- 5 年與 10 年歷史百分位。
- 資料所屬月份與公布日。
- 3.3%～3.4% 僅作「2026 公開案例參考帶」。
- 不得直接產生賣出指令。

### 4.4 Alert

```text
normal
elevated
historically_high
reference_extreme_case
insufficient_data
```

門檻應由歷史百分位和案例參考共同形成，不得使用固定 2.5 兆元。

---

## 5. 三等份部署模型

### 5.1 定位

此模組是 `CapitalDeploymentPlanner`，不是自動交易策略。

### 5.2 Rules

```text
max_entries = 3
weights = [1/3, 1/3, 1/3]
fourth_entry = forbidden
```

價格間距由外部提供：

- 固定百分比。
- ATR 倍數。
- 人工設定價格。
- 核准的 0.382 回檔區。

### 5.3 Output

```json
{
  "planned_total_capital": 900000,
  "entries": [
    {"stage": 1, "weight": 0.333333, "budget": 300000, "trigger": "..."},
    {"stage": 2, "weight": 0.333333, "budget": 300000, "trigger": "..."},
    {"stage": 3, "weight": 0.333334, "budget": 300000, "trigger": "..."}
  ],
  "max_entries": 3,
  "automatic_order": false
}
```

### 5.4 Invalidation

- 原分析情境失效。
- EPS 或產業假設下修。
- 第三筆已完成後不得繼續攤平。
- 資金風險上限被觸發。

---

## 6. 二低一高篩選器

### 6.1 原始概念

- 低 PE。
- 低 P/B。
- 高現金殖利率。
- 技術面轉強。

研究未取得固定絕對門檻，因此 v2 以分位數進行「專案操作化」，證據層級為 C。

### 6.2 v2 Operationalization

預設值僅為可配置研究參數：

```text
PE：產業或自身五年分位 ≤ 30%
P/B：產業或自身五年分位 ≤ 30%
殖利率：產業或自身五年分位 ≥ 70%
Forward EPS growth：不得為負
Technical turn：由獨立實驗模組提供，不可硬綁固定均線共振
```

輸出各分項，不只輸出 passed/failed。

---

## 7. 年線確認

### 7.1 定位

`Close > SMA_year` 是工程操作化，不宣稱為本人完整公式。

### 7.2 Parameters

```yaml
year_line:
  period: 240
  confirmation_days: 3
  require_positive_slope: true
  slope_lookback: 5
```

### 7.3 Output

- above_year_line。
- consecutive_days。
- slope。
- confirmation_status。
- evidence_level B。
- 不單獨觸發買入。

---

## 8. 費氏時間窗

### 8.1 Anchor

必須引用人工核准的 `anchor_id`。

### 8.2 Windows

```text
13, 21, 34, 55, 89 個月
```

### 8.3 Tolerance

預設 ±1 個月，配置化。

### 8.4 行為

只輸出：

- approaching_window。
- in_window。
- passed_window。
- no_price_confirmation。

不得輸出「必然反轉」。

---

## 9. 0.382 回檔

### 9.1 Preconditions

- 有人工核准的完整上升波 A→B。
- `B.price > A.price`。
- A、B 在 `as_of_date` 前已確認。
- 錨點版本不可被覆寫。

### 9.2 Formula

```text
retracement_0382 = B - 0.382 × (B - A)
```

### 9.3 Output

- 精確價。
- 可配置觀察帶。
- 與年線、估值或前高是否重疊。
- 失效條件由人工或情境配置提供。

不得自動下單。

---

## 10. 等幅推算

### 10.1 Anchors

人工核准 A、B、C：

- `amplitude = B - A`
- `target = C + amplitude`

### 10.2 Constraints

- 所有錨點必須有時間順序。
- 計算結果標記 `formula=equal_move`.
- 錨點改變時建立新版本，不覆寫舊結果。

---

## 11. 波浪候選與人工核准

### 11.1 自動模組可以做

- 產生 no-repaint pivot candidates。
- 驗證高低點交替。
- 顯示候選浪型。
- 計算候選情境。

### 11.2 自動模組不可以做

- 宣稱唯一正確浪級。
- 自動選取重大循環低點並冠名為杜氏判浪。
- 重標後覆寫舊預測。
- 直接產生交易指令。

### 11.3 Manual Anchor Schema

```python
class ApprovedAnchorSet:
    anchor_set_id: str
    symbol: str
    timeframe: str
    as_of_date: date
    anchors: list[AnchorPoint]
    wave_label: str | None
    market_phase: str | None
    approved_by: str
    approved_at: datetime
    rationale: str
    supersedes_id: str | None
```

---

## 12. 目標區間整合

### 12.1 Candidate Methods

- Forward EPS × PE。
- Equal move。
- 0.382 support。
- 人工選定費氏延伸。
- 歷史前高或箱型。
- 其他方法只能以 experimental 顯示。

### 12.2 Independence

兩個方法如果依賴同一個 EPS 或同一組錨點，不得計為兩個獨立證據。

### 12.3 Confluence Output

```text
support_count
independent_method_count
overlap_range
supporting_methods
shared_dependencies
contradicting_methods
```

`confidence` 不得解釋為實際命中機率。使用名稱：

```text
evidence_strength = low / moderate / high
```

---

## 13. 分析快照與版本

每次分析均建立不可變快照：

```python
class AnalysisSnapshot:
    snapshot_id: str
    symbol: str
    as_of_date: date
    model_version: str
    rule_versions: dict[str, str]
    source_data_versions: dict[str, str]
    manual_approval_ids: list[str]
    output_json: dict
    created_at: datetime
    supersedes_snapshot_id: str | None
```

禁止更新舊快照內容。模型重標、EPS 更新或 PE 修改時建立新快照。

---

## 14. 禁止進入 v2 核心的現有功能

- 固定 20／30／50 策略。
- 固定 7%～11% Stage 2。
- 固定均線共振作為杜氏買進條件。
- 自動 EVA 底價。
- 預設 EPS 成長率產生 Forward EPS。
- 固定 PB < 1.5、殖利率 > 4% 冠名為本人固定門檻。
- 固定 M1B 比率或成交金額直接賣出。
- 全歷史極值 hindsight 結果進入即時綜合結論。
