# Phase 3 Acceptance Evidence

## Implemented scope

- Versioned CBC M1B and TWSE／TPEx turnover contracts with raw units, TWD normalization, source metadata, `available_at`, `ingested_at`, revision and status.
- As-of-safe repository, deterministic ratio／rolling mean／historical percentile engine and independent v2 liquidity section.
- LIQ-01 and LIQ-02 traces only; LIQ-03 and LIQ-04 remain unsupported.
- Legacy v1 paths remain separate.
- Latest partial turnover cannot be masked by an older complete observation.
- TWSE and TPEx remote failures are isolated; one successful source persists as partial.
- Turnover revocation is ranked before status interpretation and cannot restore an older revision.

## Data-source evidence

- CBC Statistical Database API: `EF15M01`; metadata declares `新台幣百萬元,%`.
- TWSE OpenAPI: `exchangeReport/FMTQIK`, `TradeValue`.
- TPEx OpenAPI: `tpex_daily_trading_index`, `TradeAmount`.

CBC does not expose historical per-period publication timestamps in this dataset response. Tests use an explicit release mapping, and import fails closed when it is absent.

## Validation record

- Phase 3 review-fix focused suite: 19 passed.
- Full regression: 140 passed after review fixes.
- v1 golden snapshot: 2 passed.
- Warning: the pre-existing FastAPI TestClient `httpx`／`httpx2` deprecation warning remains; no new warning category was observed.
- Collector tests use fixed local fixtures and do not require live network access.

Commit and CI run are recorded in the Draft PR after execution.

## Boundary

Phase 3 only. Phase 4 was not started. No external Forward EPS collector, broker API, real-account connection, real order, automatic trading, sell signal or fixed 2.5 trillion threshold was added. Phase 2 contracts were not changed.
