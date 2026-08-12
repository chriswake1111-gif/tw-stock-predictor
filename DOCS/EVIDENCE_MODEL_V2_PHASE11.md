# Evidence Model V2 Phase 11 — Research Lifecycle Foundation

## Scope

Phase 11 adds a read-only, deterministic comparison layer for immutable Phase 7 analysis snapshots. It answers two separate questions without rerunning an analysis model:

1. What stored facts differ between the selected snapshots?
2. Under one explicit knowledge cutoff, how do their currently effective dependencies differ?

This phase does not add a database migration, collector, write API, automatic approval, trading integration, or Phase 12 workflow.

## Comparison contract

- Contract: `analysis_snapshot_v1`
- Policy: `1.0`
- Direction: base snapshot to comparison snapshot
- Numeric delta: comparison minus base
- Ordering: deterministic category, section, resource type, canonical identity, field path, and change type order
- Missing is represented by an explicit sentinel and is distinct from JSON `null`.
- Decimal values and timezone-aware timestamps are canonicalized before comparison.
- Unknown or incompatible snapshot contracts fail closed as `incomparable_contract`.
- Read-time validation requires the persisted envelope, envelope/output identity,
  Phase 7-10 section containers, and registered comparison collections to match
  `analysis_snapshot_v1`. Malformed snapshots receive no best-effort or partial diff.

The comparator uses an explicit semantic whitelist. It does not recursively diff arbitrary JSON and it does not infer whether a change is favorable, unfavorable, bullish, or bearish.

## Stored facts and current context

`stored_deltas` describe immutable facts captured in the two snapshots, including registered dependency revisions, rule traces, profiles, section status, valuation cells, technical ranges, confluence, deployment, liquidity, and screening records.

Valuation cells use logical EPS series, logical PE series, EPS scenario and fiscal
year as stable identity. Revision-specific IDs remain provenance only. Technical
anchor/provenance changes are reported separately from calculated target/support
range changes; ambiguous duplicate groups use deterministic exact-match/add-remove
semantics and do not infer lineage.

`current_context_deltas` describe dependency state resolved at the requested `comparison_cutoff`. They are not allowed to rewrite or invalidate historical snapshot facts. The API exposes the base and comparison current contexts separately.

Both snapshots and both dependency contexts are read in one SQLite read transaction with `query_only=ON`. Existing repository methods gained connection-aware variants so the service does not open independent read views during one comparison.

M1B context reuses the Phase 10 exact publication-evidence binding rule. A later
corrected accepted evidence event cannot make an older M1B revision eligible; a
new M1B revision must explicitly bind that evidence ID.

## Read-only API

```text
GET /api/v2/analysis/snapshots/compare
  ?base_snapshot_id=...
  &comparison_snapshot_id=...
  &comparison_cutoff=2026-08-12T04:00:00Z
```

The cutoff is mandatory and must contain an explicit timezone. Missing snapshots return 404. Persisted snapshot integrity failures are server errors. Semantic incompatibility returns a successful HTTP response with `status=incomparable_contract` and explicit reasons.

Public comparison DTOs do not expose stored SHA-256 values. The endpoint performs no writes.

## Evidence Workspace

The existing snapshot history page links to `/snapshots/compare`. The page sends only a GET request and renders server-provided delta arrays. It does not recompute differences in the browser. Desktop and mobile layouts preserve long identifiers and values without horizontal page overflow.

## Boundaries and limitations

- No model rerun or snapshot mutation.
- No historical fact is relabeled using current dependency state.
- No database schema or dependency changes.
- No arbitrary JSON diff fallback.
- No investment direction, recommendation, or automated action.
- Phase 12 has not started.
