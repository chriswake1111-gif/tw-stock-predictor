"""Wave support must never turn unqualified prices into confirmed pivots."""
import copy
import pytest
from src.services.wave_anchor_guidance import wave_support
from src.services.research_guidance_service import build_guidance
from src.services.local_assumption_service import LocalAssumptionService
from src.services.research_evidence_service import ResearchEvidenceService
from tests.test_research_guidance import db, CANDIDATE, SYMBOL  # noqa: F401

ANCHOR = dict(CANDIDATE, topic='anchor', fiscal_year=None, value=None, unit='TWD', rule_id='FB-04',
    basis='人工來源的未還原價格；非自動判浪', anchors=[
        dict(role='origin', market_date='2026-01-02', price=100),
        dict(role='swing_end', market_date='2026-01-20', price=150)])


def test_missing_and_unqualified_prices_cannot_be_promoted():
    missing = wave_support({})
    assert missing['owner'] == 'program' and missing['status'] == 'insufficient_data'
    data = dict(source='FinMind', dataset='TaiwanStockPrice', status='available', verified=True,
                automatic_candidates_eligible=True, calendar_verified=True, adjusted=True,
                rows=[dict(date='2026-01-02', volume=0, zero_volume=True)],
                last_update_status='failed', is_stale=True, observed_at='2026-01-03T00:00:00Z')
    result = wave_support({'public_data': {'TaiwanStockPrice': data}})
    assert result['owner'] == 'engineering'
    assert result['automatic_candidates_eligible'] is False and result['automatic_candidates'] == []
    assert result['first_date'] == result['last_date'] == '2026-01-02'
    assert result['price_basis'] == '未還原權息的每日價格'
    for reason in ('零成交量', '更新失敗', '過期', '交易日', '除權息'):
        assert any(reason in text for text in result['blockers'])


def test_unknown_source_integrity_and_feature_rollback(monkeypatch):
    result = wave_support({'public_data': {'TaiwanStockPrice': {'reason': 'snapshot_integrity_error'}}})
    assert result['owner'] == 'engineering'
    assert '尚未核對' in result['price_basis']
    assert any('檢查失敗' in text for text in result['blockers'])
    monkeypatch.setenv('RESEARCH_WAVE_ASSIST_ENABLED', 'false')
    assert wave_support({}) is None


def test_manual_candidates_max_three_without_source_promotion(db):
    evidence = ResearchEvidenceService(db)
    for n in range(4):
        evidence.append(SYMBOL, dict(ANCHOR, title=f'人工候選 {n}'), f'anchor-candidate-{n}')
    summary = dict(canonical_symbol=SYMBOL, public_data={})
    guidance = build_guidance(summary, [], evidence.guidance_evidence(SYMBOL))
    assert len(guidance['candidates']) == 3
    assert any(g['id'] == 'anchor' and g['owner'] == 'user' for g in guidance['gaps'])
    assert guidance['wave_support']['automatic_candidates_eligible'] is False
    assert guidance['candidates'][0]['anchors'] == ANCHOR['anchors']
    assert '確認時點未提供' in guidance['assistant_request']


def test_wave_preview_draft_revision_replay_and_explicit_approval(db):
    evidence = ResearchEvidenceService(db)
    assumptions = LocalAssumptionService(db)
    first = evidence.append(SYMBOL, ANCHOR, 'anchor-first-source')
    kind, values = evidence.candidate_values(SYMBOL, first['record_id'])
    assumptions.preview(SYMBOL, kind, values)
    assert assumptions.list(SYMBOL)['items'] == []
    result = assumptions.execute(SYMBOL, kind, 'draft', values, 'anchor-draft-request', candidate_id=first['record_id'])
    row = assumptions.list(SYMBOL)['items'][0]
    assert row['approval'] is None
    replacement = copy.deepcopy(ANCHOR)
    replacement['anchors'][1]['price'] = 160
    evidence.append(SYMBOL, dict(replacement, previous_id=first['record_id']), 'anchor-revised-source')
    with pytest.raises(ValueError, match='changed_review_again'):
        evidence.candidate_values(SYMBOL, first['record_id'])
    with pytest.raises(ValueError, match='changed_review_again'):
        assumptions.execute(SYMBOL, kind, 'draft', values, 'anchor-stale-draft', candidate_id=first['record_id'])
    assert assumptions.execute(SYMBOL, kind, 'draft', values, 'anchor-draft-request', candidate_id=first['record_id']) == result
    # Existing drafts retain their source, never inherit approval from a revision.
    assumptions.execute(SYMBOL, kind, 'approve', {'rationale': '隔離測試中模擬使用者核准原假設'}, 'anchor-user-approval', resource_id=row['id'])
    assert assumptions.list(SYMBOL)['items'][0]['approval']['decision'] == 'approved'
    assert wave_support({})['automatic_candidates_eligible'] is False


def test_future_or_blocked_wave_candidate_not_selectable(db):
    evidence = ResearchEvidenceService(db)
    future = copy.deepcopy(ANCHOR)
    future['anchors'][1]['market_date'] = '2200-01-01'
    record = evidence.append(SYMBOL, future, 'future-anchor-source')
    with pytest.raises(ValueError, match='available_at'):
        evidence.candidate_values(SYMBOL, record['record_id'])
    with pytest.raises(ValueError, match='unread_or_conflicting'):
        evidence.append(SYMBOL, dict(ANCHOR, source_access='blocked'), 'blocked-anchor-source')
