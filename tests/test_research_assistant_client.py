import json
import os
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.research_assistant.cli import compact, execute, parser
from src.research_assistant.client import AssistantError, CONTRACT, LocalClient, NoRedirect, local_origin, request_key, atomic_json


@pytest.fixture
def client(tmp_path):
    c = LocalClient(tmp_path / "安裝 空白", tmp_path / "使用者 資料")
    c.origin = "http://127.0.0.1:1234"
    c.descriptor = {"launch_id":"test-launch", "build_sha":"abc"}
    return c


@pytest.mark.parametrize("origin", ["https://127.0.0.1:1234", "http://example.com:1234", "http://127.0.0.1:1234/path", "http://u:p@127.0.0.1:1234", "http://127.0.0.1:1234?url=x"])
def test_nonlocal_or_ambiguous_origin_rejected(origin):
    with pytest.raises(AssistantError): local_origin(origin)


def test_redirect_never_followed():
    with pytest.raises(AssistantError, match="redirect_refused"):
        NoRedirect().redirect_request(None,None,302,None,None,"http://external.invalid")


def test_request_ids_reject_header_text_and_require_stable_uuid():
    for value in (None, "arbitrary8", "bad\r\nheader"):
        with pytest.raises(AssistantError): request_key(value)
    assert request_key("01900000-0000-4000-8000-000000000001") == "01900000-0000-4000-8000-000000000001"


def test_receipt_retention_bound_preserves_existing_files(tmp_path,monkeypatch):
    monkeypatch.setattr("src.research_assistant.client.MAX_RECEIPT_FILES",1)
    atomic_json(tmp_path/"first.json",{"retained":True})
    with pytest.raises(AssistantError,match="storage_full"):
        atomic_json(tmp_path/"second.json",{"new":True})
    assert json.loads((tmp_path/"first.json").read_text()) == {"retained":True}
    assert len(list(tmp_path.iterdir()))==1


