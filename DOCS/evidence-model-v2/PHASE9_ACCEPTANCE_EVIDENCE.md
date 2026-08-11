# Phase 9 Acceptance Evidence

Phase 9 adds read-only discovery contracts, deterministic presentation status and a same-origin Evidence Workspace. Phase 1–8 calculations, approvals and immutable histories remain unchanged.

Final local evidence: backend focused Phase 9 tests 15 passed; backend full regression 341 passed with one existing Starlette warning; v1 golden 2 passed; frontend unit/contract/accessibility 10 passed; frontend lint, TypeScript and production build passed; Playwright desktop/mobile visual suite 2 passed and produced eight deterministic state captures. GitHub Actions evidence is recorded in the Draft PR.

The browser client references only `/api/v2` GET resources. It contains no write method, admin credential, current quote, financial calculation, aggregate score, ranking, probability, PWA or broker integration.

## First Code Review remediation

The first review findings were closed additively on the Phase 9 branch:

1. Backend/frontend DTO drift — **CLOSED**. The frontend consumes `pe_value`,
   `calculated_level`, and the planner's stored `weight` / `capital_budget`
   fields. `tests/contracts/phase9_frontend_contracts.json` is consumed by the
   React tests and validated against actual FastAPI GET output plus the Phase 7
   confluence engine.
2. FB-03 target versus FB-04 support semantics — **CLOSED**. The technical
   scenario read output now carries server-authoritative `semantic_role`; the
   browser only maps that value to presentation labels.
3. Multi-cluster contamination — **CLOSED**. Every `overlap_ranges` cluster is
   rendered with its own independent-method count and evidence strength. Stable
   ordering is explicitly disclosed as non-recommendational.
4. Snapshot detail status contract — **CLOSED**. The exact detail type no longer
   extends the list summary type and reads status from immutable
   `snapshot.output.status`; it does not invent `analysis_status`.

Presentation follow-up also formats the stored historical reach-rate decimal as
a percentage without recomputing numerator or denominator, uses a neutral
evaluation-origin disclosure, and hides the raw `maximum_cluster_strength`
policy identifier from the main user-facing summary.

### Contract data flow

```text
Evidence Model V2 backend
        ↓
read-only API DTO
        ↓
backend-validated shared contract fixture
        ↓
React presentation only
```

The browser does not recompute PE targets, Fibonacci levels, deployment weights
or budgets, cluster metrics, snapshot status, screening results, historical
performance, evidence grades, ranking, or probability.

Reviewers and users must keep the following distinctions explicit:

- Evidence Grade is not Method Confluence.
- Method Confluence is not Historical Target Reach.
- Historical Target Reach is not future probability.
- FB-03 is a target scenario; FB-04 is a support scenario.
- Disjoint target clusters retain independent semantics and no cluster is
  automatically recommended.
- Snapshot Detail shows immutable stored output, not reconstructed current
  analysis.
