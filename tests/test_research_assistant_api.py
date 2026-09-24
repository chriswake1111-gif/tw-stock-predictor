from tests.test_local_assumption_api import api, csrf, EPS  # reuse installed-security fixture


def test_preview_guard_and_legacy_save_compatibility(api):
    reviewed = api.get("/api/v2/research/journal/2330.TW/preview")
    assert reviewed.status_code == 200
    p = reviewed.json()
    assert p["contract_version"] == "tw_stock_research_assistant_v1"
    assert p["comparison"]["status"] == "no_previous"
    request = {"knowledge_cutoff_at":p["knowledge_cutoff_at"], "note":"確認部分研究",
               "expected_content_fingerprint":p["content_fingerprint"]}
    headers=csrf(api) | {"Idempotency-Key":"assistant-api-save"}
    first=api.post("/api/v2/research/journal/2330.TW",json=request,headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["summary"] == p["current"]["summary"]
    retry=api.post("/api/v2/research/journal/2330.TW",json=request,headers=headers)
    assert retry.json()==first.json()
    legacy=api.post("/api/v2/research/journal/2330.TW",json={"knowledge_cutoff_at":p["knowledge_cutoff_at"],"note":"原介面"},headers=csrf(api)|{"Idempotency-Key":"legacy-compatible"})
    assert legacy.status_code==200


def test_assumption_change_returns_409_and_keeps_review_unsaved(api):
    p=api.get("/api/v2/research/journal/2330.TW/preview").json()
    draft=api.post("/api/v2/research/assumptions/2330.TW/eps/draft",json=EPS,
                   headers=csrf(api)|{"Idempotency-Key":"assistant-new-draft"})
    assert draft.status_code==200
    result=api.post("/api/v2/research/journal/2330.TW",json={"knowledge_cutoff_at":p["knowledge_cutoff_at"],"note":"stale", "expected_content_fingerprint":p["content_fingerprint"]},
                    headers=csrf(api)|{"Idempotency-Key":"assistant-stale-save"})
    assert result.status_code==409 and result.json()["detail"]=="research_content_changed_review_again"
    assert api.get("/api/v2/research/journal/2330.TW").json()["entries"]==[]


def test_new_preview_and_save_retain_local_security(api):
    path="/api/v2/research/journal/2330.TW"
    p=api.get(path+"/preview").json()
    body={"knowledge_cutoff_at":p["knowledge_cutoff_at"],"expected_content_fingerprint":p["content_fingerprint"]}
    assert api.post(path,json=body,headers={"Idempotency-Key":"without-csrf"}).status_code==403
    assert api.post(path,json=body,headers=csrf(api)|{"Origin":"http://evil.invalid","Idempotency-Key":"wrong-origin"}).status_code==403
