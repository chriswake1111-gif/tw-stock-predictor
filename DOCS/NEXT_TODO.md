# 專案進度與下階段待辦 (NEXT_TODO.md)

## 2026-08-01 研究模式產品邊界與分析核心改進

- [x] 正式建立 `DOCS/PRODUCT_BOUNDARY.md`：僅允許市場研究、歷史回測與虛擬模擬；禁止券商 SDK、真實委託、跟單及客戶資金管理。
- [x] 新增 `src/analysis_service.py`，統一 FastAPI、Streamlit 與每日排程器的分析契約。
- [x] 移除固定波浪點、固定成交量、股價推造 EPS/EVA 及前端假預設值；資料不足改回傳 `insufficient_data` / `not_applicable`。
- [x] 修復即時波浪 `as_of_date` 未完整傳遞到費氏時間窗的未來資料問題。
- [x] TTM EPS 改為驗證連續四季，並加入 150 日快取新鮮度檢查。
- [x] 負 EPS 不再產生負的 PE 目標價。
- [x] FinMind 殖利率加入 `percent` / `ratio` 明確單位 migration，內部統一使用比例。
- [x] CBC M1B 依 `available_at` 執行 as-of 查詢，不再假設期末日即可取得資料。
- [x] 均線扣抵欄位不完整時採 fail-closed，不產生共振訊號。
- [x] Backtrader 進場訊號納入 8/13/21/55/144 日扣抵斜率，結果明示 `historical_backtest_only` 與 `simulated_orders_only`。
- [x] FastAPI 靜態前端與 Streamlit 均顯示研究模式及非投資建議聲明。
- [x] 建立離線優先的固定快照與 walk-forward CLI：`tools/run_backtest.py`。
- [x] 同窗比較 TU 策略、買進持有及 SMA 8/21，納入手續費、證交稅、滑價、整張交易與訊號次日開盤執行。
- [x] 報告輸出 `report.json`、彙總／視窗／交易／品質 CSV、資料快照及人類可讀摘要；檔名包含資料與設定指紋。
- [x] 產出基準報告：`reports/backtest/baseline-v2-20260801-5cb3f28be410`（模擬資金 1,000 萬元，未進行參數最佳化）。
- [x] 驗證：`52 passed`；僅保留既有 FastAPI TestClient 第三方棄用警告。
- [x] 擴充 2014-01-02 至 2026-07-31 的長期調整價快照，三個標的各約 3,060 筆。
- [x] 新增 `DOCS/RESEARCH_DATA_CONTRACT.md`，保存來源、套件版本、抓取參數、雙 SHA-256 與供應商資料可變性契約。
- [x] Walk-forward 擴充為 12 個年度驗證窗，並依同窗 `^TWII` 報酬分為 9 個牛市、1 個熊市、2 個盤整窗。
- [x] 完成五組訓練窗參數敏感度；排名不套用驗證窗，避免資訊洩漏。
- [x] 長期報告：`reports/backtest/long-history-v1-20260801-eee3948d6fa6`。
- [x] 新增固定 snapshot 重播與執行前 SHA-256 驗證，避免供應商回溯修改污染比較。
- [x] 新增平均資金使用率，區分「有持倉天數」與「實際投入資金比例」。
- [x] 保留 legacy 訊號，完成訓練窗配置選型 `adaptive v1.1 balanced cap`。
- [x] adaptive v1.1 長期報告：`reports/backtest/adaptive-v1-1-20260801-193192967aed`。
- [x] aggressive adaptive v1 因 2330 最差 MDD 達 18.17%，僅保留 `reports/backtest/adaptive-v1-20260801-8353b098396a` 作失敗實驗證據。
- [x] adaptive v1.1 三標的報酬均高於 legacy，最差 MDD 分別為 4.82%、6.43%、11.63%；僅列為擴大驗證候選，未升格預設。
- [x] 以 `config/expanded_validation_universe.yaml` 預註冊 Phase A 的 14 檔 TWSE 分層樣本，固定選樣、日期與整體門檻後才執行回測。
- [x] 初步擴大驗證報告：`reports/backtest/expanded-phase-a-20260801-1c3d9e6d79ab`；未加入官方交易日完整性 gate 時，14 檔皆完成計算、僅 0056 與 006208 通過策略 gate。此報告只保留為前後比較，不作升格證據。
- [x] 報告新增 `universe_gate.csv` 與類別覆蓋摘要；不可事後移除失敗／缺資料標的，且 `promotion_to_default` 固定為 `false`。
- [x] 新增 TWSE 月成交唯讀稽核、零量列移除、參考交易日缺口與逐筆官方補值契約；1301 2025-08 與 2317 2018-10 已和官方資料完全對齊。
- [x] Audited v2 報告：`reports/backtest/expanded-phase-a-audited-v2-20260801-b8e7d89c9854`；14 檔均有尚未完整解釋的長期交易日／零量差異，品質合格可用數為 0，正式結論為 `hold_for_revision`。
- [x] 建立 Phase 1 TWSE 官方完整性層：以 `FMTQIK` 建立 3,068 個官方成交日，只針對缺口月份抓取 `STOCK_DAY`，並收錄 `TWT49U`／`TWTAUU` 公司行動；407 個請求具備限速、checkpoint 與同 scope 續跑。
- [x] 完成 14 檔官方對帳報告 `official_integrity_audit_v1.json`；辨識供應商漏掉的補行星期六交易、2317 停牌／未交易日及 006208 官方零量列。既有 `daily_ohlcv` 52,074 筆與 canonical SHA-256 均未改變。
- [x] 核准並實作 `provider_compatible_adjusted_v1`：同月份、同公司行動區段至少兩個 `1e-6` 唯一共識錨點才可將 TWSE raw OHLC 轉為 parent-compatible adjusted OHLC；volume 不調整。
- [x] 建立不可變 Phase 2 快照 `phase2-gap-adjusted-v1-20260801-ec781a02134f`：236 個官方缺口中重建 202 筆、34 筆 fail-closed；官方完整性 gate 由 0/14 提升至 12/14，原 audited-v2 未覆寫。
- [x] Evidence Model v2 Phase 0／1：建立 legacy golden snapshot、證據規則 registry 與 U 級規則 fail-closed。
- [x] Evidence Model v2 Phase 2：完成 Forward EPS／PE immutable revisions、transactional migration、`knowledge_cutoff_at` as-of 查詢與平行 v2 valuation API。
- [x] Forward EPS 多來源維持分離，不自動平均；verified matrix 只使用 approved symbol-scope PE，draft／revoked PE 不進入估值。
- [x] Evidence Model v2 Phase 2 review hardening：寫入預設關閉、管理 API key、draft-only import、immutable approval ledger、U 級 fail-closed、C 級 operationalization 與永久 idempotency ledger。
- [x] Phase 2 驗收：完整回歸結果與 v1 golden snapshot 證據更新於 `DOCS/evidence-model-v2/PHASE2_ACCEPTANCE_EVIDENCE.md`；未新增外部 Forward EPS Collector。
- [x] Phase 2 CI／approval follow-up：固定 FastAPI／Starlette／Pydantic／httpx2／pytest 相容組、draft/revoked EPS 狀態、VAL-02／VAL-04 精確核准對應與 backdated decision 防護。
- [x] Evidence Model v2 Phase 3：完成 CBC M1B、TWSE＋TPEx turnover 的 as-of 對齊、partial／revoked fail-closed 與 LIQ-01／LIQ-02 trace。
- [x] Evidence Model v2 Phase 4：完成 symbol-bound 人工錨點 immutable revision、rule-specific approval 與 FB-03／FB-04 情境參考。

