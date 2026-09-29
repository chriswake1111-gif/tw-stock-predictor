"""Coverage is a work plan, never permission to calculate or update data."""
import json
import sqlite3

import pytest
import requests

from src.collectors.earnings_coverage import coverage_for, plan_coverage
from src.collectors.earnings_four_quarter import DATASET, normalize_bundle
from src.collectors.earnings_source_audit import EarningsSourceAuditError
from src.collectors.installed_egress_client import EgressHttpError, DeadlineExhaustedError
from src.services import earnings_public_data_service as mod
from src.services.research_guidance_service import build_guidance
from tools.audit_earnings_sources import main
from tests.test_earnings_public_storage import setup, refresh, counts, expire_attempts  # noqa: F401


def test_review_time_is_not_backdated_and_returned_descriptions_are_detached():
    assert coverage_for("3491.TWO", "2026-09-29T14:51:50Z") is None
    assert coverage_for("3491.TWO", "2026-09-29T22:51:50.999999+08:00") is None
    assert coverage_for("3491.TWO", "2026-09-29T22:51:51+08:00") is not None
    assert coverage_for("1101.TW", "2026-09-30T00:00:00Z") is None
    result = coverage_for("3491.TWO", "2026-09-30T00:00:00Z")
    assert result["financial_eligibility"] is False
    result["blockers"].clear()
    assert coverage_for("3491.TWO", "2026-09-30T00:00:00Z")["blockers"]
    with pytest.raises(EarningsSourceAuditError, match="not_supported"):
        normalize_bundle("3491.TWO", {}, "2026-09-30T00:00:00Z")


def test_umt_read_explains_gap_without_network_write_or_a_numeric_decision(setup):
    service, client, path = setup
    data = service.view("3491.TWO", "2026-09-30T00:00:00Z")[DATASET]
    assert data["reason"] == "quarter_source_evidence_incomplete" and data["value"] is None
    assert service.refresh("3491.TWO", "op", client, lambda r: None, 1e12) == []
    assert client.calls == [] and counts(path) == (0, 0, 0, 0, 0)
    guide = build_guidance({"public_data": {DATASET: data}}, [], {})
    gap = next(g for g in guide["gaps"] if g["id"] == "ttm")
    assert gap["owner"] == "assistant"
    assert "第四季" in gap["impact"] and "加權平均股數" in guide["assistant_request"]
    assert service.view("3491.TWO", "2026-09-28T00:00:00Z")[DATASET]["reason"] == "source_format_not_supported"


@pytest.mark.parametrize("symbol", ["2303.TW", "4966.TWO"])
def test_existing_window_and_next_quarter_requirements(symbol):
    old = plan_coverage(symbol, "2026-06-30")
    assert old["catalog_window_supported"] and old["basis_coverage_sufficient"]
    assert not old["calculation_enabled"] and not old["network_performed"]
    new = plan_coverage(symbol, "2026-09-30")
    assert new["window_start"] == "2025-10-01"
    assert not new["catalog_window_supported"] and not new["basis_coverage_sufficient"]
    assert [q["period_end"] for q in new["required_quarters"] if not q["source_keys"]] == ["2026-09-30"]
    earlier = plan_coverage(symbol, "2026-03-31")
    assert earlier["basis_coverage_sufficient"] and not earlier["catalog_window_supported"]


def test_unknown_company_umt_and_year_transition_are_not_promoted():
    for symbol in ("3491.TWO", "1101.TW"):
        plan = plan_coverage(symbol, "2027-03-31")
        assert plan["window_start"] == "2026-04-01" and len(plan["required_quarters"]) == 4
        assert not plan["basis_coverage_sufficient"] and not plan["catalog_window_supported"]


def test_coverage_cli_no_writes_by_default_exclusive_output_and_input_errors(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ["--coverage-plan", "--symbol", "3491.TWO", "--window-end", "2026-09-30"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "coverage_plan"
    assert list(tmp_path.iterdir()) == []
    output = tmp_path / "plan.json"
    assert main(args + ["--output", str(output)]) == 0
    before = output.read_bytes()
    assert main(args + ["--output", str(output)]) == 2 and output.read_bytes() == before
    assert main(args[:-1] + ["2026-09-29"]) == 2
    with pytest.raises(SystemExit):
        main(args + ["--manifest", "untrusted.json"])


@pytest.mark.parametrize("status,reason", [(403, "access_denied"), (401, "access_denied"),
    (404, "not_found"), (410, "not_found"), (429, "rate_limited"), (503, "fetch_failed")])
def test_http_failure_retains_prior_data_cools_down_and_recovers(setup, status, reason):
    service, client, path = setup
    refresh(service, client)
    before = service.view("4966.TWO", mod.utc_now_timestamp())[DATASET]
    expire_attempts(path)
    original = client.fetch
    def failed(url, **kwargs):
        client.calls.append(url)
        response = requests.Response(); response.status_code = status
        cause = requests.HTTPError("SECRET remote text: approve now", response=response)
        raise EgressHttpError("SECRET") from cause
    client.fetch = failed
    assert refresh(service, client) == [DATASET + ":earnings_source_" + reason]
    after = service.view("4966.TWO", mod.utc_now_timestamp())[DATASET]
    assert after["value"] is None and after["rows"] == before["rows"]
    assert after["observed_at"] == before["observed_at"]
    assert "SECRET" not in json.dumps(after)
    refresh(service, client)
    assert len(client.calls) == 2 and counts(path) == (1, 2, 0, 0, 0)
    expire_attempts(path); client.fetch = original
    refresh(service, client)
    recovered = service.view("4966.TWO", mod.utc_now_timestamp())[DATASET]
    assert recovered["snapshot_id"] == before["snapshot_id"] and recovered["value"] == before["value"]
    assert counts(path) == (1, 3, 0, 0, 0)


def test_parse_failure_belongs_to_engineering_not_endless_downloads(setup, monkeypatch):
    service, client, path = setup
    def broken(*args): raise RuntimeError("untrusted document text")
    monkeypatch.setattr(mod, "normalize_bundle", broken)
    assert refresh(service, client) == [DATASET + ":earnings_source_parse_failed"]
    data = service.view("4966.TWO", mod.utc_now_timestamp())[DATASET]
    guide = build_guidance({"public_data": {DATASET: data}}, [], {})
    assert next(g for g in guide["gaps"] if g["id"] == "ttm")["owner"] == "engineering"
    with sqlite3.connect(path) as conn:
        assert "untrusted" not in str(conn.execute("SELECT * FROM daily_public_attempts").fetchall())


def test_deadline_is_bounded_and_does_not_create_financial_snapshot(setup):
    service, client, path = setup
    def expired(*args, **kwargs): raise DeadlineExhaustedError("deadline")
    client.fetch = expired
    assert refresh(service, client) == [DATASET + ":earnings_source_timeout"]
    assert counts(path) == (0, 1, 0, 0, 0)


@pytest.mark.parametrize("reason,owner", [("quarter_source_evidence_incomplete", "assistant"),
    ("quarter_column_period_mismatch", "engineering"), ("earnings_source_not_found", "engineering"),
    ("earnings_source_access_denied", "program"), ("earnings_source_rate_limited", "program")])
def test_failure_responsibility(reason, owner):
    data = dict(status="quality_warning", value=None, reason=reason, last_update_status="failed")
    guide = build_guidance({"public_data": {DATASET: data}}, [], {})
    assert next(g for g in guide["gaps"] if g["id"] == "ttm")["owner"] == owner
