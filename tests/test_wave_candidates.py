"""Fixed anonymous source-shaped evidence. All collection is mocked."""
from contextlib import closing
import copy
from datetime import date, timedelta
import json
import sqlite3

import pandas as pd
import pytest

from src.collectors.installed_egress_client import validate_egress_url, EndpointNotAllowlistedError
from src.collectors.wave_candidate_sources import VERSION, EVENT_FIELDS, plan
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.repositories.wave_candidate_repository import WaveCandidateRepository
from src.repositories.wave_session_repository import connection, digest
from src.services.wave_candidate_refresh import refresh
from src.services.wave_candidate_service import WaveCandidateService, qualify, pairs
from src.services.local_assumption_service import LocalAssumptionService
from src.services.research_evidence_service import ResearchEvidenceService
from tests.test_research_guidance import db  # noqa: F401
from tests.test_local_assumption_api import api, csrf  # noqa: F401

SYMBOL = "9911.TW"
BOUNDS = dict(start="2026-09-01", end="2026-09-24")
OBSERVED = "2026-09-26T01:00:00.000000Z"
STOCK_FIELDS = ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數", "註記"]
MARKET_FIELDS = ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"]


@pytest.fixture(autouse=True)
def pilot(monkeypatch):
    monkeypatch.setattr("src.services.wave_candidate_service.PILOT_SCOPE", dict(symbol=SYMBOL, **BOUNDS))


def fixture():
    days = [(date(2026, 9, 1)+timedelta(days=i)) for i in range(24) if (date(2026, 9, 1)+timedelta(days=i)).weekday() < 5]
    prices = [110,109,108,107,106,90,100,108,120,140,125,115,95,110,120,140,130,120]
    rows = [[f"115/09/{d.day:02d}", "1000", "100000", str(p), str(p+1), str(p-1), str(p),
             str(p-prices[i-1]) if i else "0", "100", ""] for i,(d,p) in enumerate(zip(days,prices))]
    sources = []
    for spec in plan(SYMBOL, **BOUNDS):
        key = spec["source_id"]
        if key == "twse_stock":
            payload = dict(stat="OK", date="20260901", title="115年09月 9911 匿名公司 各日成交資訊", fields=STOCK_FIELDS,
                data=rows, total=len(rows), notes=["+/-/X表示漲/跌/不比價", "**為變更面額"])
        elif key == "twse_market":
            payload = dict(stat="OK", date="20260901", fields=MARKET_FIELDS, data=[[r[0], "1000", "100000", "100", "1000", "0"] for r in rows])
        elif key == "twse_holiday":
            payload = dict(stat="OK", date="20260101", queryYear=2026, fields=["日期", "名稱", "說明"], data=[], total=0)
        elif key == "twse_halt":
            payload = dict(stat="OK", params=dict(startDate="20260901",endDate="20260924"), fields=["編號", "證券代號", "證券名稱", "暫停交易日期", "暫停交易時間", "恢復交易日期", "恢復交易時間"],data=[])
        elif key in EVENT_FIELDS:
            payload = dict(stat="OK", fields=EVENT_FIELDS[key], data=[], strDate="20260901", endDate="20260924",
                           params=dict(startDate="20260901",endDate="20260924"))
        else:
            payload = None
        raw = json.dumps(payload, ensure_ascii=False) if payload is not None else "<p>集中市場交易時間為星期一至星期五</p>"
        sources.append(dict(spec=spec, fetched_at=OBSERVED, status="accepted", raw_text=raw, raw_sha256=digest(raw)))
    return dict(version=VERSION, symbol=SYMBOL, range=BOUNDS, price_binding=None, status="accepted", sources=sources)


def mutate(payload, key, fn):
    item = next(s for s in payload["sources"] if s["spec"]["source_id"] == key)
    value = json.loads(item["raw_text"])
    fn(value)
    item["raw_text"] = json.dumps(value, ensure_ascii=False)
    item["raw_sha256"] = digest(item["raw_text"])


def states(checks):
    return {c["id"]: c["status"] for c in checks}


