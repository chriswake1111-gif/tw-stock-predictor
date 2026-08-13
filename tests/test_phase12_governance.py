from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

AUTHORITATIVE_REQUIREMENTS = (
    "合法symbol可新增watchlist membership。",
    "`2330`與`2330.TW`解析為相同canonical identity。",
    "明確`.TWO`保留；系統不自行推測OTC。",
    "非法symbol fail closed。",
    "重複add不建立duplicate membership。",
    "archive保留membership與review history。",
    "unarchive復用原watchlist item。",
    "hard delete在application與database皆被拒絕。",
    "empty watchlist回`available`及`items=[]`。",
    "symbol沒有snapshot時回`review_state=no_snapshot`，且不自動refresh。",
    "沒有review event時回`baseline_not_set`及`comparison_has_deltas=null`。",
    "acknowledgment精確綁定指定snapshot ID。",
    "cross-symbol acknowledgment被拒絕。",
    "review event為append-only。",
    "新acknowledgment不覆寫舊event。",
    "`reviewed_at`由server產生。",
    "review comparison cutoff由request明確提供、normalize UTC並保存。",
    "`reviewed_at`與`comparison_cutoff_at`維持獨立欄位。",
    "naive review/query timestamp、future review cutoff及future query cutoff均回HTTP 422。",
    "query cutoff早於latest review event cutoff時回HTTP 422。",
    "latest snapshot依symbol、query cutoff、created_at及snapshot_id deterministic選取。",
    "incompatible actual latest snapshot不得退回compatible次新snapshot。",
    "actual latest snapshot integrity failure不得退回次新snapshot。",
    "acknowledged snapshot missing時回blocked，且不得替換baseline。",
    "baseline或latest snapshot integrity failure回`snapshot_integrity_error`。",
    "comparison只能重用Phase 11 `SnapshotComparisonService`。",
    "comparison result、deltas、counts及`comparison_has_deltas`均不得持久化。",
    "stored delta存在時`comparison_has_deltas=true`。",
    "current-context delta存在時`comparison_has_deltas=true`。",
    "comparable且兩類delta皆空時`comparison_has_deltas=false`。",
    "same snapshot、same query cutoff、comparable且無delta時`comparison_has_deltas=false`。",
    "no snapshot、baseline not set、missing baseline、incomparable、blocked、unknown、integrity failure或comparison unavailable時`comparison_has_deltas=null`。",
    "stale維持獨立freshness語意，不自動形成delta、blocked或temporal-change宣稱。",
    "dependency blocked形成`review_state=blocked`。",
    "dependency unknown形成`review_state=unknown`。",
    "page open、GET、scroll或comparison完成均不得自動acknowledge。",
    "workflow metadata不進Evidence Grade、Rule Trace、模型輸出、screening或historical performance。",
    "Research write API預設disabled並回HTTP 503。",
    "帶有non-allowlisted Origin的Research browser GET回HTTP 403。",
    "非loopback、invalid Host、non-allowlisted Origin、invalid CSRF、invalid content type或oversized body的Research write依typed contract拒絕。",
    "frontend source及production bundle均不包含Evidence admin API key。",
    "duplicate acknowledgment同key同semantic payload回原event。",
    "相同idempotency key搭配不同payload回HTTP 409。",
    "duplicate archive/unarchive為deterministic idempotent operation。",
    "backup/restore保留membership、archive state、review history及idempotency behavior。",
    "database failure不得偽裝成empty queue。",
    "list只回summary/count；detail才可回完整Phase 11 comparison。",
    "list maximum 50，並使用同一SQLite read snapshot。",
    "frontend不得使用ranking、recommendation、temporal-change或directional investment語彙。",
    "desktop、mobile、keyboard及accessibility驗證通過。",
    "v1 API golden維持不變。",
    "Phase 8–11既有regression維持。",
    "不新增Journal、price、notification、LLM、broker或trading功能。",
    "migration additive、transactional、rerunnable且rollback-safe。",
    "CSRF session在1,800秒TTL到期後回HTTP 403 `csrf_session_expired`，mutation不得執行。",
    "無Origin的loopback＋valid Host request可讀；無Origin的non-loopback request回HTTP 403。",
    "same snapshot在同一query cutoff下即使freshness為stale，無delta時仍回`comparison_has_deltas=false`及`freshness_status=stale`，且不得宣稱T1至T2沒有變化。",
)


def test_phase12_acceptance_namespace_and_product_boundary():
    evidence = (ROOT / "DOCS" / "PHASE12_ACCEPTANCE_EVIDENCE.md").read_text(encoding="utf-8")
    rows = [line.split("|") for line in evidence.splitlines() if line.startswith("| AC-")]
    assert len(rows) == len(AUTHORITATIVE_REQUIREMENTS) == 57
    for number, (row, requirement) in enumerate(zip(rows, AUTHORITATIVE_REQUIREMENTS), start=1):
        assert row[1].strip() == f"AC-{number:02d}"
        assert row[2].strip() == requirement
        assert row[4].strip() in {"PASS", "NOT VERIFIED"}
    implementation = (ROOT / "DOCS" / "EVIDENCE_MODEL_V2_PHASE12.md").read_text(encoding="utf-8")
    assert "comparison_has_deltas" in implementation
    assert "does not assert" in implementation
    assert "broker connection" in implementation
    assert "real order" in implementation
    assert "Phase 13 behavior" in implementation


def test_phase12_browser_write_client_has_no_admin_secret():
    source = (ROOT / "frontend" / "src" / "api" / "researchClient.ts").read_text(encoding="utf-8")
    assert 'method: "POST"' in source
    assert "X-CSRF-Token" in source
    assert "X-Admin-API-Key" not in source


def test_phase12_workflow_metadata_is_not_model_evidence_or_new_product_scope():
    workflow_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "src/domain/research_workflow.py",
            "src/repositories/research_workflow_repository.py",
            "src/services/research_review_service.py",
            "src/api/routes/research_workflow.py",
        )
    ).lower()
    for forbidden in (
        "evidence_grade", "rule_trace", "broker_api", "place_order",
        "trade_signal", "investment_ranking", "runtime_llm",
    ):
        assert forbidden not in workflow_sources