def test_read_retry_once_write_never_automatically_retried(client):
    calls=[]
    def failing(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError("secret-url")
    client.opener = SimpleNamespace(open=failing)
    with pytest.raises(AssistantError, match="local_connection_failed"): client._http("/api/ready")
    assert len(calls)==2
    calls.clear()
    with pytest.raises(AssistantError, match="write_response_unknown"): client._http("/api/v2/research/bootstrap",body={})
    assert len(calls)==1


def stock(symbol="2330.TW", name="台積電", security="股票"):
    return {"canonical_symbol":symbol, "official_code":symbol.split('.')[0], "security_type":security, "short_name":name}


@pytest.mark.parametrize("symbol", ["2330.TW", "2408.TW", "6488.TWO"])
def test_exact_resolved_ordinary_stock(client,symbol):
    client.search=lambda q:{"results":[stock(symbol)],"total_matches":1}
    assert client.resolve(symbol)[0]==symbol


def test_evidence_commands_check_capability_and_encode_scope(client):
    calls = []
    def http(path, **kwargs):
        calls.append((path, kwargs))
        return {"research_guidance_contract":"research_guidance_v1"} if path == "/api/ready" else {"items":[]}
    client._http = http
    client.evidence("2330.TW", history=True, before="evidence_cursor")
    assert "history=true&before=evidence_cursor" in calls[-1][0]
    client.evidence_reuse("2330.TW", "2026 全年估值", 2026, force=True)
    assert "force=true" in calls[-1][0] and "fiscal_year=2026" in calls[-1][0]
    client.evidence_candidate("2330.TW", "evidence_abc")
    assert calls[-1][0].endswith("/evidence_abc/candidate")
    client._http = lambda *a, **k: {}
    for command in (lambda:client.evidence("2330.TW"), lambda:client.evidence_reuse("2330.TW","scope"), lambda:client.evidence_candidate("2330.TW","evidence_abc")):
        with pytest.raises(AssistantError, match="upgrade_required"):
            command()


def test_wave_candidate_cli_only_reads_typed_local_routes(client):
    calls=[]
    client._http=lambda path,**kwargs:(calls.append((path,kwargs)) or {"wave_candidates_contract":"wave_candidates_v1"})
    client.wave_candidates("2330.TW")
    client.wave_candidates("2330.TW","wc_fixture")
    assert calls[-1]==("/api/v2/research/wave-candidates/2330.TW/wc_fixture",{})
    assert all(not kw for _,kw in calls)
    args=parser().parse_args(["wave-candidates","2330.TW","--id","wc_fixture"])
    assert args.command=="wave-candidates" and not hasattr(args,"confirmed")
    client._http=lambda *a,**k:{}
    with pytest.raises(AssistantError,match="wave_candidates_upgrade_required"):client.wave_candidates("2330.TW")


def test_guided_save_preserves_exact_review_and_separate_confirmation(client):
    from src.research_assistant.cli import parser
    args = parser().parse_args(["evidence-record", "2330.TW", "--input", "work.json", "--request-id", "01900000-0000-4000-8000-000000000001"])
    assert args.command == "evidence-record" and not hasattr(args, "confirmed")
    assert parser().parse_args(["review", "2330.TW", "--year", "2026"]).year == 2026
    with pytest.raises(SystemExit):
        parser().parse_args(["approve", "2330.TW"])
    recorded = []
    client.mutate = lambda path, body, key: recorded.append((path,body,key)) or {}
    reviewed = {"contract_version":"tw_stock_research_assistant_v1", "symbol":"2330.TW",
        "content_fingerprint":"a"*64, "knowledge_cutoff_at":"2026-01-01T00:00:00Z",
        "guidance":{"contract_version":"research_guidance_v1","selected_year":2026}}
    client.save(reviewed, "原文與草稿分開", "01900000-0000-4000-8000-000000000001")
    assert recorded[0][1]["include_research_context"] is True
    assert recorded[0][1]["research_year"] == 2026
    assert recorded[0][1]["expected_content_fingerprint"] == reviewed["content_fingerprint"]


def test_ambiguous_and_unsupported_do_not_trigger_update(client):
    client.search=lambda q:{"results":[stock(),stock("2408.TW")],"total_matches":2}
    assert client.research("台")["status"]=="needs_selection"
    client.search=lambda q:{"results":[stock("0050.TW",security="ETF")],"total_matches":1}
    assert client.research("0050")["status"]=="unsupported_instrument"


def test_partial_failure_keeps_cached_review(client):
    client.resolve=lambda q:("2330.TW",stock())
    client.review=lambda s:{"symbol":s,"current":{"status":"partial"}}
    def fail(*a, **k): raise AssistantError("source_unavailable")
    client.update=fail
    result=client.research("台積電")
    assert result["review"]["current"]["status"]=="partial"
    assert result["update"]["reason"]=="source_unavailable"


def test_wait_has_deadline_and_does_not_cancel(client):
    now=[0.0]
    client.clock=lambda:now[0]
    client.sleep=lambda n:now.__setitem__(0,now[0]+n)
    client._http=lambda *a,**k:{"status":"running","operation_id":"op1"}
    result=client.wait("op1",seconds=5)
    assert now[0]==5 and result["wait_status"]=="timeout"
    with pytest.raises(AssistantError):client.wait("op1",seconds=121)


def test_cancel_only_explicitly_created_operation_same_launch(client):
    with pytest.raises(AssistantError,match="not_created"):client.cancel("shared")
    client._remember_operation({"operation_id":"mine","operation_created":True},"2330.TW")
    client.operation=lambda op:{"status":"running","target_symbols":["2330.TW"]}
    calls=[]
    client.mutate=lambda path,body:calls.append(body) or {"status":"cancelling"}
    assert client.cancel("mine")["status"]=="cancelling"
    assert calls==[{"expected_operation_id":"mine"}]
    client.descriptor["launch_id"]="another-launch"
    with pytest.raises(AssistantError,match="instance_changed"):client.cancel("mine")


def test_cli_has_no_approval_or_generic_http_commands():
    p=parser()
    for command in (["approve"], ["request","http://external"], ["save","--review","a","--note-file","b","--request-id","12345678"]):
        with pytest.raises(SystemExit):p.parse_args(command)


def test_pe_preview_requires_server_year_pairing_capability(client):
    calls = []
    client.mutate = lambda path, body, key: calls.append((path, body, key)) or {"status": "preview_only"}
    client._http = lambda path: {"ready": True, "research_assistant_contract": CONTRACT}
    payload = {"values": {"fiscal_year": 2027, "pe_value": 20, "label": "test", "rationale": "custom"}}
    with pytest.raises(AssistantError, match="pe_fiscal_year_upgrade_required"):
        client.assumption("2330.TW", "pe", payload)
    assert calls == []
    client._http = lambda path: {"ready": True, "valuation_pairing_policy": "same_fiscal_year_v1"}
    assert client.assumption("2330.TW", "pe", payload)["status"] == "preview_only"
    assert calls[0][1]["values"]["fiscal_year"] == 2027


def test_doctor_exposes_pairing_capability_without_starting(client):
    starts = []
    client.connect = lambda *, start: starts.append(start) or {"valuation_pairing_policy": "same_fiscal_year_v1"}
    client.active_operation = lambda: None
    result = execute(parser().parse_args(["doctor"]), client)
    assert starts == [False]
    assert result["valuation_pairing_policy"] == "same_fiscal_year_v1"


def test_compact_preserves_provenance_and_explicitly_marks_omitted_rows():
    result=compact({"dataset":{"source":"FinMind","status":"quality_warning","rows":[{"close":1},{"close":2}]}})["dataset"]
    assert result=={"source":"FinMind","status":"quality_warning","row_count":2,"latest_rows":[{"close":2}],"rows_omitted":1}


def test_connect_checks_build_and_capability_before_research(client):
    root=client.install_root / "tw-stock-predictor"
    (root / "_internal").mkdir(parents=True)
    (root / "tw-stock-predictor.exe").touch()
    (root / "_internal/package-manifest.json").write_text(json.dumps({"build_sha":"abc"}))
    runtime=client.user_root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "instance.json").write_text(json.dumps({"origin":"http://127.0.0.1:1234","host":"127.0.0.1","port":1234,"build_sha":"abc"}))
    client.ownership=lambda d,expected_build_sha:(True,"ok")
    client._http=lambda p:{"ready":True,"origin":"http://127.0.0.1:1234","build_sha":"abc"}
    with pytest.raises(AssistantError,match="upgrade_required"):client.connect()
    client._http=lambda p:{"ready":True,"origin":"http://127.0.0.1:1234","build_sha":"abc","research_assistant_contract":CONTRACT}
    assert client.connect()["status"]=="ready"
    client.ownership=lambda *a,**k:(False,"server_parent_identity_mismatch")
    with pytest.raises(AssistantError,match="parent_identity"):client.connect()


