import pytest

from src.domain.valuation import (
    ApprovalResourceType,
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
    PEScenario,
    PEScope,
    ValuationApproval,
)
from src.engine.forward_pe_valuation import ForwardPEValuationEngine
from src.repositories.forward_eps_repository import ForwardEPSRepository


CUTOFF = "2026-08-02T00:00:00Z"


def approve(repo, resource_type, resource_id, key, *, evidence="B", project=False,
            decision=ApprovalStatus.APPROVED, rule_id=None):
    return repo.add_approval(
        ValuationApproval(
            approval_id=f"approval-{key}", resource_type=resource_type,
            resource_id=resource_id, decision=decision,
            rule_id=rule_id or ("VAL-02" if resource_type is ApprovalResourceType.FORWARD_EPS else "VAL-04"),
            evidence_level=evidence, project_operationalization=project,
            approved_by="admin", rationale="reviewed evidence",
            available_at="2026-08-01T00:02:00Z",
        ),
        f"approval-{key}", ingested_at="2026-08-01T00:03:00Z",
    )


def add_eps(repo, *, series, source, eps, approved=True):
    row = repo.add_forward_eps(
        ForwardEPSObservation(
            logical_series_id=series, revision_number=1, symbol="2330.TW",
            fiscal_year=2027, eps_base=eps, source_name=source,
            source_type=ForwardEPSSourceType.MANUAL, published_at="2026-08-01",
            available_at="2026-08-01T00:00:00Z",
        ), f"eps-{series}", ingested_at="2026-08-01T00:01:00Z",
    )
    if approved:
        approve(repo, ApprovalResourceType.FORWARD_EPS, row["id"], f"eps-{series}")
    return row


def add_pe(repo, *, series, scope, pe, approved=True, evidence="B", project=False,
           revision=1, revision_of=None, effective_from=None, effective_to=None,
           symbol=None, industry=None, market=None, evidence_basis_rule_id=None):
    values = {
        "symbol": symbol if symbol is not None else ("2330.TW" if scope is PEScope.SYMBOL else None),
        "industry": industry if industry is not None else ("Semiconductor" if scope is PEScope.INDUSTRY else None),
        "market": market if market is not None else ("TW" if scope is PEScope.MARKET else None),
    }
    row = repo.add_pe_scenario(
        PEScenario(
            logical_series_id=series, revision_number=revision, revision_of=revision_of,
            label="base", pe_value=pe, rationale="research scenario", evidence_level="U",
            scope=scope, available_at="2026-08-01T00:00:00Z",
            approval_status=ApprovalStatus.DRAFT, effective_from=effective_from,
            effective_to=effective_to,
            evidence_basis_rule_id=evidence_basis_rule_id, **values,
        ), f"pe-{series}-{revision}", ingested_at="2026-08-01T00:01:00Z",
    )
    if approved:
        approve(repo, ApprovalResourceType.PE_SCENARIO, row["id"], f"pe-{series}-{revision}",
                evidence=evidence, project=project)
    return row


def test_multiple_forward_eps_sources_are_not_averaged(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    first = add_eps(repo, series="source-a", source="Source A", eps=10.0)
    second = add_eps(repo, series="source-b", source="Source B", eps=20.0)
    add_pe(repo, series="symbol-pe", scope=PEScope.SYMBOL, pe=15.0)
    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)
    assert result["multiple_sources_aggregated"] is False
    assert {c["observation_id"] for c in result["target_matrix"]} == {first["id"], second["id"]}
    assert {c["target_price"] for c in result["target_matrix"]} == {150.0, 300.0}


