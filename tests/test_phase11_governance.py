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
