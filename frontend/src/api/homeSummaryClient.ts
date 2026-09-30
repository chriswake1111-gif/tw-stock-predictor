import { guidanceRead } from './guidanceClient';

export type HomeSummary = {
  contract_version: 'research_home_summary_v1'; enabled: boolean; symbol: string;
  local_only: true; prepared_at: string; selected_year: number | null;
  baseline: { entry_id: string; created_at: string; knowledge_cutoff_at: string } | null;
  status: string; headline: string;
  dates: { field: string; label: string; current: string | null; previous: string | null }[];
  limitations: { id: string; text: string; impact?: string; owner: string }[];
  next_step: { target: 'research-changes' | 'research-candidates' | 'research-data'; label: string; owner: string };
};

// Shared across mounts: cancelled requests retain their slot until fetch settles.
// Queued requests are dropped on unmount, including rapid page/category changes.
let active = 0;
const queue: (() => void)[] = [];
function drain() {
  while (active < 2 && queue.length) queue.shift()!();
}
export function readHomeSummary(symbol: string, signal: AbortSignal): Promise<HomeSummary> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      const index = queue.indexOf(start);
      if (index >= 0) queue.splice(index, 1);
      reject(new DOMException('Cancelled', 'AbortError'));
    };
    const start = () => {
      signal.removeEventListener('abort', abort);
      if (signal.aborted) { abort(); return; }
      active++;
      void guidanceRead<HomeSummary>(`/api/v2/research/library/${encodeURIComponent(symbol)}/summary`, signal)
        .then(data => {
          if (data.contract_version !== 'research_home_summary_v1' || data.symbol !== symbol || typeof data.enabled !== 'boolean'
              || (data.enabled && (data.local_only !== true || typeof data.headline !== 'string' || !Array.isArray(data.dates)
                  || !Array.isArray(data.limitations) || !['research-changes', 'research-candidates', 'research-data'].includes(data.next_step?.target)))) {
            throw new Error('invalid_home_summary');
          }
          resolve(data);
        }).catch(reject).finally(() => { active--; drain(); });
    };
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    queue.push(start);
    drain();
  });
}

export function localResearchLink(symbol: string, target?: HomeSummary['next_step']['target'], year?: number | null) {
  return `/stocks/${encodeURIComponent(symbol)}?view=local${year ? `&research_year=${year}` : ''}${target ? `#${target}` : ''}`;
}
