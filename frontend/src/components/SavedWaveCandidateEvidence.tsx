import type { WaveCandidateReference } from '../api/guidanceClient';

export function SavedWaveCandidateEvidence({ evidence }: { evidence: WaveCandidateReference }) {
  return <article className="guidance-source">
    <h3>{evidence.title}</h3>
    <p>這是該份人工假設原先引用的程式候選版本，並非外部分析師報告。後來的證據更新不會改寫這份引用。</p>
    <p>完整查核期間：{evidence.requested_range.start} 至 {evidence.requested_range.end} · 價格基準：{evidence.basis_date}</p>
    <p>證據取得：{evidence.known_at} · 規則：{evidence.rule_id}／{evidence.algorithm_version}</p>
    <ul>{evidence.limitations.map(item => <li key={item}>{item}</li>)}</ul>
    <details><summary>原始來源版本與校驗資料</summary>
      <p>資料包：{evidence.package_ref} · 校驗值：{evidence.content_sha256}</p>
      <ul>{evidence.sources.map(source => <li key={source.url}>
        <p>{source.source_id} · {source.fetched_at}</p><p>{source.url}</p><p>{source.raw_sha256}</p>
      </li>)}</ul>
    </details>
  </article>;
}
