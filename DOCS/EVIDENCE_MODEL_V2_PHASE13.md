# Evidence Model v2 Phase 13 — Universe Foundation

Phase 13 supplies neutral identity and data-quality context for Taiwan listed instruments. It does not provide price/quote ingestion, ranking, screening, valuation, recommendation, broker connectivity, or trading.

## Status and as-of contract

`universe_status_matrix_v1` is shared by exact, canonical, code-plus-venue and list lookups. A timezone-aware `knowledge_cutoff_at` is required; date-only input is rejected rather than expanded to end-of-day. Every lookup uses one SQLite read transaction and the same cutoff for both channels:

1. The historical/reference channel selects the latest safe accepted identity/provenance visible at the cutoff.
2. The operational channel selects the actual latest resource or instrument state visible at that cutoff.

Provider errors, partial observations and schema changes may therefore preserve an older reference while reporting a blocked operational state. Revocation is stronger than transport failure: the visible revoke targets the immutable `instrument_revision_id` domain, and without a cutoff-visible corrected accepted revision the old identity is not returned as a safe historical reference. The legacy `current` argument is internal compatibility only and cannot bypass the cutoff.

List/search selects the latest safe accepted reference for each instrument before
applying query, security-type, listing-status, cursor, or page predicates. This
prevents an older matching revision from being resurrected when the latest
reference no longer matches the requested filter. Resource health is resolved
per `(resource_id, logical_revision_key)` at the same cutoff and then reduced
to each venue using the `universe_status_matrix_v1` precedence
(`needs_human_input` > `partial` > `available` > `insufficient_data`); a newer
healthy resource or logical feed cannot mask a blocker from another required
feed in that venue, while a corrected revision may clear only its own logical
feed. Optional corroborating/manual sources remain neutral unless an explicit
policy makes them a dependency, and zero-row resource observations remain
visible in the health composition.

## Identity and mapping

Operational observations without a source publication instant are visible only
from their server-side ingested_at observation boundary. They remain blocked
operational health and never become a historical identity reference.

`universe_instruments` is an immutable anchor keyed by venue, source code and identity epoch. Same-code reuse requires explicit termination and no proven continuity; a successor gets a new anchor and append-only alias. `universe_symbol_mapping_v1` creates `.TW` or `.TWO` only for an approved master resource scope and verified identity. A carried mapping is effective only when the approved master is safe at the relevant knowledge cutoff: its availability and publication evidence must be visible, and it must not be targeted by a visible instrument-revision revoke. Corroborating/manual rows cannot inherit a future, unavailable or revoked master mapping; read-time projection reuses the same safe master channel, and without such evidence the canonical field remains null and the result is `canonical_mapping_unverified`. `security_type=unknown` remains unknown.

Canonical, code-plus-venue and list reads use one cutoff-bound identity-epoch
selector. A later epoch becomes effective only when its immediate predecessor's
latest visible listing event is an accepted termination and the successor anchor
is itself visible. Before that boundary the old epoch remains reproducible; after
it, the old epoch cannot compete because it has a larger revision number. The
selector is scoped by venue and preserves leading-zero source codes, so sequential
code reuse cannot expose duplicate canonical keys or cross-venue collisions.
List/search first keyset-selects a bounded window of `(venue, official_code)`
groups in canonical order, then applies the recursive epoch/event CTE only to
that window. Filtered pages advance through additional bounded windows instead
of evaluating the full Universe before `LIMIT limit+1`; no universe-sized Python
ID set or `IN (...)` predicate is constructed.

Cutoff-visible lifecycle and operational event tables are composed into the same
read transaction as the reference and freshness channels. `listed` and
`terminated` events affect `listing_status` only; operational events and a
source-backed `resumed` event affect `trading_state` only. Date-only events are
not applied during the same calendar date because no intraday instant is proven.
Event provenance is returned without raw payloads, credentials or idempotency
secrets, and a non-accepted latest event fails closed to `unknown` rather than
resurrecting an older state. Awaiting-review events map to
`source_revision_awaiting_review`; applicable revoked events use
`source_revision_revoked_without_corrected_revision`; other blockers remain
non-actionable `partial`. The actionable allowlist is exactly the five LUK-15
reasons, while `availability_unproven` remains fail-closed but is not actionable.

