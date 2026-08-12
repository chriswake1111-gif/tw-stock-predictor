from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_comparison_routes_are_static_before_dynamic_routes() -> None:
    api_source = (ROOT / "src/api/routes/v2_valuation.py").read_text(encoding="utf-8")
    app_source = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")

    assert api_source.index('@router.get("/analysis/snapshots/compare")') < api_source.index(
        '@router.get("/analysis/{symbol}")'
    )
    assert app_source.index('path="/snapshots/compare"') < app_source.index(
        'path="/snapshots/:snapshotId"'
    )


def test_comparison_surface_remains_read_only_and_server_authoritative() -> None:
    route_source = (ROOT / "src/api/routes/v2_valuation.py").read_text(encoding="utf-8")
    service_source = (ROOT / "src/services/snapshot_comparison_service.py").read_text(
        encoding="utf-8"
    )
    page_source = (ROOT / "frontend/src/pages/SnapshotComparisonPage.tsx").read_text(
        encoding="utf-8"
    )

    comparison_route = route_source[
        route_source.index('@router.get("/analysis/snapshots/compare")') :
        route_source.index('@router.get("/analysis/{symbol}")')
    ]
    assert "@router.post" not in comparison_route
    assert "PRAGMA query_only = ON" in service_source
    assert 'BEGIN"' in service_source
    assert "stored_deltas" in page_source
    assert "current_context_deltas" in page_source
    assert "Math.abs" not in page_source
    assert "JSON.parse" not in page_source


def test_phase11_does_not_add_trading_or_directional_language() -> None:
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "src/domain/snapshot_comparison.py",
            "src/engine/snapshot_comparator.py",
            "src/services/snapshot_comparison_service.py",
            "frontend/src/pages/SnapshotComparisonPage.tsx",
        )
    ).lower()

    for forbidden in ("broker api", "real order", "auto trading", "guaranteed return"):
        assert forbidden not in sources


APPROVED_REQUIREMENTS = (
    "同 symbol、model version、capture mode 的 snapshots 可通過 gate。",
    "不同 symbol 回傳 `incomparable_contract/different_symbol`。",
    "不同 model version fail closed。",
    "不同 capture mode fail closed。",
    "缺少 cutoff 為 request validation failure。",
    "cutoff 無 timezone 被拒絕。",
    "snapshot hash 失敗時不產生 semantic deltas。",
    "stored deltas 只使用 taxonomy v1。",
    "Forward EPS／PE revision 與 approval references 可追溯。",
    "FB-03 永遠呈現 target，FB-04 永遠呈現 support。",
    "higher／lower target 不產生方向語意。",
    "screening pass／fail 只顯示 before／after。",
    "confluence clusters 不排名、不選最佳。",
    "current approval revoke 不改寫 stored approval。",
    "兩邊 freshness 使用同 cutoff 及同 DB read snapshot。",
    "unknown／blocked reasons 完整保留。",
    "same snapshot comparison deltas 為空。",
    "相同輸入與 DB state 產生相同 canonical response。",
    "comparison GET 不造成 database row count 改變。",
    "Phase 8 evaluator及資料完全不變。",
    "Frontend 不實作自己的 diff。",
    "UI 清楚分隔 stored facts 與 current context。",
    "UI 無 bullish／bearish／confidence／buy／sell 字樣。",
    "沒有 migration。",
    "既有 v1／v2 API regression 通過。",
    "沒有新增 broker API 或真實交易能力。",
)


def test_phase11_acceptance_evidence_preserves_approved_ac_contract():
    text = (ROOT / "DOCS/PHASE11_ACCEPTANCE_EVIDENCE.md").read_text(encoding="utf-8")
    for number, requirement in enumerate(APPROVED_REQUIREMENTS, start=1):
        assert f"| AC-{number:02d} | {requirement} |" in text


def test_public_comparison_frontend_contract_omits_snapshot_hash():
    types = (ROOT / "frontend/src/api/types.ts").read_text(encoding="utf-8")
    comparison = types.split("export interface SnapshotComparisonReference", 1)[1].split("}", 1)[0]
    assert "output_sha256" not in comparison
