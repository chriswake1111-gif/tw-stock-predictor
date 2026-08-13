import pytest

from src.domain.research_workflow import (
    canonical_research_symbol,
    comparison_has_deltas,
)


def test_phase12_symbol_identity_is_canonical_and_otc_is_explicit():
    assert canonical_research_symbol("2330") == "2330.TW"
    assert canonical_research_symbol("2330.tw") == "2330.TW"
    assert canonical_research_symbol("2330.TWO") == "2330.TWO"
    with pytest.raises(ValueError, match="research_symbol_invalid"):
        canonical_research_symbol("not-a-stock")


def test_comparison_has_deltas_is_nullable_and_neutral():
    assert comparison_has_deltas(
        comparison_status="comparable", stored_delta_count=0,
        current_context_delta_count=0,
    ) is False
    assert comparison_has_deltas(
        comparison_status="comparable", stored_delta_count=1,
        current_context_delta_count=0,
    ) is True
    assert comparison_has_deltas(
        comparison_status="blocked", stored_delta_count=0,
        current_context_delta_count=0,
    ) is None
