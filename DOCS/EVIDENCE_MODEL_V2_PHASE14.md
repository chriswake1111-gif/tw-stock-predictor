# Evidence Model v2 Phase 14 — Official EOD Close Context

## Status

This document records the LUK-29 Phase 14 Implementation scope and the latest
Second Code Review remediation. The implementation is isolated from the Phase
13 baseline `be8e931038bf818cf7d9f578e18fa91b8f068cd4` and remains in one Draft
PR for the Third Code Review gate. It is not a merge, deployment, production
activation, or Phase 15 authorization.

## Purpose and boundary

Phase 14 provides a neutral, read-only context for an official, unadjusted end-of-day close. It reports source evidence, product classification, data freshness, quality status, and fail-closed reasons. It does not calculate targets, valuation, technical indicators, waves, signals, ranking, recommendations, backtests, or trading actions.

The browser calls the backend only. No provider request, credential, write operation, broker connection, order, or auto-trading capability is added.

## Approved source contracts

| Evidence | Provider/resource | Exact source contract | Accepted price field |
| --- | --- | --- | --- |
| TWSE whole-market EOD | `twse-universe-official` / `twse.eod.stock_day_all` | `GET https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`; top-level JSON array; exact required fields include `Code`, `Name`, `Date`, `TradeVolume`, `TradeValue`, `ClosingPrice`, and the remaining approved daily fields | `ClosingPrice` only |
| TPEx whole-market EOD | `tpex-universe-official` / `tpex.eod.daily_close_quotes` | `GET https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`; top-level JSON array; exact approved field set includes `SecuritiesCompanyCode`, `CompanyName`, `Date`, `TradingShares`, `TransactionAmount`, `Close`, and the remaining approved daily fields | `Close` only |
| Product classification | `twse-isin-official` / `twse.isin.security_classification` | `GET https://isin.twse.com.tw/isin/single_main.jsp?owncode={official_code}&stockname=`; Traditional labels are matched exactly for `Security Code`, `Market`, `Type of security`, listing date, ISIN/CFI, currency, and remarks | Not a price source |

The two EOD parsers require the approved top-level array and required string fields. Envelope drift, field drift, invalid ROC dates, mixed source dates, malformed numeric strings, or provider errors fail closed. Empty payloads are retained as an unknown observation; row count or absence alone never proves a partial feed. Partial status is available only through a separately proven partial fixture/coverage proof.

The legacy `tpex_mainboard_quotes` resource is excluded from Phase 14 and cannot be used as a fallback. The Phase 14 path does not use yfinance, adjusted prices, inferred weekdays/weekends, annual holiday schedules, FMTQIK, TPEx `spendi`/`cmode`, or a calendar/session authority.

## Product, unit, and status policies

The seeded policy versions are:

- `eod_product_scope_v1`
- `eod_currency_unit_scope_v1`
- `eod_public_close_eligibility_v1`
- `eod_currentness_policy_v1`
- `eod_close_status_matrix_v1`
- `official_reported_close_v1`
- public DTO `eod_close_context_v1`

Only an exact code/venue/classification match with `Type of security=股票`, a proven TWD per-share policy, a positive close, usable positive volume, accepted identity binding, and eligible observation may expose `status=available` and `close_value`. Exact non-stock classifications include `認購權證`, `認售權證`, `權證`, `權利證書`, `臺灣存託憑證`, `存託憑證`, ETF, ETN, and preferred evidence from exact Type/CFI/Remarks fields; these are `not_applicable`. Missing, foreign, conflicting, or unrecognized classification is `needs_human_input`.

Status precedence is deterministic: `blocked > needs_human_input > partial > unknown > insufficient_data > not_applicable > available`. Every non-available DTO returns `close_value=null`; currency, unit, and price semantics are also withheld unless the status is available. Unknown reason codes are fail-closed as blocked.

