# Evidence Model V2 Phase 6

## Scope

Phase 6 adds a percentile-based「二低一高」research screening module. It produces a
detailed research result, not a buy/sell signal, recommendation, broker instruction, or automatic
order. `SEL-01` remains evidence level C, `project_operationalization`, and requires human approval.

## Concept versus project operationalization

The evidence-backed concept is low PE, low P/B, high cash dividend yield, plus an independent
technical confirmation. The actual thresholds, history window, minimum sample, EPS source, and
technical component are immutable parameters of an approved `ScreeningProfileRevision`.

The example 30th/30th/70th percentile profile is a project research configuration. It is not a
published fixed Du-method formula. Phase 6 contains no fixed PB 1.5, dividend-yield 4%, or fixed PE
rule. `SEL-02`, `SEL-03`, and `MA-01` through `MA-03` cannot satisfy this screen.

## Normalized valuation contract

Legacy `stock_valuation` is not used as a V2 screening input because it lacks the complete
point-in-time revision contract. The additive `security_valuation_observations` table records:

- logical observation ID, revision number, and previous revision ID;
- symbol and metric date;
- PE, PB, and dividend yield normalized as a ratio;
- source name and dataset;
- `available_at`, `ingested_at`, and status.

Revisions preserve symbol, metric date, source identity, and yield unit. A revision must increment
by one and reference the prior latest stored revision. As-of lookup first selects the highest
revision visible under both timestamps, then checks status. A revoked latest revision never falls
back to an older value.

No collector or historical backfill is added in this phase. If trustworthy point-in-time history
has not been imported into the normalized table, screening returns `insufficient_data`.

## Percentile convention

The pure engine uses deterministic empirical upper rank:

```text
percentile = count(history_value <= current_value) / observation_count * 100
```

Equal values therefore receive the same upper-rank percentile. The window is a profile-controlled
number of calendar years through the knowledge-cutoff date, not a fixed row count. The minimum
observation count is also profile-controlled; the Phase 6 contract applies a safety floor of 20.

PE and PB must be finite and greater than zero. Dividend yield must be finite, non-negative, and
stored as a ratio (`0.04` means 4%). Invalid values do not become artificially favorable metrics.
Missing or insufficient components fail closed.

## Forward EPS growth

Phase 6 consumes the Phase 2 Forward EPS repository and exact VAL-02 approval semantics. Each
profile explicitly selects `source_name` and `source_type`. For each fiscal year, the repository
returns the effective latest revision at the knowledge cutoff; revoked or unapproved latest
revisions do not fall back, and multiple logical series for one selected source/year are treated as
ambiguous rather than averaged or auto-selected.

The engine uses the latest two chronological approved base forecasts only when the fiscal years are
consecutive:

```text
growth = (later_eps_base - earlier_eps_base) / earlier_eps_base
later_fiscal_year = earlier_fiscal_year + 1
```

The earlier EPS must be positive. Missing years return
`forward_eps_growth_requires_two_years`; a gap returns
`forward_eps_growth_requires_consecutive_years`; ambiguous series return
`forward_eps_source_selection_ambiguous`; and a non-positive denominator returns
`non_comparable_forward_eps`. Negative comparable growth is an available component that fails the
approved profile. Historical TTM EPS and fixed growth assumptions are never substituted.

## Screening profile governance

Profile revisions and approval events are immutable, idempotent, and as-of safe. An approval binds
the exact profile revision and is created server-side from `SEL-01` registry metadata:

- evidence level C;
- implementation mode `project_operationalization`;
- `project_operationalization: true`;
- exact approval actor from protected server configuration.

A new profile revision needs a new approval. Latest revoke wins without fallback. There is no
implicit symbol/industry/global precedence. A request must explicitly select a logical profile or
revision, unless exactly one applicable approved profile exists. Zero eligible profiles returns
`approved_screening_profile_required`; more than one returns
`screening_profile_selection_required`.

Industry and self-history are distinct bases. This phase implements normalized self-history.
Industry membership and point-in-time peer history are not yet contracted, so industry requests
return `industry_peer_data_required` and never fall back to self-history.

## Technical component

`TechnicalTurnComponent` is a replaceable protocol outside the percentile engine. The default
component is unavailable and returns `technical_confirmation_required`; it never defaults to true.
Phase 6 does not implement WV-03. A separately evidenced component can be injected and retains its
own Rule Trace. MA resonance and MA-03 are explicitly rejected as Phase 6 confirmation sources.

## API

Additive V2 endpoints:

- `POST /api/v2/security-valuations`
- `POST /api/v2/screening-profiles`
- `POST /api/v2/screening-profiles/{profile_revision_id}/approval`
- `GET /api/v2/screening/{symbol}`

The screening section is also added to `GET /api/v2/analysis/{symbol}` with section-local failure.
Writes remain default-closed and require the configured admin API key and server-side actor.
Idempotency keys and fingerprints are not returned by public DTOs.

An available result distinguishes `status` from `research_result`, which is either
`meets_approved_profile` or `does_not_meet_approved_profile`. Rule Trace includes `SEL-01`, model
version, evidence class, implementation mode, logical profile ID, exact revision ID, exact approval
ID, and cutoff. `automatic_order` is always false.

## Known limitations

- No production historical valuation importer is included; normalized point-in-time observations
  must be supplied through the protected API or another authorized future import path.
- Industry classification and peer history remain unavailable and fail closed.
- No concrete WV-03 implementation is provided; the default technical component is unavailable.
- The 30/30/70 example, calendar window, and minimum sample are project research parameters, not
  verified Du fixed thresholds.
- A screening pass is not a statement that a security is cheap, suitable, or expected to rise.
- Phase 7 target confluence, cross-method scoring, and immutable whole-analysis snapshots are not
  implemented.
