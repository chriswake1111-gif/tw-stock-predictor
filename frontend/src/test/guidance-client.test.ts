import { expect, it, vi } from 'vitest';
import { guidanceRead } from '../api/guidanceClient';

it('preserves a known candidate revision error for actionable UI feedback', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'research_evidence_changed_review_again' }), { status: 409 }));
  await expect(guidanceRead('/api/v2/research/evidence/2330.TW/example/candidate')).rejects.toThrow('research_evidence_changed_review_again');
});

it('does not expose arbitrary source or server error text to the guided UI', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'unexpected internal data: <script>...</script>' }), { status: 500 }));
  await expect(guidanceRead('/api/v2/research/evidence/2330.TW')).rejects.toThrow('既有研究仍保留');
});
