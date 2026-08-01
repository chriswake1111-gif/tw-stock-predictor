from src.domain.valuation import (
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    PEScenario,
    PEScope,
)
from src.engine.forward_pe_valuation import ForwardPEValuationEngine
from src.repositories.forward_eps_repository import ForwardEPSRepository


CUTOFF = "2026-08-02T00:00:00Z"


def add_eps(repo, *, series, source, eps):
    return repo.add_forward_eps(
        ForwardEPSObservation(
            logical_series_id=series,
            revision_number=1,
            symbol="2330.TW",
            fiscal_year=2027,
            eps_base=eps,
            source_name=source,
            source_type=ForwardEPSSourceType.MANUAL,
            published_at="2026-08-01",
            available_at="2026-08-01T00:00:00Z",
        ),
        f"eps-{series}",
        ingested_at="2026-08-01T00:01:00Z",
    )


def add_pe(
    repo,
    *,
    series,
    scope,
    pe,
    status=ApprovalStatus.APPROVED,
    revision=1,
    revision_of=None,
):
    values = {
        "symbol": "2330.TW" if scope is PEScope.SYMBOL else None,
        "industry": "Semiconductor" if scope is PEScope.INDUSTRY else None,
        "market": "TW" if scope is PEScope.MARKET else None,
    }
    return repo.add_pe_scenario(
        PEScenario(
            logical_series_id=series,
            revision_number=revision,
            revision_of=revision_of,
            label="base",
            pe_value=pe,
            rationale="approved research scenario",
            evidence_level="B",
            scope=scope,
            available_at="2026-08-01T00:00:00Z",
            approval_status=status,
            approved_by="reviewer" if status is ApprovalStatus.APPROVED else None,
            approved_at="2026-08-01T00:00:00Z" if status is ApprovalStatus.APPROVED else None,
            **values,
        ),
        f"pe-{series}-{revision}",
        ingested_at="2026-08-01T00:01:00Z",
    )


def test_multiple_forward_eps_sources_are_not_averaged(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    first = add_eps(repo, series="source-a", source="Source A", eps=10.0)
    second = add_eps(repo, series="source-b", source="Source B", eps=20.0)
    add_pe(repo, series="symbol-pe", scope=PEScope.SYMBOL, pe=15.0)

    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)

    assert result["status"] == "available"
    assert result["multiple_sources_aggregated"] is False
    assert {cell["observation_id"] for cell in result["target_matrix"]} == {
        first["id"], second["id"]
    }
    assert {cell["target_price"] for cell in result["target_matrix"]} == {150.0, 300.0}


def test_verified_matrix_uses_only_approved_symbol_scope_pe(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    add_eps(repo, series="source-a", source="Source A", eps=10.0)
    symbol_pe = add_pe(repo, series="symbol-pe", scope=PEScope.SYMBOL, pe=15.0)
    industry_pe = add_pe(repo, series="industry-pe", scope=PEScope.INDUSTRY, pe=18.0)
    market_pe = add_pe(repo, series="market-pe", scope=PEScope.MARKET, pe=12.0)

    result = ForwardPEValuationEngine(repo).evaluate(
        "2330.TW", CUTOFF, industry="Semiconductor", market="TW"
    )

    assert {cell["pe_scenario_id"] for cell in result["target_matrix"]} == {symbol_pe["id"]}
    assert {row["id"] for row in result["reference_pe_scenarios"]["industry"]} == {industry_pe["id"]}
    assert {row["id"] for row in result["reference_pe_scenarios"]["market"]} == {market_pe["id"]}
    assert result["reference_pe_scenarios"]["automatic_use"] is False


def test_draft_and_revoked_pe_do_not_enter_matrix(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    add_eps(repo, series="source-a", source="Source A", eps=10.0)
    add_pe(
        repo, series="draft-pe", scope=PEScope.SYMBOL, pe=10.0,
        status=ApprovalStatus.DRAFT,
    )
    approved = add_pe(repo, series="revoked-pe", scope=PEScope.SYMBOL, pe=15.0)
    add_pe(
        repo, series="revoked-pe", scope=PEScope.SYMBOL, pe=15.0,
        status=ApprovalStatus.REVOKED, revision=2, revision_of=approved["id"],
    )

    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)

    assert result["status"] == "needs_human_input"
    assert result["pe_scenarios"] == []
    assert result["target_matrix"] == []


def test_zero_or_negative_eps_is_saved_but_matrix_is_not_applicable(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    add_eps(repo, series="loss-source", source="Loss Source", eps=-2.0)
    add_pe(repo, series="symbol-pe", scope=PEScope.SYMBOL, pe=15.0)

    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)

    assert result["status"] == "not_applicable"
    assert result["target_matrix"][0]["target_price"] is None
    assert result["target_matrix"][0]["status"] == "not_applicable"
