import type { Evidence, WaveSupport } from '../api/guidanceClient';
import { evidenceFixture } from './researchGuidanceFixture';

export const waveFixture: WaveSupport = {
  contract_version: 'wave_anchor_guidance_v1', status: 'quality_warning', price_basis: '未還原權息的每日價格', source: 'FinMind',
  first_date: '2026-01-02', last_date: '2026-01-20', observed_at: '2026-01-21T00:00:00Z', checked_at: '2026-01-21T00:00:00Z',
  blockers: ['尚未核對區間內的完整交易日與停牌紀錄。', '尚未核對除權息及其他公司行動造成的價格跳動。'],
  next_action: '資料能力待補強；可先閱讀有來源的人工候選或保存部分研究。',
  confirmation_policy: '轉折發生日與確認時點必須分開。', approval_policy: '核准只接受人工假設，不會補足交易日證據。',
  automatic_candidates_eligible: false, snapshot_id: 'synthetic-price', source_sha256: 'a'.repeat(64),
};
export const anchorFixture: Evidence = {
  ...evidenceFixture, topic: 'anchor', fiscal_year: null, value: null, unit: 'TWD', title: '匿名來源波段候選',
  summary: '以兩個歷史價位作為人工假設。', basis: '未還原價格；非自動確認', limitations: '價格口徑與波段解讀仍有不確定性。',
  rule_id: 'FB-04', anchors: [{ role: 'origin', market_date: '2026-01-02', price: 100 }, { role: 'swing_end', market_date: '2026-01-20', price: 150 }],
};
