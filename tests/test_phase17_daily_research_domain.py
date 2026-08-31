from __future__ import annotations

import copy

import pytest

from src.domain.daily_research_context_provenance import (
    build_daily_context_reference,
    context_change_reasons,
    verify_daily_context_reference,
)
from src.domain.daily_research_refresh_policy import evaluate_required_analysis_sections
from src.domain.daily_research_review_context import (
    BASELINE_SELECTION_REASON_CODES,
    DAILY_CONTRACT_VERSIONS,
    DAILY_REASON_CODES,
    DailyResearchCursor,
    DailyResearchCursorError,
    baseline_selection_eligibility,
    decode_daily_cursor,
    derive_review_flags,
    reduce_page_status,
    reduce_preflight_status,
    validate_daily_d_k,
)


def test_exact_contract_version_registry_is_locked():
    assert DAILY_CONTRACT_VERSIONS == (
        "daily_research_review_context_v1",
        "daily_research_review_context_policy_v1",
        "daily_research_review_status_v1",
        "daily_research_workflow_time_v1",
        "daily_research_snapshot_selection_v1",
        "daily_research_review_reason_registry_v1",
        "daily_research_review_d_k_v1",
        "daily_research_review_order_v1",
        "daily_research_review_cursor_v1",
        "daily_research_snapshot_integration_v1",
        "daily_research_preflight_v1",
        "daily_research_baseline_selection_policy_v1",
        "daily_research_baseline_selection_reason_registry_v1",
    )


def test_d_k_validation_inclusive_boundary_and_timezone_rules():
    assert validate_daily_d_k(
        "2026-08-31", "2026-08-31T00:00:00+00:00", "2026-08-31T00:00:00Z"
    ) == ("2026-08-31", "2026-08-31T00:00:00Z", "2026-08-31T00:00:00Z")
    with pytest.raises(ValueError, match="knowledge_cutoff_after_request"):
        validate_daily_d_k(
            "2026-08-31", "2026-08-31T00:00:00.000001Z", "2026-08-31T00:00:00Z"
        )
    with pytest.raises(ValueError, match="knowledge_cutoff_timezone_required"):
        validate_daily_d_k(
            "2026-08-31", "2026-08-31T00:00:00", "2026-08-31T00:00:00Z"
        )
    with pytest.raises(ValueError, match="market_date_after_knowledge_cutoff"):
        validate_daily_d_k(
            "2026-09-01", "2026-08-31T00:00:00Z", "2026-08-31T00:00:00Z"
        )


def test_status_and_review_reducers_keep_delta_freshness_and_attention_separate():
    assert reduce_page_status([]) == "available"
    assert reduce_page_status(["blocked", "blocked"]) == "blocked"
    assert reduce_page_status(["available", "blocked"]) == "partial"
    assert reduce_preflight_status("available", "blocked") == "blocked"
    stale = derive_review_flags(
        review_state="comparable_without_deltas",
        comparison_status="comparable",
        comparison_has_deltas=False,
        freshness_status="stale",
        item_status="available",
        reason_codes=["snapshot_stale"],
    )
    assert stale == {
        "review_needed": True,
        "review_blocked": False,
        "review_limited": True,
    }


def test_reason_registries_are_neutral_and_fixed():
    forbidden = ("buy", "sell", "hold", "bull", "bear", "rank", "score")
    for code in (*DAILY_REASON_CODES, *BASELINE_SELECTION_REASON_CODES):
        assert not any(word in code for word in forbidden)


def test_cursor_round_trip_and_context_mismatch():
    cursor = DailyResearchCursor(
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        limit=25,
        active_population_checksum="abc",
        last_symbol="2330.TW",
        last_watchlist_item_id="item-1",
    ).encode()
    decoded = decode_daily_cursor(
        cursor,
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        limit=25,
    )
    assert decoded.last_symbol == "2330.TW"
    with pytest.raises(DailyResearchCursorError, match="daily_cursor_context_mismatch"):
        decode_daily_cursor(
            cursor,
            market_date="2026-08-31",
            knowledge_cutoff_at="2026-08-31T00:00:00Z",
            limit=50,
        )


def test_baseline_selection_emits_all_reasons_in_registry_order():
    result = baseline_selection_eligibility(
        item_symbol="2330.TW",
        snapshot={
            "symbol": "2317.TW",
            "created_at": "2026-09-01T00:00:00Z",
            "knowledge_cutoff_at": "2026-09-02T00:00:00Z",
        },
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        integrity_error=True,
        contract_supported=False,
    )
    assert result["baseline_selection_reason_codes"] == list(
        BASELINE_SELECTION_REASON_CODES[1:]
    )
    assert result["baseline_selection_blocked"] is True


def _reference(status: str = "available"):
    return build_daily_context_reference(
        market_date="2026-08-31",
        knowledge_cutoff_at="2026-08-31T00:00:00Z",
        phase13={
            "identity": {"canonical_symbol": "2330.TW", "venue": "TWSE", "identity_status": "resolved"},
            "lifecycle": {"listing_status": "listed"},
            "trading": {"trading_state": "trading"},
            "source_state": {},
        },
        phase14={"eod": {"market_date": "2026-08-31", "status": "available"}},
        phase15={"coverage": {"status": "available"}},
        phase16_item={"status": status, "canonical_symbol": "2330.TW", "venue": "TWSE"},
        phase16_aggregate={"status": "available", "scope": "phase16_full_twse_tpex_context"},
        context_reasons=[],
    )


def test_provenance_is_allowlisted_hashed_and_exactly_mapped():
    baseline = _reference()
    assert verify_daily_context_reference(baseline)
    current = copy.deepcopy(baseline)
    current["phase13"]["trading"]["trading_state"] = "suspended"
    current.pop("context_digest")
    current = build_daily_context_reference(
        market_date=current["market_date"],
        knowledge_cutoff_at=current["knowledge_cutoff_at"],
        phase13=current["phase13"],
        phase14=current["phase14"],
        phase15=current["phase15"],
        phase16_item=current["phase16"]["item"],
        phase16_aggregate=current["phase16"]["aggregate"],
        context_reasons=[],
    )
    assert context_change_reasons(baseline, current) == ["trading_state_changed"]


def test_five_section_gate_accepts_only_complete_or_explicit_not_applicable():
    analysis = {name: {"status": "available"} for name in (
        "valuation", "liquidity", "technical_support", "screening", "target_confluence"
    )}
    assert evaluate_required_analysis_sections(analysis)["eligible"] is True
    analysis["screening"] = {"status": "not_applicable"}
    assert evaluate_required_analysis_sections(analysis) == {
        "eligible": False,
        "error": "required_analysis_section_applicability_unresolved",
        "section": "screening",
    }
    analysis["screening"]["applicability"] = {
        "applicable": False,
        "applicability_reason": "profile_scope_not_applicable",
        "method_policy_version": "screening_profile_v1",
    }
    assert evaluate_required_analysis_sections(analysis)["eligible"] is True