def test_verified_matrix_uses_only_approved_symbol_scope_pe(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    eps = add_eps(repo, series="source-a", source="Source A", eps=10.0)
    symbol_pe = add_pe(repo, series="symbol-pe", scope=PEScope.SYMBOL, pe=15.0)
    industry_pe = add_pe(repo, series="industry-pe", scope=PEScope.INDUSTRY, pe=18.0)
    market_pe = add_pe(repo, series="market-pe", scope=PEScope.MARKET, pe=12.0)
    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF, industry="Semiconductor", market="TW")
    assert {c["pe_scenario_id"] for c in result["target_matrix"]} == {symbol_pe["id"]}
    assert {r["id"] for r in result["reference_pe_scenarios"]["industry"]} == {industry_pe["id"]}
    assert {r["id"] for r in result["reference_pe_scenarios"]["market"]} == {market_pe["id"]}
    cell = result["target_matrix"][0]
    assert cell["approval_ids"]["VAL-02"] == "approval-eps-source-a"
    assert cell["approval_ids"]["VAL-04"] == "approval-pe-symbol-pe-1"
    assert eps["id"] == cell["observation_id"]
    assert any("approval-eps-source-a" in t["approval_ids"] for t in result["rules_used"])
    trace = {row["rule_id"]: row["approval_ids"] for row in result["rules_used"]}
    assert trace == {
        "VAL-01": [],
        "VAL-02": ["approval-eps-source-a"],
        "VAL-04": ["approval-pe-symbol-pe-1"],
    }
    assert cell["rule_ids"] == ["VAL-01", "VAL-02", "VAL-04"]
    assert result["pe_scenarios"][0]["import_status"] == "draft"
    assert result["pe_scenarios"][0]["effective_approval_status"] == "approved"
    assert "approval_status" not in result["pe_scenarios"][0]


@pytest.mark.parametrize("pe_value", [20.0, 21.0, 25.0])
def test_explicit_public_case_pe_includes_val_03_without_borrowed_approval(
    tmp_path, pe_value
):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    add_eps(repo, series="source", source="Source", eps=10.0)
    add_pe(
        repo, series=f"public-case-{pe_value}", scope=PEScope.SYMBOL,
        pe=pe_value, evidence_basis_rule_id="VAL-03",
    )

    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)
    cell = result["target_matrix"][0]
    trace = {row["rule_id"]: row["approval_ids"] for row in result["rules_used"]}

    assert cell["rule_ids"] == ["VAL-01", "VAL-02", "VAL-03", "VAL-04"]
    assert set(cell["approval_ids"]) == {"VAL-02", "VAL-04"}
    assert trace["VAL-03"] == []
    assert trace["VAL-04"] == [f"approval-pe-public-case-{pe_value}-1"]


def test_val_03_evidence_basis_rejects_non_public_case_pe():
    with pytest.raises(ValueError, match="requires PE 20, 21, or 25"):
        PEScenario(
            logical_series_id="non-public-case", revision_number=1,
            label="base", pe_value=15.0, rationale="research scenario",
            evidence_level="U", scope=PEScope.SYMBOL, symbol="2330.TW",
            available_at="2026-08-01T00:00:00Z",
            approval_status=ApprovalStatus.DRAFT,
            evidence_basis_rule_id="VAL-03",
        ).validated()


def test_unapproved_or_revoked_forward_eps_is_excluded(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    draft = add_eps(repo, series="draft", source="Draft", eps=10.0, approved=False)
    revoked = add_eps(repo, series="revoked", source="Revoked", eps=11.0)
    repo.add_approval(
        ValuationApproval(
            approval_id="approval-revoke-eps", resource_type=ApprovalResourceType.FORWARD_EPS,
            resource_id=revoked["id"], decision=ApprovalStatus.REVOKED,
            rule_id="VAL-02", evidence_level="B", project_operationalization=False,
            approved_by="admin", rationale="revoked after review",
            available_at="2026-08-01T00:04:00Z",
        ), "approval-revoke-eps", ingested_at="2026-08-01T00:05:00Z",
    )
    add_pe(repo, series="symbol-pe", scope=PEScope.SYMBOL, pe=15.0)
    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)
    assert result["status"] == "needs_human_input"
    assert result["reason"] == "approved_forward_eps_required"
    assert {row["effective_approval_status"] for row in result["forward_eps"]} == {
        "draft", "revoked"
    }
    assert draft["id"] != revoked["id"]


