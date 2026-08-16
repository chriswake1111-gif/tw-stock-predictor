# Evidence Model V2 Phase 12 — Research Review Queue MVP

## Scope

Phase 12 adds a local, single-user workflow for maintaining a research watchlist and explicitly acknowledging an immutable analysis snapshot as the current review baseline. It reuses the Phase 11 comparison service at query time and does not persist comparison results.

This workflow metadata is not Evidence Model evidence. It is not a stock score, ranking, recommendation, prediction, signal, order, or temporal event stream.

## Durable contracts

- Workflow contract: `research_review_queue_v1`.
- Base migration: `20260813_14_evidence_model_v2_research_review_queue`.
- First-review additive remediation: `20260813_15_phase12_first_review_remediation` enforces canonical symbols without rewriting the reviewed migration checksum.
- Tables: `research_watchlist_items` and append-only `research_review_events`.
- `2330` and `2330.TW` resolve to the same identity; `2330.TWO` remains independent.
- Archive preserves the item ID and review history. Archive/unarchive responses expose
  `changed=true` only for a real state transition and `changed=false` for an idempotent no-op;
  adding an archived symbol restores that same item.
- A review event records the exact snapshot ID, explicit comparison cutoff, and server review time.
- The actual latest visible snapshot is selected by `created_at DESC, snapshot_id DESC`; incompatible or corrupt latest rows never fall back to an older row.
- `comparison_has_deltas` is `true` or `false` only for a comparable result; unavailable states use `null`.
- `stale` is a freshness fact. It does not assert that the market or dependency context changed after review.

## Local security boundary

All `/api/v2/research/*` routes require a valid, immutable startup configuration, an actual loopback ASGI client, and an exact configured Host. If an Origin is present on a read it must also be exact-allowlisted.

```text
RESEARCH_APPLICATION_ORIGIN=http://127.0.0.1:8000
RESEARCH_ALLOWED_DEV_ORIGINS=http://localhost:5173
RESEARCH_WORKFLOW_WRITES_ENABLED=false
```

Configured origins require `http` or `https`, a loopback host, an explicit numeric port, and no userinfo, wildcard, query, fragment, or non-root path. Any invalid configured origin disables every Research route with `503 research_security_configuration_invalid`.

Writes are disabled by default. When explicitly enabled, a write additionally requires an exact Origin, JSON content type, a body no larger than 16 KiB, and a valid host-only HttpOnly SameSite=Strict CSRF session with a 1,800-second TTL. An `http.disconnect` received while reading a partial body terminates processing without invoking the downstream application. The browser receives no admin API key and never auto-replays a mutation after CSRF expiry.

## API

```text
GET  /api/v2/research/csrf-token
GET  /api/v2/research/queue?comparison_cutoff=<timezone-aware timestamp>&order=<symbol|updated_at>
GET  /api/v2/research/queue/{item_id}?comparison_cutoff=<timezone-aware timestamp>
POST /api/v2/research/queue
POST /api/v2/research/queue/{item_id}/archive
POST /api/v2/research/queue/{item_id}/unarchive
POST /api/v2/research/queue/{item_id}/acknowledgments
```

Acknowledgment writes require `Idempotency-Key`. The same key and semantic payload return the original event; reuse with a different semantic payload returns `409 review_idempotency_conflict`. The key remains in internal persistence for retry/conflict enforcement but is excluded from acknowledgment responses and nested queue/detail review-event references by an explicit public projection.

Queue responses expose `workflow_contract_version=research_review_queue_v1`. The default
neutral order is `symbol ASC, watchlist_item_id ASC`; `order=updated_at` uses
`updated_at DESC, symbol ASC, watchlist_item_id ASC`. This is operational ordering only and
does not express priority, importance, attractiveness, or recommendation.

Each Research mutation emits a Phase-12-local structured audit record with a server-generated
correlation ID, command type, item/event identity where available, canonical symbol,
outcome/reason, and server timestamp. Audit records never include CSRF/session material,
admin credentials, raw idempotency keys, headers, cookies, authorization data, or request bodies.
The audit is not persisted to a database and does not change migrations 14 or 15.

## Explicit non-goals

Phase 12 does not add a journal, notes, current quotes, ranking, recommendation, alert, LLM workflow, multi-user access, broker connection, real account access, real order placement, automatic trading, or Phase 13 behavior.