def test_qualification_reconciliation_and_cash_ratio():
    item = fixture()
    rows, checks = qualify(item)
    assert len(rows) == 18 and all(c["status"] == "passed" for c in checks)
    assert len(pairs(rows)) == 1
    event_date = rows[10]["date"]
    mutate(item, "twse_stock", lambda p: p["data"][10].__setitem__(7, "X0.00"))
    event = [event_date, "9911", "匿名", "140", "138", "2", "息", "", "", "138", "138", "", "", "", ""]
    mutate(item, "exright", lambda p: p["data"].append(event))
    adjusted, checks = qualify(item)
    assert all(c["status"] == "passed" for c in checks)
    assert adjusted[5]["low"] == pytest.approx(89*138/140)
    assert adjusted[10]["factor"] == "1" and adjusted[5]["volume"] == 1000
    assert adjusted[5]["raw_low"] == 89
    mutate(item, "exright", lambda p: p["data"][0].__setitem__(4,"139"))
    assert not qualify(item)[0]


@pytest.mark.parametrize("problem", ["missing_stock", "both_missing", "duplicate", "zero", "ohlc", "note", "x", "delta", "identity", "count", "month", "halt", "holiday_conflict", "event", "reduction", "facevalue", "future", "weekly", "no_event_source"])
def test_missing_or_conflicting_evidence_never_returns_candidates(problem):
    item = fixture()
    if problem in {"missing_stock", "both_missing"}:
        def remove(p):
            p["data"].pop(7); p["total"] -= 1
        mutate(item, "twse_stock", remove)
        if problem == "both_missing": mutate(item, "twse_market", lambda p: p["data"].pop(7))
    elif problem == "duplicate": mutate(item, "twse_stock", lambda p: p["data"].__setitem__(7,p["data"][6]))
    elif problem in {"zero", "ohlc", "note", "x", "delta"}:
        position, value = dict(zero=(1,"0"),ohlc=(4,"1"),note=(9,"**"),x=(7,"X0.00"),delta=(7,"999"))[problem]
        mutate(item, "twse_stock", lambda p: p["data"][8].__setitem__(position,value))
    elif problem == "identity": mutate(item,"twse_stock",lambda p:p.update(title="其他股票 9912 公司"))
    elif problem == "count": mutate(item,"twse_stock",lambda p:p.update(total=1))
    elif problem == "month": mutate(item,"twse_market",lambda p:p.update(date="20260801"))
    elif problem == "halt": mutate(item,"twse_halt",lambda p:p["data"].append(["1","9911","匿名","115/09/02","09:00","115/09/02","10:00"]))
    elif problem == "holiday_conflict": mutate(item,"twse_holiday",lambda p:p.update(data=[["2026-09-02","休市","休市一天"]],total=1))
    elif problem in {"event", "reduction", "facevalue"}:
        key={"event":"exright","reduction":"reduction","facevalue":"denomination"}[problem]
        row=["2026-09-02","9911","匿名","110","100"]+["0"]*(len(EVENT_FIELDS[key])-5)
        mutate(item,key,lambda p:p["data"].append(row))
    elif problem == "future": item["sources"][0]["fetched_at"]="2026-09-01T01:00:00.000000Z"
    elif problem == "weekly": item["sources"][-1]["raw_text"]="沒有可核對的交易規則"
    elif problem == "no_event_source": next(s for s in item["sources"] if s["spec"]["source_id"]=="exright")["status"]="failed"
    assert not qualify(item)[0]
    assert "failed" in states(qualify(item)[1]).values()


def test_prefix_replay_tail_ties_and_ambiguous_pivots(monkeypatch):
    rows,_=qualify(fixture())
    engine=WaveFibonacciEngine(config_path="")
    frame=pd.DataFrame(rows)
    complete=engine.detect_confirmed_pivots(frame,lookback=5,confirmation_bars=3)
    for end in range(1,len(rows)+1):
        assert engine.detect_confirmed_pivots(frame.iloc[:end],lookback=5,confirmation_bars=3)==[p for p in complete if p.confirmed_at_index<end]
    assert all(p.confirmed_at_index==p.pivot_index+3 for p in complete)
    frame["low"]=frame["high"]=100
    assert engine.detect_confirmed_pivots(frame)==[]
    from src.engine.wave_fibonacci import ConfirmedPivot
    ambiguous=[ConfirmedPivot("low",5,"2026-09-08",90,8,"2026-09-11"),
               ConfirmedPivot("high",5,"2026-09-08",140,8,"2026-09-11"),
               ConfirmedPivot("high",9,"2026-09-14",150,12,"2026-09-17")]
    monkeypatch.setattr(WaveFibonacciEngine,"detect_confirmed_pivots",lambda *a,**k:ambiguous)
    assert pairs(rows)==[]