Current mode accepts only a source trade date equal to the evaluated Asia/Taipei date. An older source is stale/unknown, a future or invalid date is blocked, and same-day session completion is never inferred. Current source, classification, and observation selection requires both `available_at <= evaluated_at` and `ingested_at <= evaluated_at`; ingestion alone cannot expose future-available evidence. Missing source, empty source, or no cutoff-visible record remains unknown. An explicit suspension is `not_applicable`.

As-of mode uses `latest_observed_trade_date_as_of_cutoff`: first choose the maximum valid source trade date visible at the cutoff, then choose the deterministic latest revision for that exact date. If that date is blocked, missing, revoked, or otherwise unusable, the service does not fall back to an earlier date. Identity and classification are selected at the same knowledge cutoff in the same read transaction.

## Persistence and recovery

Migration `20260827_18_phase14_eod_close_context` is additive, rerunnable,
checksum-safe, and preserves existing Phase 10/13 registry rows. It registers
the two EOD resources and the exact TWSE ISIN classifier, seeds TWD/share
policies with `write_enabled=0`, and adds:

- `eod_price_resource_policies`
- `eod_close_source_snapshots`
- `eod_product_classification_evidence`
- `eod_close_observations`
- `eod_ingestion_idempotency`

Migration `20260827_19_phase14_second_code_review_remediation` is additive and
leaves Migration 18 immutable. It adds the internal
`source_observation_state` evidence column and the durable command-reservation
table used by the operator retry state machine:

- `source_observation_state`
- `eod_ingestion_command_reservations`

Raw evidence remains bound to the existing Phase 10 `raw_resource_revisions` identity and SHA-256 provenance. EOD records, corrections, revocations, and the completed idempotency ledger are append-only and immutable; a correction must explicitly supersede the latest revision in its logical chain. The additive command-reservation table is a durable `reserved -> running -> completed` / `running -> reserved` state machine that binds a key and payload before side effects. Raw revision, source/classification evidence, source-observed rows, normalized observations, the legacy EOD ledger, Phase 10 run/item, and terminal run state are committed in one SQLite transaction. Failure cleanup releases the lock, records the failed run/audit outcome, and returns the reservation to retryable `reserved` state. Focused tests cover correction-before/after-cutoff, late-D1 versus D2 selection, latest blockers/revokes without fallback, classifier determinism and exact fixtures, source-observed/public-eligibility separation, atomic failure recovery, same-key concurrency, backup/restore, availability/ingestion visibility, and Phase 13 identity epoch reuse.

The operator ingestion service is disabled unless explicitly enabled. Public reads never migrate or write the database. Evidence backup/restore validation includes all six EOD tables and their foreign-key dependencies, and post-restore repository/service checks preserve correction and revocation lineage, D-first/no-fallback semantics, current latest-blocking, Phase 13 identity epochs, command reservation state, and zero-volume raw retention/public ineligibility.

## Public API and frontend

The public API is GET-only:

- `GET /api/v2/market-context/eod-close/current/{canonical_symbol}`
- `GET /api/v2/market-context/eod-close/as-of/{canonical_symbol}?knowledge_cutoff_at=...`

There is no public `trade_date` selector. Invalid symbols/cutoffs return 422; missing Phase 14 storage returns 503; ordinary evidence insufficiency is a 200 DTO with an explicit status and null close. The DTO excludes raw payloads, hashes, credentials, locks, actor identifiers, idempotency keys, internal revision chains, ranking, recommendation, valuation, technical, wave, signal, quote, and return fields.

The `/eod-close` page is a neutral, read-only surface with current/as-of selection, timezone-aware cutoff input, explicit status/reason display, backend-only fetches, and the disclaimer that the official unadjusted close is not a target price, buy/sell instruction, or recommendation. The public DTO remains unchanged by the internal source-observation state.

## Review boundary

The Draft PR is the only requested external change. First and Second Code Review remediations are implemented on the same Draft PR; exact-head CI and the Third Code Review, merge, deployment, production activation, and Phase 15 remain separate gates. No Phase 15 work is included in this implementation.
