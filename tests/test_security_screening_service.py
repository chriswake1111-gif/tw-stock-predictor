from src.domain.screening import (
    ScreeningProfileRevision,
    ScreeningProfileScope,
    SecurityValuationObservation,
    ValuationBasis,
)
from src.domain.valuation import (
    ApprovalResourceType,
    ApprovalStatus,
    ForwardEPSObservation,
    ForwardEPSSourceType,
)
from src.services.forward_eps_service import ForwardEPSService
from src.services.security_screening_service import SecurityScreeningService


class PositiveTechnicalComponent:
    def evaluate(self, symbol, knowledge_cutoff_at, profile):
        return {
            "status": "available",
            "reason": None,
            "turned_positive": True,
            "component_name": profile["technical_component"],
            "rule_id": "WV-03",
            "rule_version": "2.0.0",
            "evidence_level": "B",
            "implementation_mode": "parameterized_support",
            "project_operationalization": False,
            "data_as_of": knowledge_cutoff_at,
            "details": {"fixture": "offline"},
        }


def profile(logical_id="profile-a", *, scope=ScreeningProfileScope.GLOBAL,
            scope_value=None, basis=ValuationBasis.SELF_HISTORY,
            revision_number=1, revision_of=None, available_at="2026-01-05T10:00:00Z"):
    return ScreeningProfileRevision(
        logical_profile_id=logical_id,
        revision_number=revision_number,
        revision_of=revision_of,
        scope=scope,
        scope_value=scope_value,
        valuation_basis=basis,
        valuation_source_name="authorized-research",
        valuation_source_dataset="daily-valuation-v1",
        pe_percentile_max=30,
        pb_percentile_max=30,
        dividend_yield_percentile_min=70,
        history_years=5,
        minimum_observations=20,
        forward_eps_source_name="broker-a",
        forward_eps_source_type=ForwardEPSSourceType.BROKER_REPORT,
        technical_component="wv03_confirmation",
        available_at=available_at,
        created_by="reviewer",
        rationale="approved project-operated research profile",
    )


def setup_clock(monkeypatch):
    now = ["2026-01-10T12:00:00.000000Z"]
    for module in (
        "src.repositories.security_valuation_repository",
        "src.repositories.screening_repository",
        "src.repositories.forward_eps_repository",
    ):
        monkeypatch.setattr(f"{module}.utc_now_timestamp", lambda: now[0])
    return now


def add_valuation_history(service):
    for index in range(20):
        is_current = index == 19
        service.ingest_valuation(
            SecurityValuationObservation(
                logical_observation_id=f"valuation-{index}",
                revision_number=1,
                symbol="2330.TW",
                metric_date=f"2025-01-{index + 1:02d}",
                pe=5.0 if is_current else 10.0 + index,
                pb=0.5 if is_current else 1.0 + index / 10,
                dividend_yield_ratio=0.08 if is_current else 0.01 + index / 1000,
                source_name="authorized-research",
                source_dataset="daily-valuation-v1",
                available_at=f"2025-01-{index + 1:02d}T10:00:00Z",
            ),
            f"valuation-{index}",
        )


def add_forward_eps(db):
    service = ForwardEPSService(db)
    for year, value in ((2027, 10.0), (2028, 12.0)):
        resource = service.ingest_forward_eps(
            ForwardEPSObservation(
                logical_series_id=f"broker-a-{year}",
                revision_number=1,
                symbol="2330.TW",
                fiscal_year=year,
                eps_base=value,
                source_name="broker-a",
                source_type=ForwardEPSSourceType.BROKER_REPORT,
                published_at="2026-01-01",
                available_at=f"2026-01-0{year - 2026}T10:00:00Z",
            ),
            f"eps-{year}",
        )
        service.record_approval(
            resource_type=ApprovalResourceType.FORWARD_EPS,
            resource_id=resource["id"],
            decision=ApprovalStatus.APPROVED,
            rule_id="VAL-02",
            rationale="reviewed forecast source",
            available_at=f"2026-01-0{year - 2026}T11:00:00Z",
            approved_by="reviewer",
            idempotency_key=f"eps-approval-{year}",
        )


def approve(service, profile_id, key="profile-approval",
            decision=ApprovalStatus.APPROVED, approved_at="2026-01-05T11:00:00Z"):
    return service.record_profile_approval(
        profile_revision_id=profile_id,
        decision=decision,
        rationale="reviewed screening research profile",
        approved_at=approved_at,
        approved_by="reviewer",
        idempotency_key=key,
    )


def complete_setup(tmp_path, monkeypatch):
    setup_clock(monkeypatch)
    db = str(tmp_path / "db.sqlite")
    service = SecurityScreeningService(
        db, technical_component=PositiveTechnicalComponent()
    )
    add_valuation_history(service)
    add_forward_eps(db)
    revision = service.ingest_profile(profile(), "profile-a")
    approval = approve(service, revision["id"])
    return service, revision, approval


def test_approved_profile_produces_detailed_research_result(tmp_path, monkeypatch):
    service, revision, approval = complete_setup(tmp_path, monkeypatch)
    result = service.analyze("2330.TW", "2026-01-20T00:00:00Z")

    assert result["status"] == "available"
    assert result["research_result"] == "meets_approved_profile"
    assert set(result["components"]) == {
        "pe", "pb", "dividend_yield", "forward_eps_growth", "technical_turn"
    }
    trace = result["rule_trace"]
    assert trace["logical_profile_id"] == "profile-a"
    assert trace["profile_revision_id"] == revision["id"]
    assert trace["approval_id"] == approval["approval_id"]
    assert trace["evidence_level"] == "C"
    assert trace["implementation_mode"] == "project_operationalization"
    technical_trace = result["technical_rule_trace"]
    assert technical_trace == {
        "rule_id": "WV-03",
        "version": "2.0.0",
        "evidence_level": "B",
        "implementation_mode": "parameterized_support",
        "project_operationalization": False,
        "source_data_as_of": "2026-01-20T00:00:00.000000Z",
    }
    assert result["automatic_order"] is False