def test_append_only_versions_quota_and_read_without_writes(db,monkeypatch):
    repository=WaveCandidateRepository(db)
    item=fixture()
    saved=repository.append(item,"fixture-one")
    assert repository.append(item,"fixture-one")==saved
    before=open(db,"rb").read()
    service=WaveCandidateService(db)
    result=service.get(SYMBOL)
    assert result["status"]=="available" and not result["historical_backtest_eligible"]
    assert open(db,"rb").read()==before
    assert ResearchEvidenceService(db).candidate_values(SYMBOL,result["candidates"][0]["candidate_id"])[0]=="anchor"
    with sqlite3.connect(db) as conn:
        for action in ("UPDATE wave_candidate_packages SET symbol='9912.TW'", "DELETE FROM wave_candidate_packages"):
            with pytest.raises(sqlite3.IntegrityError): conn.execute(action)
    monkeypatch.setattr("src.repositories.wave_candidate_repository.MAX_STORAGE_BYTES",1)
    with pytest.raises(ValueError,match="storage_full"):repository.append(item,"fixture-two")
    assert repository.append(item,"fixture-one")==saved


@pytest.mark.parametrize("status",["failed","revoked"])
def test_latest_failure_blocks_previous_and_stale_draft(db,status):
    repository=WaveCandidateRepository(db)
    item=fixture(); repository.append(item,"fixture-one")
    service=WaveCandidateService(db); first=service.get(SYMBOL)["candidates"][0]
    kind,values=service.candidate_values(SYMBOL,first["candidate_id"])
    repository.append(dict(item,status=status),"fixture-revised")
    assert service.get(SYMBOL)["candidates"]==[]
    with pytest.raises(ValueError,match="changed_review_again"):
        LocalAssumptionService(db).execute(SYMBOL,kind,"draft",values,"draft-stale",candidate_id=first["candidate_id"])


def test_integrity_damage_no_fallback_and_no_missing_database_creation(db,tmp_path):
    repository=WaveCandidateRepository(db); item=fixture()
    repository.append(item,"fixture-one"); repository.append(item,"fixture-two")
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER wave_candidate_packages_no_update")
        conn.execute("UPDATE wave_candidate_packages SET payload_json='{}' WHERE sequence=2")
    assert WaveCandidateService(db).get(SYMBOL)["status"]=="unavailable"
    path=tmp_path/"missing.db"
    assert WaveCandidateService(path).get(SYMBOL)["status"]=="unavailable" and not path.exists()


def test_package_scope_and_flag_off(db,monkeypatch):
    item=fixture(); WaveCandidateRepository(db).append(item,"fixture-one")
    monkeypatch.setattr("src.services.wave_candidate_service.PILOT_SCOPE",dict(symbol=SYMBOL,start="2026-08-01",end="2026-09-24"))
    assert WaveCandidateService(db).get(SYMBOL)["status"]=="unsupported"
    for flag in ("RESEARCH_WAVE_CANDIDATES_ENABLED","RESEARCH_WAVE_ASSIST_ENABLED","RESEARCH_GUIDANCE_ENABLED"):
        with monkeypatch.context() as patch:
            patch.setenv(flag,"false")
            result=WaveCandidateService(db).get(SYMBOL)
            assert not result["enabled"] and result["candidates"]==[]