Instrument-level `supersedes_revision_id` is derived from the exact parent `universe_revision_id` being corrected or revoked, mapped to the normalized instrument revision for the same instrument, resource and logical revision chain. A first revision in a new source/logical chain has no implicit global instrument predecessor. Historical eligibility uses one cutoff-visible chain evaluator: accepted corrections and revoked events both exclude their direct targets and all visible ancestors, so an older accepted revision cannot be resurrected after a correction/revocation sequence. List/search uses the same effective reference before filters, canonical ordering, cursor binding and `LIMIT limit+1`; blocked or unmapped rows do not consume safe identity page slots, while venue resource health remains independently visible.

## Source boundary

The legacy synthetic TPEx spendi key is read-compatible only for persisted
history and is rejected for new collector/parser ingestion. Approved resources
use fixed official envelopes and required field groups; missing or renamed
required fields and envelope drift become schema_changed. The expected schema
contract is code-owned and checked before acceptance; a caller cannot authorize
drift by supplying a matching fingerprint. The observed schema evidence
fingerprint includes the envelope and row-field set and is persisted only after
the expected contract gate passes.

The reviewed first-party shapes are preserved without approximation: TWSE
`company/newlisting` and `company/suspendListingCsvAndHtml` are top-level JSON
arrays, using the official `Code`/date fields and
`DelistingDate`/`Company`/`Code` fields respectively (termination has no
synthetic `reason`). TPEx `tpex_spendi_history`/`tpex_spendi_today` validate
`Date`, `SecuritiesCompanyCode`, `CompanyName` and the suspend/resume event
fields; `tpex_cmode` validates its official trading flags rather than a
generic `status`. TPEx delisted data remains the official `tables` + `fields` +
array-row envelope. Unknown fields, wrong envelope type, field type drift and
renamed required fields fail closed without an old-parser fallback.

Only TWSE `t187ap03_L`, `company/newlisting`, and termination candidates plus approved TPEx master, delisted and operational sources are registrable for new collection. TPEx `spendi history`, `spendi today` and `cmode` are distinct source contracts in the collector and logical revision keys; legacy rows remain only for already imported history. TPEx `company.html` / `company/otcSearch` remains a manual corroborating source until its machine response contract is independently verified, so `tpex.company.current` is retained only for registry/history compatibility and is rejected by new collector/parser ingestion. `tpex_mainboard_quotes` is an explicit excluded price classification and has no Phase 13 resource, collector, hash, fixture, API or frontend path.

## Writes and recovery

Every operator revision must bind to an existing Phase 10 raw-resource
revision for the same resource and match its stored SHA-256. A caller-supplied
hash without that durable raw provenance row is rejected before any Universe
mutation.

Runtime ingestion is disabled unless `UNIVERSE_INGESTION_WRITES_ENABLED=true` and a complete operator context (non-blank actor, run, lock and audit references) is supplied. Revision ingestion additionally requires a non-blank idempotency key and Phase 10 raw-resource provenance before its first mutation. A resource-level revision can record accepted, partial, provider-error, schema-changed, awaiting-review or revoked source health even when no normalized instrument row exists; list/search composes that health per venue without silently dropping a blocked source. All revisions, events, anchors and idempotency bindings are append-only. A key is permanently bound to its payload fingerprint; corrected content creates a superseding revision. Historical lookups require an explicit non-null availability instant; manual-publication resources also require the exact accepted publication evidence to be visible at the cutoff and at or before that approved availability point.

Registered resource policy is authoritative for `freshness_mode` and master
completeness. Payload claims cannot promote a seeded
`unknown_without_official_cadence` master to `official_cadence_window` or
`licensed_reference`; coverage completeness is stored separately from cadence
proof, so seeded TWSE/TPEx masters remain `freshness=unknown` and
`current_complete=false` until a reviewed policy version proves the cadence.

Source-specific envelope/required-field validators derive parser/schema evidence and independent raw versus normalized SHA-256 values. Query dimensions and source-record references are stored with the revision; public DTOs omit raw hashes, payloads, credentials, locks, idempotency keys and fingerprints. The additive remediation migration is versioned as `20260821_17_phase13_second_review_remediation` in the additive migration marker and is rerunnable/checksum-safe without rewriting the original Phase 13 migration. Successful operator mutations emit secret-safe structured audit logs containing actor/run/lock/audit, command/resource/venue/channel, typed outcome/reason and server timestamp. EvidenceBackupService validates and round-trips the Universe tables and their existing provenance dependencies.