### 尚未完成／不得誤解為已完成

- [x] CBC M1B 已有 Phase 10 官方 `EF15M01` 顯式 CLI 採集；bare timestamp 或缺 publication provenance evidence 的 period 僅保存候選，不自動成為 eligible M1B。
- [ ] EVA 因缺少完整 NOPAT、投入資本與流通股數資料契約，API 明確回傳 `unsupported`。
- [ ] 融資模組目前只計算觀察期融資餘額成長率；若要實作「融資相對大盤報酬率」，仍需確認正式業務公式。
- [ ] 長期資料、參數敏感度與行情分層已完成；目前只有一個熊市年度窗，熊市結論仍需更多歷史或其他標的驗證。
- [ ] yfinance 為非官方供應路徑，實測重新抓取時調整價末位小數可能回溯改變；重現既有結果須使用報告內固定快照。
- [ ] `^TWII` 無法直接交易，報告使用 ETF 稅率與單位的「指數代理成本模型」，不是可成交商品的精確重現。
- [ ] 回測尚未納入股利現金流、個股漲跌停、成交量容量與市場衝擊。
- [ ] adaptive v1.1 已完成 Phase A 計算，但官方完整性 gate 為 0/14 可用；不得視為跨標的驗證完成，legacy 仍是正式預設策略。
- [ ] Phase 2 已建立 parent-compatible 缺日 adjusted 流程，但仍不是全歷史官方調整價；ETF 分割／反分割、面額變更及股利現金流／總報酬語意仍需獨立的官方全量資料契約。
- [ ] `006208.TW` 有 32 筆官方成交股數／金額存在但 OHLC 為空，另有 1 筆錨點不足；`2308.TW` 有 1 筆因子共識平手。這 34 筆維持 `insufficient_data`，不得用成交均價或內插補值。
- [ ] 測試仍有 FastAPI TestClient 對 `httpx` 的第三方棄用警告，暫不影響功能。
- [ ] Evidence Model v2 尚無外部 Forward EPS Collector；目前只允許人工或已授權來源匯入。
- [x] Evidence Model v2 Phase 5 三等份 planner 已完成；Phase 7 scenario synthesis 與 immutable analysis snapshot 已合併至 `main`。Phase 4 仍不含自動錨點、自動數浪或費氏時間窗。