def test_api_read_preview_draft_replay_separate_approval(api):
    path=str(api.app.state.runtime_settings.paths.database_path)
    WaveCandidateRepository(path).append(fixture(),"fixture-one")
    base=f"/api/v2/research/wave-candidates/{SYMBOL}"
    response=api.get(base)
    assert response.status_code==200 and response.headers["Cache-Control"]=="no-store"
    candidate_id=response.json()["candidates"][0]["candidate_id"]
    detail=api.get(base+"/"+candidate_id).json()
    body={k:detail[k] for k in ("values","candidate_id")}
    url=f"/api/v2/research/assumptions/{SYMBOL}/anchor/"
    assert api.post(url+"preview",json=body,headers=csrf(api)).status_code==200
    assert api.get(f"/api/v2/research/assumptions/{SYMBOL}").json()["items"]==[]
    wrong=copy.deepcopy(body); wrong["values"]["anchors"][0]["price"]+=1
    assert api.post(url+"draft",json=wrong,headers=csrf(api)|{"Idempotency-Key":"mismatch-draft"}).status_code==422
    headers=csrf(api)|{"Idempotency-Key":"explicit-draft"}
    one=api.post(url+"draft",json=body,headers=headers)
    assert one.status_code==200,one.text
    assert api.post(url+"draft",json=body,headers=headers).json()==one.json()
    items=api.get(f"/api/v2/research/assumptions/{SYMBOL}").json()["items"]
    assert len(items)==1 and items[0]["approval"] is None
    WaveCandidateRepository(path).append(dict(fixture(),status="revoked"),"fixture-revoked")
    assert api.post(url+"preview",json=body,headers=csrf(api)).status_code==409
    headers=csrf(api)|{"Idempotency-Key":"explicit-draft"}
    assert api.post(url+"draft",json=body,headers=headers).json()==one.json()
    assert api.post(url+"draft",json=body,headers=csrf(api)|{"Idempotency-Key":"new-stale-draft"}).status_code==409


def test_explicit_refresh_no_network_on_read_preview_reuse(db):
    class Client:
        def __init__(self):self.calls=[]
        def fetch(self,url,**kwargs):
            self.calls.append(url)
            record=next(s for s in fixture()["sources"] if s["spec"]["url"]==url)
            return 200,record["raw_text"].encode(),{}
    client=Client()
    args=dict(request_key="collect-one",client=client)
    assert refresh(db,SYMBOL,**BOUNDS,**args)["status"]=="planned" and not client.calls
    assert refresh(db,SYMBOL,**BOUNDS,execute=True,**args)["status"]=="checked"
    count=len(client.calls)
    assert refresh(db,SYMBOL,**BOUNDS,execute=True,**args)["status"]=="replayed"
    assert refresh(db,SYMBOL,**BOUNDS,execute=True,request_key="collect-two",client=client)["status"]=="retained_recent"
    assert len(client.calls)==count
    assert WaveCandidateService(db).get(SYMBOL)["status"]=="available"


def test_exact_egress_and_sources_required(db):
    for spec in plan(SYMBOL,**BOUNDS):
        assert validate_egress_url(spec["url"])==spec["url"]
        for suffix in ("#x", "&target=https://evil.invalid", "&response=json"):
            with pytest.raises(EndpointNotAllowlistedError):validate_egress_url(spec["url"]+suffix)
    item=fixture();item["sources"].pop()
    with pytest.raises(ValueError,match="incomplete"):WaveCandidateRepository(db).append(item,"bad-sources")


def test_draft_reference_and_saved_research_remain_frozen(db):
    from src.services.daily_research_journal_service import DailyResearchJournalService
    item=fixture(); repository=WaveCandidateRepository(db)
    repository.append(item,"fixture-one")
    service=WaveCandidateService(db)
    candidate=service.get(SYMBOL)["candidates"][0]
    identifier=candidate["candidate_id"]
    kind,values=service.candidate_values(SYMBOL,identifier)
    LocalAssumptionService(db).execute(SYMBOL,kind,"draft",values,"selected-draft",candidate_id=identifier)
    journal=DailyResearchJournalService(db)
    preview=journal.preview(SYMBOL)
    reference=preview["guidance"]["assumption_evidence"][0]["evidence"]
    assert reference["contract_version"]=="wave_candidate_reference_v1"
    saved=journal.save(SYMBOL,preview["knowledge_cutoff_at"],"匿名測試部分研究","explicit-save",
                       expected_content_fingerprint=preview["content_fingerprint"],include_research_context=True)
    repository.append(dict(item,status="revoked"),"fixture-revoked")
    assert service.get(SYMBOL)["candidates"]==[]
    assert ResearchEvidenceService(db).get(SYMBOL,identifier)==reference
    assert journal.history(SYMBOL)["entries"][0]==saved
    assert journal.preview(SYMBOL)["guidance"]["assumption_evidence"][0]["evidence"]==reference
    with pytest.raises(ValueError,match="reference_invalid"):
        service.saved_reference(SYMBOL,identifier[:-1]+("0" if identifier[-1]!="0" else "1"))


