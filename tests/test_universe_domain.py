from __future__ import annotations

import pytest

from src.domain.universe import (
    UniverseStatus,
    UniverseVenue,
    canonical_symbol_for,
    map_canonical_symbol,
    parse_source_temporal,
    universe_status_matrix_v1,
    validate_knowledge_cutoff_at,
)


def test_cutoff_requires_aware_timestamp_and_source_date_keeps_precision():
    assert validate_knowledge_cutoff_at("2026-08-21T00:00:00+08:00").endswith("Z")
    with pytest.raises(ValueError):
        validate_knowledge_cutoff_at("2026-08-21")
    with pytest.raises(ValueError):
        validate_knowledge_cutoff_at("2026-08-21T00:00:00")
    assert parse_source_temporal("2026-08-21") == ("2026-08-21", "date")


def test_mapping_preserves_leading_zero_and_unknown_security_type():
    assert canonical_symbol_for(UniverseVenue.TWSE, "0050") == "0050.TW"
    mapped = map_canonical_symbol(venue="TPEX", official_code="00878", approved_scope=True,
                                  identity_verified=True, security_type="unknown")
    assert mapped["canonical_symbol"] == "00878.TWO"
    assert mapped["security_type"] == "unknown"
    rejected = map_canonical_symbol(venue="TWSE", official_code="2330", approved_scope=False,
                                    identity_verified=True)
    assert rejected["canonical_symbol"] is None
    assert rejected["reason"] == "canonical_mapping_unverified"


def test_status_matrix_is_fail_closed_and_unknown_freshness_is_not_actionable():
    assert universe_status_matrix_v1({"identity_reference": None})["status"] == UniverseStatus.INSUFFICIENT_DATA.value
    base = {"instrument_id": "u1", "official_code": "2330"}
    assert universe_status_matrix_v1({"identity_reference": base, "operational_freshness": "unknown", "reasons": ["freshness_unknown"]})["status"] == "partial"
    assert universe_status_matrix_v1({"identity_reference": base, "operational_freshness": "unknown", "reasons": ["source_revision_awaiting_review"]})["status"] == "needs_human_input"
    assert universe_status_matrix_v1({"identity_reference": base, "operational_freshness": "current", "current_complete": True})["status"] == "available"