def test_all_revoked_forward_eps_has_explicit_reason(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    revoked = add_eps(repo, series="revoked", source="Revoked", eps=11.0)
    repo.add_approval(
        ValuationApproval(
            approval_id="approval-revoked", resource_type=ApprovalResourceType.FORWARD_EPS,
            resource_id=revoked["id"], decision=ApprovalStatus.REVOKED,
            rule_id="VAL-02", evidence_level="A", project_operationalization=False,
            approved_by="admin", rationale="withdrawn",
            available_at="2026-08-01T00:04:00Z",
        ), "approval-revoked", ingested_at="2026-08-01T00:05:00Z",
    )
    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "forward_eps_approval_revoked"


def test_u_pe_fails_closed_and_c_is_project_operationalization(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    add_eps(repo, series="source", source="Source", eps=10.0)
    u_pe = add_pe(repo, series="u-pe", scope=PEScope.SYMBOL, pe=10.0, evidence="U")
    with pytest.raises(ValueError, match="project_operationalization"):
        add_pe(repo, series="bad-c", scope=PEScope.SYMBOL, pe=12.0, evidence="C", project=False)
    c_pe = add_pe(repo, series="c-pe", scope=PEScope.SYMBOL, pe=15.0, evidence="C", project=True)
    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)
    assert {c["pe_scenario_id"] for c in result["target_matrix"]} == {c_pe["id"]}
    assert u_pe["id"] not in {r["id"] for r in result["pe_scenarios"]}
    assert result["pe_scenarios"][0]["approved_evidence_level"] == "C"
    assert result["pe_scenarios"][0]["project_operationalization"] == 1


@pytest.mark.parametrize("changes", [
    {"scope": PEScope.SYMBOL, "symbol": "2317.TW"},
    {"scope": PEScope.INDUSTRY, "symbol": None, "industry": "Semiconductor"},
])
def test_pe_revision_rejects_cross_scope_identity(tmp_path, changes):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    first = add_pe(repo, series="identity", scope=PEScope.SYMBOL, pe=15.0, approved=False)
    with pytest.raises(ValueError, match="revision cannot change identity"):
        add_pe(repo, series="identity", pe=16.0, approved=False, revision=2,
               revision_of=first["id"], **changes)


def test_pe_effective_range_filters_not_yet_and_expired(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    add_eps(repo, series="source", source="Source", eps=10.0)
    add_pe(repo, series="future", scope=PEScope.SYMBOL, pe=15.0,
           effective_from="2026-08-03T00:00:00Z")
    add_pe(repo, series="expired", scope=PEScope.SYMBOL, pe=16.0,
           effective_to="2026-08-01T12:00:00Z")
    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)
    assert result["status"] == "needs_human_input"
    assert result["pe_scenarios"] == []


def test_zero_or_negative_eps_is_saved_but_matrix_is_not_applicable(tmp_path):
    repo = ForwardEPSRepository(str(tmp_path / "valuation.db"))
    add_eps(repo, series="loss-source", source="Loss Source", eps=-2.0)
    add_pe(repo, series="symbol-pe", scope=PEScope.SYMBOL, pe=15.0)
    result = ForwardPEValuationEngine(repo).evaluate("2330.TW", CUTOFF)
    assert result["status"] == "not_applicable"
    assert result["target_matrix"][0]["target_price"] is None


def test_pe_validates_effective_range_and_approved_text():
    base = dict(logical_series_id="x", revision_number=1, label="base", pe_value=10,
                rationale="reason", evidence_level="B", scope=PEScope.SYMBOL,
                symbol="2330.TW", available_at="2026-08-01T00:00:00Z",
                approval_status=ApprovalStatus.DRAFT)
    with pytest.raises(ValueError, match="effective_from"):
        PEScenario(**base, effective_from="2026-08-02T00:00:00Z",
                   effective_to="2026-08-01T00:00:00Z").validated()
    for field in ("label", "rationale"):
        values = {**base, field: " ", "approval_status": ApprovalStatus.APPROVED,
                  "approved_by": "admin", "approved_at": "2026-08-01T00:00:00Z"}
        with pytest.raises(ValueError, match=field):
            PEScenario(**values).validated()