def test_diagnosis_can_recover_active_id_after_update_response_lost(client):
    client._http=lambda path:{"active_operation":{"operation_id":"existing","target_symbols":["2330.TW"]}}
    assert client.active_operation()["operation_id"]=="existing"


def test_missing_installation_never_starts_a_process(client,monkeypatch):
    calls=[]
    monkeypatch.setattr("src.research_assistant.client.subprocess.Popen",lambda *a,**k:calls.append(1))
    with pytest.raises(AssistantError,match="installation_missing"):client.connect(start=True)
    assert calls==[]


@pytest.mark.skipif(os.name != "nt", reason="Windows hidden launch contract")
def test_start_missing_instance_uses_hidden_existing_launcher_and_explicit_user_root(client,monkeypatch):
    root=client.install_root/"tw-stock-predictor"
    (root/"_internal").mkdir(parents=True)
    (root/"tw-stock-predictor.exe").touch()
    (root/"_internal/package-manifest.json").write_text(json.dumps({"build_sha":"abc"}))
    calls=[]
    def spawn(command,**kwargs):
        calls.append((command,kwargs))
        runtime=client.user_root/"runtime"
        runtime.mkdir(parents=True)
        (runtime/"instance.json").write_text(json.dumps({"origin":"http://127.0.0.1:1234","host":"127.0.0.1","port":1234,"build_sha":"abc"}))
    monkeypatch.setattr("src.research_assistant.client.subprocess.Popen",spawn)
    client.ownership=lambda *a,**k:(True,"ok")
    client._http=lambda p:{"ready":True,"origin":"http://127.0.0.1:1234","build_sha":"abc","research_assistant_contract":CONTRACT}
    with pytest.raises(AssistantError,match="not_running"):client.connect()
    assert calls==[]
    assert client.connect(start=True)["status"]=="ready"
    assert calls[0][0]==[str(root/"tw-stock-predictor.exe"),"--user-root",str(client.user_root)]
    assert calls[0][1]["creationflags"] != 0
    assert len(calls)==1


@pytest.mark.parametrize("command", ["doctor", "connect"])
def test_cli_reports_guidance_capability(command):
    client = SimpleNamespace(
        connect=lambda **kwargs: {"research_guidance_contract": "research_guidance_v1"},
        origin="http://127.0.0.1:12345",
        descriptor={"build_sha": "test"},
        active_operation=lambda: None,
    )
    result = execute(SimpleNamespace(command=command), client)
    assert result["research_guidance_contract"] == "research_guidance_v1"
