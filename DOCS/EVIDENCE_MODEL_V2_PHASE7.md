# Evidence Model V2 Phase 7

Phase 7 adds profile-governed scenario synthesis and append-only analysis snapshots. It does not
add price prediction probabilities, BUY/SELL recommendations, a broker connection, or a Phase 8
evaluator.

## TGT-01 governance

`TGT-01` remains evidence level C, `project_operationalization`, and requires an exact human
approval of the exact synthesis profile revision. Governance metadata comes only from
`config/model_rules.yaml` through `RuleRegistry`.

The first implementation accepts only these trusted upstream candidate families:

| Family | Role | Source |
| --- | --- | --- |
| VAL-01 | target | approved Phase 2 Forward EPS x symbol PE matrix cell |
| FB-03 | target | approved Phase 4 equal-move scenario |
| FB-04 | support | approved Phase 4 0.382 retracement scenario |

Phase 3 liquidity, Phase 5 deployment, and Phase 6 screening are snapshot context only. They do
not create a target candidate or increase target evidence strength.

## Counting and independence

- `candidate_count` is the number of eligible target candidates in a cluster.
- `support_count` is the number of distinct eligible target method families in the cluster.
- `independent_method_count` is the connected-component count after different target families
  sharing an authoritative upstream revision are connected.
- Multiple VAL-01 matrix cells remain one method family.
- FB-04 participates only in `cross_role_alignment`.
- Fewer than two independent target components produces no TGT-01 confluence, a null
  `evidence_strength`, and no TGT-01 Rule Trace.

All disjoint overlap clusters are retained. Top-level count and strength fields are summary values
only and disclose `summary_policy=maximum_cluster_strength`. The system does not emit a primary,
recommended, or automatically selected target.

## Approved synthesis profile

The immutable profile revision stores the allowed families, relative point-to-range tolerance,
closed-boundary maximal-active-set clustering policy, connected-component dependency policy,
Decimal precision, rounding policy, and evidence-strength thresholds. Thresholds are not hard
coded in the engine and every valid threshold must start at two independent target components.

An explicit old revision cannot be selected after a newer cutoff-visible revision exists. A new
revision requires a new approval. The latest revoke does not fall back to an older approval or
revision. If selection is omitted, zero profiles requires human input and multiple applicable
profiles requires explicit selection; there is no hidden symbol/global/latest precedence.

## Immutable analysis snapshot

`POST /api/v2/analysis/{symbol}/refresh` computes the same additive analysis used by the GET path
and inserts an immutable snapshot. The snapshot stores the exact output, knowledge cutoff,
capture mode, model version, exact synthesis profile revision and approval, used rule versions,
lossless contributing resource references, approval IDs, output SHA-256, semantic fingerprint,
creation time, and optional explicit same-symbol supersedes link.

SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers reject changes to `analysis_snapshots`.
`GET /api/v2/analysis/snapshots/{snapshot_id}` verifies the stored output hash and returns the
stored output without recomputation.

The server assigns capture mode:

- no caller-supplied cutoff: `live_refresh`;
- explicit timestamp or date cutoff: `historical_reconstruction`.

A historical reconstruction is not evidence that the snapshot was actually published at the
historical cutoff. Phase 8 evaluation is not implemented.

## API and write boundary

GET analysis remains side-effect free. Profile writes, approvals, and refresh persistence are
disabled unless `EVIDENCE_V2_WRITES_ENABLED=true`, and require the configured admin API key.
The server supplies the approval actor. Arbitrary target candidates, caller-supplied capture mode,
caller-supplied evidence metadata, and internal fingerprints are not accepted or exposed.

## Known limitations

- Semantic contradiction is not yet deterministic; `contradicting_methods` remains empty and the
  result discloses this limitation.
- Only the approved Phase 2 and Phase 4 sources above enter synthesis.
- Evidence strength describes approved-policy method overlap, not probability, expected return,
  accuracy, or validation performance.
- INV-01 is not implemented. Existing upstream invalidation strings are retained as context but
  are not presented as a complete approved invalidation checklist.
