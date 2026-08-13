from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_phase12_acceptance_namespace_and_product_boundary():
    evidence = (ROOT / "DOCS" / "PHASE12_ACCEPTANCE_EVIDENCE.md").read_text(encoding="utf-8")
    for number in range(1, 58):
        assert evidence.count(f"| AC-{number:02d} |") == 1
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