def test_forged_provider_metadata_never_enters_rule_trace(tmp_path, monkeypatch):
    service, revision, _ = complete_setup(tmp_path, monkeypatch)

    class ForgedTechnicalComponent(PositiveTechnicalComponent):
        def evaluate(self, symbol, knowledge_cutoff_at, profile):
            result = super().evaluate(symbol, knowledge_cutoff_at, profile)
            result.update(evidence_level="A", implementation_mode="verified_core")
            return result

    service.technical_component = ForgedTechnicalComponent()
    result = service.analyze(
        "2330.TW",
        "2026-01-20T00:00:00Z",
        profile_revision_id=revision["id"],
    )
    component = result["components"]["technical_turn"]
    assert result["status"] == "insufficient_data"
    assert component["reason"] == "technical_rule_metadata_mismatch"
    assert component["evidence_level"] == "B"
    assert component["implementation_mode"] == "parameterized_support"
    assert result["technical_rule_trace"] is None
    assert result["rules_used"] == []


def test_missing_profile_approval_needs_human_input(tmp_path, monkeypatch):
    setup_clock(monkeypatch)
    service = SecurityScreeningService(str(tmp_path / "db.sqlite"))
    revision = service.ingest_profile(profile(), "profile-a")
    result = service.analyze(
        "2330.TW", "2026-01-20T00:00:00Z",
        profile_revision_id=revision["id"],
    )
    assert result["status"] == "needs_human_input"
    assert result["reason"] == "approved_screening_profile_required"
    assert result["rules_used"] == []


def test_profile_selection_is_explicit_when_multiple_apply(tmp_path, monkeypatch):
    service, first, _ = complete_setup(tmp_path, monkeypatch)
    second = service.ingest_profile(profile("profile-b"), "profile-b")
    approve(service, second["id"], key="profile-b-approval")

    ambiguous = service.analyze("2330.TW", "2026-01-20T00:00:00Z")
    explicit = service.analyze(
        "2330.TW", "2026-01-20T00:00:00Z",
        profile_revision_id=first["id"],
    )
    assert ambiguous["reason"] == "screening_profile_selection_required"
    assert len(ambiguous["applicable_profile_revision_ids"]) == 2
    assert explicit["status"] == "available"
    assert explicit["profile"]["profile_revision_id"] == first["id"]


def test_new_profile_revision_does_not_inherit_old_approval(tmp_path, monkeypatch):
    service, first, _ = complete_setup(tmp_path, monkeypatch)
    second = service.ingest_profile(
        profile(
            revision_number=2,
            revision_of=first["id"],
            available_at="2026-01-06T10:00:00Z",
        ),
        "profile-a-v2",
    )
    result = service.analyze(
        "2330.TW", "2026-01-20T00:00:00Z", logical_profile_id="profile-a"
    )
    assert result["status"] == "needs_human_input"
    assert result["reason"] == "approved_screening_profile_required"
    assert result["profile_revision_id"] == second["id"]


def test_latest_profile_approval_revoke_has_no_fallback(tmp_path, monkeypatch):
    service, revision, _ = complete_setup(tmp_path, monkeypatch)
    approve(
        service,
        revision["id"],
        key="profile-revoke",
        decision=ApprovalStatus.REVOKED,
        approved_at="2026-01-06T11:00:00Z",
    )
    result = service.analyze(
        "2330.TW", "2026-01-20T00:00:00Z",
        profile_revision_id=revision["id"],
    )
    assert result["status"] == "needs_human_input"
    assert result["reason"] == "screening_profile_approval_revoked"


def test_profile_approval_after_cutoff_is_invisible(tmp_path, monkeypatch):
    now = setup_clock(monkeypatch)
    db = str(tmp_path / "db.sqlite")
    service = SecurityScreeningService(db)
    revision = service.ingest_profile(profile(), "profile-a")
    now[0] = "2026-02-01T12:00:00.000000Z"
    approve(
        service,
        revision["id"],
        approved_at="2026-02-01T11:00:00Z",
    )
    result = service.analyze(
        "2330.TW", "2026-01-20T00:00:00Z",
        profile_revision_id=revision["id"],
    )
    assert result["reason"] == "approved_screening_profile_required"


def test_industry_basis_fails_closed_without_peer_contract(tmp_path, monkeypatch):
    setup_clock(monkeypatch)
    service = SecurityScreeningService(str(tmp_path / "db.sqlite"))
    revision = service.ingest_profile(
        profile(
            scope=ScreeningProfileScope.INDUSTRY,
            scope_value="semiconductor",
            basis=ValuationBasis.INDUSTRY,
        ),
        "industry-profile",
    )
    approve(service, revision["id"])
    result = service.analyze(
        "2330.TW", "2026-01-20T00:00:00Z",
        profile_revision_id=revision["id"],
    )
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "industry_peer_data_required"


def test_missing_technical_provider_never_meets_profile(tmp_path, monkeypatch):
    service, revision, _ = complete_setup(tmp_path, monkeypatch)
    service.technical_component = SecurityScreeningService(
        str(tmp_path / "other.sqlite")
    ).technical_component
    result = service.analyze(
        "2330.TW", "2026-01-20T00:00:00Z",
        profile_revision_id=revision["id"],
    )
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "technical_confirmation_required"
    assert result["research_result"] is None
    assert result["rules_used"] == []
