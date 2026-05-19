# Engine Path Policy

This repository is the public research engine. It should expose generic,
public-safe mechanics and keep private data, paths, reports, tickers,
candidate lists, selected parameters, returns, and conclusions outside the
repo.

## Path Roles

The fast DuckDB path is the default research path for supported workflows:

```text
P3: build_rebalance_price_universe_panel.py
P4: build_rebalance_factor_score_panel.py --engine duckdb
P5: run_qvm_walkforward.py --factor-score-panel
```

The legacy path remains the reference implementation. Use it for validation,
audit, debugging, unsupported strategy mechanics, and fallback. It is not
deprecated for correctness, but it is deprecated for daily research usage when
the DuckDB path supports the strategy.

## Supported Fast Scope

The DuckDB factor/score engine currently supports base Q/V/M-style factor sets
and the documented strategy versions in `docs/factor_score_panel.md`.
Unsupported custom factor definitions, unsupported scoring modes, and
unsupported filters must either use `--engine legacy` explicitly or fail with a
clear error. Silent fallback is not allowed because it makes parity and runtime
interpretation ambiguous.

## Validation Policy

Synthetic tests protect public contracts. Private workspaces should add
sampled real-data audits after major engine changes. Those audits should
compare:

```text
panel-level fields
summary, ignoring cache_fingerprint only
trades
holdings
equity
failure cases
benchmark columns when supplied
```

A sampled external real-data audit has passed panel-level and walk-forward
parity for the supported DuckDB factor-score path. The detailed artifacts,
timing records, paths, tickers, and strategy results remain private.

## Retirement Policy

Do not delete the legacy path yet. Keep it while new strategy primitives are
still evolving and while the fast path does not cover every documented
strategy mechanic.

Legacy usage should shrink over time as fast-path coverage improves:

```text
1. Add or update synthetic contract tests for the public behavior.
2. Add invariant tests for PIT dates, missing data, filters, ranking, and
   output schemas.
3. Run random-window real-data audits in private workspaces after material
   engine changes.
4. Promote a fast-path capability only after panel and walk-forward parity
   are clean or differences are explicitly documented.
5. Retire legacy code only when fast-path coverage, contract tests, and audit
   history are sufficient for the affected feature.
```

Until then, legacy remains available as a reference and fallback, but the
recommended daily research route for supported workflows is the DuckDB
factor-score panel consumed directly by walk-forward.
