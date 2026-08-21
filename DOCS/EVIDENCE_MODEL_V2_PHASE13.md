# Evidence Model v2 Phase 13 — Universe Foundation

Phase 13 supplies neutral identity and data-quality context for Taiwan listed instruments. It does not provide price/quote ingestion, ranking, screening, valuation, recommendation, broker connectivity, or trading.

## Status and as-of contract

`universe_status_matrix_v1` is shared by exact, canonical, code-plus-venue and list lookups. A timezone-aware `knowledge_cutoff_at` is required; date-only input is rejected rather than expanded to end-of-day. Historical references filter visibility by the cutoff. Current reads select the actual latest revision first, so provider errors, partial/schema changes, awaiting-review and revocation cannot be hidden by an older fallback.

## Identity and mapping

`universe_instruments` is an immutable anchor keyed by venue, source code and identity epoch. Same-code reuse requires explicit termination and no proven continuity; a successor gets a new anchor and append-only alias. `universe_symbol_mapping_v1` creates `.TW` or `.TWO` only for an approved resource scope and verified identity. `security_type=unknown` remains unknown.

## Source boundary

Only TWSE `t187ap03_L`, `company/newlisting`, and termination candidates plus approved TPEx master, delisted, operational and corroborating sources are registrable. `tpex_mainboard_quotes` is an explicit excluded price classification and has no Phase 13 resource, collector, hash, fixture, API or frontend path.

## Writes and recovery

Runtime ingestion is disabled unless `UNIVERSE_INGESTION_WRITES_ENABLED=true` and an operator context (actor, run/lock/audit) is supplied. All revisions, events, anchors and idempotency bindings are append-only. A key is permanently bound to its payload fingerprint; corrected content creates a superseding revision. EvidenceBackupService validates and round-trips the Universe tables and their existing provenance dependencies.