def test_new_price_binding_blocks_stale_selection_in_same_read_transaction(db,monkeypatch):
    from src.services import wave_candidate_service as module
    repository=WaveCandidateRepository(db);repository.append(fixture(),"fixture-one")
    service=WaveCandidateService(db)
    first=service.get(SYMBOL)["candidates"][0]["candidate_id"]
    original=module.price_binding
    def changed(conn,*args):
        assert conn.in_transaction
        return dict(snapshot_id="new-snapshot",bounds=BOUNDS)
    monkeypatch.setattr(module,"price_binding",changed)
    assert service.get(SYMBOL)["candidates"]==[]
    with pytest.raises(ValueError,match="changed_review_again"):service.candidate_values(SYMBOL,first)
    monkeypatch.setattr(module,"price_binding",original)
    assert service.get(SYMBOL)["candidates"][0]["candidate_id"]==first


def test_read_transaction_keeps_one_package_during_concurrent_revision(db,monkeypatch):
    from src.services import wave_candidate_service as module
    repository=WaveCandidateRepository(db);item=fixture(); old=repository.append(item,"fixture-one")
    with sqlite3.connect(db) as conn:conn.execute("PRAGMA journal_mode=WAL")
    original=module.price_binding
    def revised(conn,*args):
        assert conn.in_transaction
        repository.append(dict(item,status="revoked"),"concurrent-revision")
        return original(conn,*args)
    monkeypatch.setattr(module,"price_binding",revised)
    result=WaveCandidateService(db).get(SYMBOL)
    assert result["package_ref"]==old["package_ref"] and result["status"]=="available"
    monkeypatch.setattr(module,"price_binding",original)
    assert WaveCandidateService(db).get(SYMBOL)["candidates"]==[]


def test_explicit_approval_and_save_preserve_candidate_classification(db):
    from src.domain.valuation import utc_now_timestamp
    from src.services.daily_research_journal_service import DailyResearchJournalService
    from src.services.technical_scenario_service import TechnicalScenarioService
    WaveCandidateRepository(db).append(fixture(), "approval-fixture")
    candidate = WaveCandidateService(db).get(SYMBOL)["candidates"][0]
    kind, values = WaveCandidateService(db).candidate_values(SYMBOL, candidate["candidate_id"])
    assumptions = LocalAssumptionService(db)
    draft = assumptions.execute(SYMBOL, kind, "draft", values, "approval-draft", candidate_id=candidate["candidate_id"])
    assert TechnicalScenarioService(db).analyze(SYMBOL, utc_now_timestamp())["status"] == "needs_human_input"
    assumptions.execute(SYMBOL, kind, "approve", {"rationale": "匿名測試明確核准"}, "explicit-approval", resource_id=draft["record"]["id"])
    state = assumptions.list(SYMBOL)["items"][0]
    assert state["approval"]["decision"] == "approved" and state["candidate_id"] == candidate["candidate_id"]
    scenarios = TechnicalScenarioService(db).analyze(SYMBOL, utc_now_timestamp())
    assert scenarios["status"] == "available" and scenarios["scenarios"][0]["rule_trace"]["rule_id"] == "FB-04"
    assert WaveCandidateService(db).get(SYMBOL)["evidence_level"] == "C"
    journal = DailyResearchJournalService(db)
    preview = journal.preview(SYMBOL)
    assert not journal.history(SYMBOL)["entries"]
    saved = journal.save(SYMBOL, preview["knowledge_cutoff_at"], "匿名已核准候選測試", "explicit-approved-save",
        expected_content_fingerprint=preview["content_fingerprint"], include_research_context=True)
    assert journal.history(SYMBOL)["entries"][0] == saved
    reference = preview["guidance"]["assumption_evidence"][0]["evidence"]
    assert reference["record_id"] == candidate["candidate_id"] and reference["rule_id"] == "WAVE-CANDIDATE-01"