### 建議下一步

1. 以新固定 snapshot 原樣重跑 Phase A；14 檔預註冊樣本全部保留，006208 與 2308 必須作為品質不合格樣本計入 universe gate，不得事後排除。
2. 比較新 Phase A 與 audited-v2 的 dataset hash、品質、交易日、報酬與 MDD；不得因結果調整 adaptive profile 或因子門檻。
3. Phase A 維持至少 10 檔品質合格且原預註冊 gate 通過後，再預註冊 TPEX Phase B 與額外熊市樣本；通過前不得替換 legacy。
4. 若要建立全歷史官方調整價，須另行處理 ETF 分割／反分割、面額變更與股利現金流，不得將本次 provider-compatible 契約誤稱為官方總報酬。
5. 完成 CBC 官方資料採集及融資指標的業務公式確認後，再擴充總體／籌碼判定。

## 📌 當前開發進度 (Current Progress - Fourth Code Review 完成)
- [x] **第四次 Code Review 全面巡檢與極限邊界優化 (Fourth Code Review Complete)**
  - [x] **P1: 單季 EPS 負值/單季虧損邊界扣減演算法修復 ([src/collectors/finmind_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py))**
    - 修正當公司出現單季虧損或單季 EPS 為負值時，`q3` / `q4` 扣減累計值不會因遞減而誤觸退回全年累積 EPS 的 Edge Case。
    - 補齊單單元測試 `test_finmind_normalize_quarterly_eps_with_negative_quarter` ([tests/test_collectors.py](file:///d:/Tools/tw-stock-predictor/tests/test_collectors.py))，全套 26 個單元測試 100% 通過。
  - [x] **第三次 Code Review 全面修復 (Plan 3 Complete)**
  - [x] **P0: 刪除 CBC M1B 所有假資料與預設值 ([src/collectors/cbc_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/cbc_collector.py))**
    - 徹底移除 `default_m1b_billion` 與 `270000.0` 預設值，改為回傳 Contract（含 `value`, `period`, `available_at`）。無資料時輸出 `status: "insufficient_data"`。
  - [x] **P0: 實作財報單季 EPS 標準化演算法 `normalize_quarterly_eps` ([src/collectors/finmind_collector.py](file:///d:/Tools/tw-stock-predictor/src/collectors/finmind_collector.py))**
    - 正確將累計財報轉算為單季 EPS（$Q1, Q2_{cum}-Q1, Q3_{cum}-Q2_{cum}, Annual-Q3_{cum}$），保留最新更正版，須集齊連續 4 季單季紀錄方可算 TTM EPS，否則回傳 `status: "insufficient_data"`。
  - [x] **P1: 真正加入 SMA5 Volume 修正 Stage 3 ([src/strategy/backtester.py](file:///d:/Tools/tw-stock-predictor/src/strategy/backtester.py))**
    - 新增 5 日均量 `self.sma5_vol`，Stage 3 量能判斷明確改為 `self.sma5_vol[0] > self.sma20_vol[0]`。
  - [x] **P1: API 波浪目標價結構分離與頂層模糊欄位移除 ([src/api/main.py](file:///d:/Tools/tw-stock-predictor/src/api/main.py))**
    - 拆分為 `wave_analysis.realtime_confirmed` (包含其獨立 `targets` 與 `time_window`) 與 `wave_analysis.hindsight_visualization` (包含 Warning)，移除頂層未標示 `wave_targets`。
  - [x] **P1: Realtime Pivot 順序與交替 Sequence 驗證 ([src/engine/wave_fibonacci.py](file:///d:/Tools/tw-stock-predictor/src/engine/wave_fibonacci.py))**
    - 要求 $index(P0) < index(P1)$ 且 $price(P0) < price(P1)$。$P2$ 投影值顯式標註 `"type": "derived_projection"` 與 `"formula"`。刪除全區間 `12629.0`, `15475.0` 預設值。
  - [x] **P1: 官方 JSON 數據解析端點單元測試與 Fixtures ([tests/fixtures/](file:///d:/Tools/tw-stock-predictor/tests/fixtures/))**
    - 新增 `twse_fmtqik_response.json` 與 `tpex_st41_response.json` 官方格式 Mock Fixtures，驗證 `MarketTurnoverCollector` 正確解析。
  - [x] **P2: GitHub Actions CI Workflow ([.github/workflows/ci.yml](file:///d:/Tools/tw-stock-predictor/.github/workflows/ci.yml))**
    - 新增 GitHub Actions CI 設定檔，每次 push/PR 自動執行 pytest 驗證。

- [x] **GitHub 遠端公開倉庫**
  - GitHub Repository URL: [https://github.com/chriswake1111-gif/tw-stock-predictor](https://github.com/chriswake1111-gif/tw-stock-predictor)

## Evidence Model V2 Phase 5 status (2026-08-09)

- [x] ENT-02 three-tranche planner conserves the exact Decimal budget and allows at most three entries.
- [x] Trigger revisions and append-only approval events are knowledge-cutoff safe.
- [x] Signal-driven backtester consumes external events only and uses next-bar simulation.
- [x] Legacy 20/30/50, 7%-11%, MA resonance, SMA55, and breakout logic remain v1-only.
- [x] Deployment API is protected, planning-only, and optional to general analysis data quality.
- [x] Phase 6 implemented on its dedicated branch: normalized point-in-time valuation revisions,
  percentile-based SEL-01 screening, immutable profile approval, exact approved Forward EPS growth,
  replaceable technical confirmation, and additive v2 API integration.
- [ ] Phase 6 has no production historical valuation importer, point-in-time industry peer contract,
  or concrete WV-03 provider; these paths return explicit `insufficient_data`.
- [x] Phase 7 merged to `main`: TGT-01 approved synthesis profiles, deterministic family/dependency
      overlap, protected refresh, and DB-enforced immutable snapshots.
- [x] Phase 8 constrained MVP implemented on its authorized branch: immutable Phase 7 snapshot input,
      approved evaluation profile, hash-verified fixed Phase 2 outcome manifest, deterministic 20/60
      session evaluator, immutable stored results, protected run API, and origin-separated descriptive
      historical summary.
- [x] Phase 8 merged to `main` at `939573b`; immutable scenario evaluation and origin-separated descriptive summaries are closed with main CI evidence.
- [x] Phase 9 已合併至 `main`：read-only v2 discovery contracts、status presentation hardening 與 Evidence Workspace MVP 已完成。
- [x] Phase 10 已在 `feature/evidence-model-v2-phase10-data-foundation` 完成五個可獨立審查的 implementation commits：provider/resource contracts、additive migration、official ingestion、freshness reads、recovery 與文件。
- [x] Phase 10 第一輪 Code Review remediation 已修正 lease lock recovery、CBC publication evidence、fail-closed freshness 與 effective revision collapse。
- [x] Phase 10 第二輪 Code Review remediation 已讓 accepted／revoked／corrected accepted publication evidence lifecycle 於 M1B as-of read boundary fail closed；同一 Draft PR 等待第三輪 Review。
- [ ] CBC 歷史月份仍需具 source reference、evidence SHA-256、verification metadata 與 actor 的官方 publication evidence；bare timestamp 只保存 `awaiting_review` candidate。
- [ ] Phase 10 不含 current quote、admin frontend、browser writes、automatic approval、broker integration 或真實交易。

## Evidence Model V2 Phase 11 status (2026-08-12)

- [x] Deterministic `analysis_snapshot_v1` comparison contract and semantic whitelist implemented.
- [x] Stored snapshot facts and cutoff-bound current dependency context are separated.
- [x] Shared SQLite read transaction and connection-aware repository reads implemented.
- [x] GET-only v2 comparison API and read-only Evidence Workspace page implemented.
- [x] Focused backend, frontend unit, responsive Playwright, and governance coverage added.
- [x] Phase 11 Draft PR review and main CI closure completed before Phase 12 started.

## Evidence Model V2 Phase 12 status (2026-08-13)

- [x] Research watchlist membership, archive/restore, and append-only exact snapshot acknowledgment implemented on the dedicated Phase 12 branch.
- [x] Actual-latest no-fallback selection, one shared cutoff/read transaction, and Phase 11 query-time comparison integrated.
- [x] Local/loopback exact-Origin/Host boundary, writes-disabled default, 1,800-second CSRF, strict DTO/body limits, and browser no-admin-secret guard implemented.
- [x] Backup/restore validation and `/research` desktop/mobile workspace added.
- [x] Phase 12 research review queue is merged to `main`; its read-only workflow and governance remain regression boundaries.
- [x] Phase 13 Universe Foundation is implemented on its dedicated Draft PR branch. Its as-of identity, source-governance, public-order pagination and bounded effective-state selection remain subject to the active review/merge gates; Phase 14 stays blocked until those gates close.

## Evidence Model v2 Phase 13 — Universe Foundation

- Universe identity is an immutable venue/code/identity-epoch context.  Same-code reuse requires explicit termination and no continuity evidence before allocating a new epoch; aliases are append-only and never inferred from name similarity.
- `universe_symbol_mapping_v1` is a project canonical convention (`.TW` / `.TWO`) and never changes v1 `normalize_symbol`. Unknown security type is retained as `unknown`, not coerced to ordinary share.
- The exact status policy is `universe_status_matrix_v1`: no safe reference is `insufficient_data`; actionable governance reasons are `needs_human_input`; non-actionable stale/unknown/provider failures are `partial`; only proven current complete data is `available`.
- Universe reads are historical/as-of and current-operational channels.  A latest blocking revision never falls back to an older revision as current.  Public DTOs contain no prices, quotes, turnover, ranking, recommendation, Rule Trace, raw payload, secrets, locks, idempotency key, or fingerprint.
- Phase 13 resources are limited to approved TWSE `t187ap03_L`/newlisting/termination and TPEx master/delisted/operational roles. TPEx `company.html` / `company/otcSearch` remains manual corroborating evidence until its machine contract is independently verified; the compatibility resource row is not a new-ingestion collector/parser contract. `tpex_mainboard_quotes` is documentation-only and excluded from registry, collectors, raw revisions, hashes, fixtures, API and frontend.
- Runtime ingestion writes use `UNIVERSE_INGESTION_WRITES_ENABLED=false` by default and a centralized actor/run/lock/audit guard. Migration seed is the only normal registry seed; read constructors and GET routes are side-effect-free.
- `universe_ingestion_idempotency` permanently binds every key to a payload fingerprint and revision.  Backup/restore includes Universe anchors, policies, revisions, events and idempotency bindings.
- Venue health selects the latest cutoff-visible state per `(resource_id, logical_revision_key)` before applying deterministic status precedence; same-feed corrections clear only that feed, while optional corroborating/manual sources stay neutral and zero-row observations remain visible.
- Registered freshness policy is authoritative: seeded TWSE/TPEx masters remain `unknown_without_official_cadence`, `freshness=unknown`, and `current_complete=false` even when an ingestion payload claims an official cadence or licensed reference.
- Canonical `.TW`/`.TWO` mapping is generated only in approved master scope. Corroborating observations can preserve an existing master mapping but cannot create one independently; otherwise the result remains `canonical_mapping_unverified`.
- Historical revocation targets `instrument_revision_id` (not the parent `universe_revision_id`) and removes the old identity from exact/canonical/resolve/list historical references until a corrected accepted revision is cutoff-visible. Master mapping carry-forward is likewise cutoff, publication-evidence and revocation safe.
- Instrument supersession is derived only from the exact parent `universe_revision_id` in the same instrument/source/logical chain; interleaved sources never share a global instrument predecessor. The effective historical evaluator removes both accepted-correction ancestors and revoked targets transitively, and list pagination applies filters, canonical ordering and cursors only after that effective reference is established.
- Phase 13 composes a cutoff-visible identity epoch per venue/code and applies lifecycle/operational events to exact, resolve and list state without date-only intraday inference. The actionable allowlist remains the authoritative five reasons and event governance fails closed. List/search prefilters possible matches without recursion, traverses null and mapped canonical lanes in the advertised public order, and limits recursive epoch/revision/event work to bounded candidate groups.

## Evidence Model v2 Phase 14 — Official EOD Close Context

- [x] LUK-29 Phase 14 Implementation, First Code Review remediation, and Second Code Review remediation are present on its dedicated Draft PR branch: exact TWSE/TPEx official EOD collectors, TWSE ISIN product classification, append-only provenance/lineage, source-observed/public-eligibility separation, durable command reservation, atomic operator evidence writes, current and cutoff-bound read-only context, fail-closed status matrix, GET-only API, neutral frontend surface, semantic backup/restore evidence, and PIT/visibility regression coverage.
- [x] Phase 14 implementation explicitly excludes `tpex_mainboard_quotes`, calendar/session inference, yfinance adjusted prices, analysis/signal/ranking/valuation integration, broker connectivity, real orders, and automatic trading.
- [ ] Phase 14 Draft PR Third Code Review and exact-head CI closure remain pending; merge and deployment are not authorized. Phase 15 has not started.

## Evidence Model V2 Phase 3 status (2026-08-01)

M1B／市場成交額 additive contracts、as-of repository、流動性 engine／service 與 v2 API section 已進入本階段驗收。CBC 歷史月份若沒有經官方發布日曆或新聞稿核對的 `available_at` mapping，必須保持 `needs_human_input`；不得用抓取時間回填。Phase 4 尚未開始。
