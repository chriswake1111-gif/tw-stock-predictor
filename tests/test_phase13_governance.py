from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_phase13_governance_has_no_quote_or_trading_path():
    migration = (ROOT / "migrations" / "20260821_16_phase13_universe_foundation.sql").read_text(encoding="utf-8")
    assert "tpex_mainboard_quotes" not in migration
    dto = (ROOT / "src" / "repositories" / "universe_repository.py").read_text(encoding="utf-8")
    assert "payload_sha256" not in dto[dto.index("def _safe_dto"):dto.index("def _find_revision")]
    assert "buy(" not in dto and "sell(" not in dto
    assert "Phase 14" not in str(ROOT / "src" / "domain" / "universe.py")
