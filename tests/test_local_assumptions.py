import sqlite3
import pytest

from src.repositories.migration_runner import apply_valuation_migration
from src.services.local_assumption_service import LocalAssumptionService
from src.domain.valuation import utc_now_timestamp


@pytest.fixture
def service(tmp_path):
    db = str(tmp_path / "research.db")
    apply_valuation_migration(db)
    return LocalAssumptionService(db)


EPS = dict(fiscal_year=2026, eps_base=100, source="使用者研究", source_date="2026-09-01", rationale="預估年度獲利")
PE = dict(label="基準", pe_value=20, rationale="使用者確認適用的情境")


def draft(service, kind, values, key, previous=None):
    return service.execute("2330.TW", kind, "draft", values, key, previous_id=previous)


def approve(service, kind, record, key, action="approve"):
    return service.execute("2330.TW", kind, action, {"rationale": "確認適用"}, key, resource_id=record["record"]["id"])


def test_explicit_approvals_revision_revoke_and_historical_cutoff(service):
    early = utc_now_timestamp()
    eps = draft(service, "eps", EPS, "draft-eps-1")
    pe = draft(service, "pe", PE, "draft-pe-1")
    assert all(i["approval"] is None for i in service.list("2330.TW")["items"])
    assert service.valuation.analyze("2330.TW", utc_now_timestamp())["status"] != "available"
    approve(service, "eps", eps, "approve-eps-1")
    approve(service, "pe", pe, "approve-pe-1")
    approved_cutoff = utc_now_timestamp()
    assert service.valuation.analyze("2330.TW", approved_cutoff)["status"] == "available"
    revised = draft(service, "eps", {**EPS, "eps_base": 110}, "draft-eps-2", eps["record"]["id"])
    assert revised["record"]["revision_number"] == 2
    assert service.valuation.analyze("2330.TW", utc_now_timestamp())["status"] != "available"
    approve(service, "eps", revised, "approve-eps-2")
    approve(service, "eps", revised, "revoke-eps-2", "revoke")
    assert service.valuation.analyze("2330.TW", utc_now_timestamp())["status"] != "available"
    assert service.valuation.analyze("2330.TW", approved_cutoff)["status"] == "available"
    assert service.valuation.analyze("2330.TW", early)["status"] != "available"


def test_retries_conflict_and_symbol_ownership(service):
    saved = draft(service, "eps", EPS, "stable-key-1")
    assert draft(service, "eps", EPS, "stable-key-1") == saved
    with pytest.raises(ValueError, match="idempotency_conflict"):
        draft(service, "eps", {**EPS, "eps_base": 200}, "stable-key-1")
    with pytest.raises(ValueError, match="not_found_for_symbol"):
        service.execute("2408.TW", "eps", "approve", {"rationale": "x"}, "other-symbol", resource_id=saved["record"]["id"])


def test_resume_after_repository_success_before_command_result(service, monkeypatch):
    original = service.valuation.ingest_forward_eps
    def interrupted(*args):
        original(*args)
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(service.valuation, "ingest_forward_eps", interrupted)
    with pytest.raises(RuntimeError):
        draft(service, "eps", EPS, "resume-key-1")
    monkeypatch.setattr(service.valuation, "ingest_forward_eps", original)
    saved = draft(service, "eps", EPS, "resume-key-1")
    assert saved["record"]["available_at"] == saved["accepted_at"]
    assert len(service.list("2330.TW")["items"]) == 1


def test_anchor_preview_does_not_write_or_approve(service):
    values = dict(rule_id="FB-04", source="手動確認", rationale="已完成上升段", anchors=[
        dict(role="origin", price=100, market_date="2026-08-01"),
        dict(role="swing_end", price=200, market_date="2026-08-20")])
    preview = service.preview("2330.TW", "anchor", values)
    assert preview["calculation"]["calculated_level"] == 161.8
    assert service.list("2330.TW")["items"] == []
    record = draft(service, "anchor", values, "anchor-draft-1")
    assert service.technical.analyze("2330.TW", utc_now_timestamp())["status"] != "available"
    approve(service, "anchor", record, "anchor-approve-1")
    assert service.technical.analyze("2330.TW", utc_now_timestamp())["status"] == "available"
    approve(service, "anchor", record, "anchor-revoke-1", "revoke")
    assert service.technical.analyze("2330.TW", utc_now_timestamp())["status"] != "available"
