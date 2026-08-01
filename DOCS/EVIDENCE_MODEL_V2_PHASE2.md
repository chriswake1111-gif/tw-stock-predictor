# Evidence Model v2 Phase 2: Forward EPS and PE scenarios

## Scope

Phase 2 adds manually ingested or authorized-source Forward EPS observations, a protected human-approval ledger, PE scenarios, and a parallel v2 valuation API. It does not add an external Forward EPS collector and does not modify the legacy v1 API.

## Knowledge cutoff policy

All internal knowledge queries use timezone-aware UTC ISO-8601 timestamps.

- `knowledge_cutoff_at`: used exactly after normalization to UTC.
- Date-only `as_of_date`: conservatively mapped to `00:00:00 Asia/Taipei`, not end of day. Same-day publications are therefore excluded.
- No cutoff input: server request time in UTC.

The API returns both `knowledge_cutoff_at` and `cutoff_policy`.

An observation is usable only when both conditions hold:

```text
available_at <= knowledge_cutoff_at
ingested_at <= knowledge_cutoff_at
```

## Forward EPS revision contract

Forward EPS records are immutable and include:

- `id`
- `logical_series_id`
- `revision_number`
- `revision_of`
- `available_at`
- server-assigned `ingested_at`
- `idempotency_key`
- reproducible `payload_fingerprint`

As-of queries select the latest known revision in each logical series. A later revision does not alter results for a cutoff before that revision became both available and ingested.

Allowed source types are `broker_report`, `company_guidance`, `consensus_api`, and `manual`. `historical_ttm` is rejected. Historical TTM is not substituted into the Forward EPS matrix.

Multiple sources are never automatically averaged. Each approved source observation generates separate matrix cells, and every cell contains its original observation ID and approval ID. Unapproved or revoked observations fail closed.

## PE scope and approval

PE scenarios are immutable revisions with one explicit scope:

1. `symbol`
2. `industry`
3. `market`

Only an approved, effective, non-revoked `symbol` scenario with non-U evidence is automatically used in the verified valuation matrix. C-level approvals are explicitly marked as project operationalization. Industry and market scenarios are returned as reference data only. Draft, revoked, not-yet-effective, and expired revisions are excluded. Evidence level and approver identity are assigned by the protected backend approval flow, not by import callers.

The approval policy is rule-specific: `VAL-02` approves Forward EPS and `VAL-04` approves company-specific PE selection. `VAL-01` is the deterministic multiplication formula and `VAL-03` is parameterized scenario support, so neither borrows a PE approval ID. Rule Trace contains only approval IDs whose ledger event has that exact Rule ID.

Forward EPS states are reported separately: no observation is `insufficient_data`, a draft observation is `needs_human_input`, all latest decisions revoked is `insufficient_data` with reason `forward_eps_approval_revoked`, and a valid `VAL-02` approval is `available`. Public records expose `import_status` and ledger-derived `effective_approval_status`.

## Formula and non-applicable EPS

```text
target_price = forward_eps * approved_symbol_pe
```

Zero or negative EPS observations may be preserved as evidence, but their matrix cells return `not_applicable` and `target_price: null`.

## Migration

Migration IDs:

```text
20260801_01_evidence_model_v2_valuation
20260801_02_evidence_model_v2_approval_hardening
```

The centralized migration runner records the migration ID, SQL SHA-256, and application timestamp. All migration statements execute inside one SQLite transaction. Re-running the same migration is a verified no-op; a checksum change for an applied version is rejected.

New tables:

- `forward_eps_observations`
- `pe_scenarios`
- `valuation_approvals`
- `valuation_idempotency_keys`
- central `schema_migrations` ledger

The migration is additive. Existing v1 tables are unchanged, and application rollback can ignore the new tables without deleting stored evidence.

## API

```text
POST /api/v2/forward-eps
POST /api/v2/pe-scenarios
POST /api/v2/forward-eps/{observation_id}/approval
POST /api/v2/pe-scenarios/{scenario_id}/approval
GET  /api/v2/analysis/{symbol}
GET  /api/v2/model-rules
```

All POST routes are disabled unless `EVIDENCE_V2_WRITES_ENABLED=true`, and require `X-Admin-API-Key` matching `EVIDENCE_V2_ADMIN_API_KEY`. The audited actor comes from `EVIDENCE_V2_ADMIN_ACTOR`; callers cannot submit `approved_by`. Imports always create draft resources. Separate protected approval routes create immutable approval events.

POST requests require `Idempotency-Key`. A permanent ledger binds every key to its fingerprint and resource ID. Repeating a key and payload returns the existing resource; reusing that key with a different payload is rejected. Public DTOs do not expose keys or fingerprints.

Sections belonging to Phase 3 or later return `unsupported` or `needs_human_input`; partial data does not fail the entire response.

## Remaining limitations

- No external Forward EPS collector.
- No automatic forecast-source averaging or source selection.
- No grouped forecast-set workflow; individually approved sources remain separate.
- No Phase 3 M1B integration.
- No Phase 4 anchor or wave workflow.
- No immutable full-analysis snapshot yet; `snapshot_id` remains `null` until Phase 7.

## Reproducible test dependencies

CI installs `requirements-dev.txt`, which applies `constraints.txt`. The compatibility set pins FastAPI, Starlette, Pydantic, httpx2, pytest, and pytest-cov; CI does not perform an additional unconstrained test-tool installation.
