# Evidence Model v2 Phase 13 — Universe Foundation

Phase 13 supplies neutral identity and data-quality context for Taiwan listed instruments. It does not provide price/quote ingestion, ranking, screening, valuation, recommendation, broker connectivity, or trading.

## Status and as-of contract

`universe_status_matrix_v1` is shared by exact, canonical, code-plus-venue and list lookups. A timezone-aware `knowledge_cutoff_at` is required; date-only input is rejected rather than expanded to end-of-day. Every lookup uses one SQLite read transaction and the same cutoff for both channels:

1. The historical/reference channel selects the latest safe accepted identity/provenance visible at the cutoff.
2. The operational channel selects the actual latest resource or instrument state visible at that cutoff.

Provider errors, partial observations and schema changes may therefore preserve an older reference while reporting a blocked operational state. Revocation is stronger than transport failure and never becomes a hidden fallback. The legacy `current` argument is internal compatibility only and cannot bypass the cutoff.

## Identity and mapping

`universe_instruments` is an immutable anchor keyed by venue, source code and identity epoch. Same-code reuse requires explicit termination and no proven continuity; a successor gets a new anchor and append-only alias. `universe_symbol_mapping_v1` creates `.TW` or `.TWO` only for an approved resource scope and verified identity. `security_type=unknown` remains unknown.

## Source boundary

Only TWSE `t187ap03_L`, `company/newlisting`, and termination candidates plus approved TPEx master, delisted, operational and corroborating sources are registrable. TPEx `spendi history`, `spendi today` and `cmode` are distinct source contracts in the collector and logical revision keys; legacy rows remain only for already imported history. `tpex_mainboard_quotes` is an explicit excluded price classification and has no Phase 13 resource, collector, hash, fixture, API or frontend path.

## Writes and recovery

Runtime ingestion is disabled unless `UNIVERSE_INGESTION_WRITES_ENABLED=true` and a complete operator context (non-blank actor, run, lock and audit references) is supplied. Revision ingestion additionally requires a non-blank idempotency key and Phase 10 raw-resource provenance before its first mutation. A resource-level revision can record accepted, partial, provider-error, schema-changed, awaiting-review or revoked source health even when no normalized instrument row exists; list/search composes that health per venue without silently dropping a blocked source. All revisions, events, anchors and idempotency bindings are append-only. A key is permanently bound to its payload fingerprint; corrected content creates a superseding revision. Historical lookups require an explicit non-null availability instant; manual-publication resources also require the exact accepted publication evidence to be visible at the cutoff and at or before that approved availability point.

Source-specific envelope/required-field validators derive parser/schema evidence and independent raw versus normalized SHA-256 values. Query dimensions and source-record references are stored with the revision; public DTOs omit raw hashes, payloads, credentials, locks, idempotency keys and fingerprints. The additive remediation migration is versioned as `20260821_17_phase13_second_review_remediation` in the additive migration marker and is rerunnable/checksum-safe without rewriting the original Phase 13 migration. Successful operator mutations emit secret-safe structured audit logs containing actor/run/lock/audit, command/resource/venue/channel, typed outcome/reason and server timestamp. EvidenceBackupService validates and round-trips the Universe tables and their existing provenance dependencies.
