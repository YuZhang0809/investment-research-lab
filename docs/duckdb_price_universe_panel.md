# DuckDB Price/Universe Panel

This is the upstream acceleration layer for the public research engine. It is
not a rewrite of the research engine and it is not a database service. The
DuckDB price/universe panel feeds the DuckDB factor/score panel, which is the
recommended downstream builder for supported daily research workflows. The
legacy CSV/list-of-dicts path remains the reference implementation for
validation, audit, and fallback.

## Scope

`scripts/build_rebalance_price_universe_panel.py` builds a
`rebalance_date x code` panel with:

```text
rebalance dates
PIT listing snapshot selection
lifecycle include/exclude checks
latest price on or before rebalance
rebalance-date price availability
IPO age in trading rows
configured liquidity lookback median trading value
12-1 and 6-1 adjusted returns
included flag and exclusion reason
```

It intentionally does not compute fundamentals latest-as-of rows, quality/value
ratios, scores, portfolios, or reports. The next layer is
`scripts/build_rebalance_factor_score_panel.py --engine duckdb`, documented in
`docs/factor_score_panel.md`.

## Usage

```powershell
python scripts\build_rebalance_price_universe_panel.py `
  --config configs\qvm_v0_1.example.yml `
  --listings data\processed\listings.csv `
  --prices data\processed\prices.csv `
  --fundamentals data\processed\fundamentals.csv `
  --start-date 2020-01-31 `
  --end-date 2026-03-31 `
  --frequency monthly `
  --out data\processed\factors\rebalance_price_universe_panel.parquet `
  --input-format auto `
  --output-format parquet
```

CSV remains supported, but Parquet is the preferred format for repeated local
research because DuckDB can scan it efficiently.

## Legacy Parity

Use `scripts/compare_fast_panel_to_legacy.py` before trusting a fast panel:

```powershell
python scripts\compare_fast_panel_to_legacy.py `
  --config configs\qvm_v0_1.example.yml `
  --listings data\processed\listings.csv `
  --prices data\processed\prices.csv `
  --fundamentals data\processed\fundamentals.csv `
  --fast-panel data\processed\factors\rebalance_price_universe_panel.parquet `
  --start-date 2020-01-31 `
  --end-date 2026-03-31 `
  --frequency monthly `
  --out reports\engineering\fast_panel_diff.csv
```

The diff output is field-level:

```text
field,rebalance_date,code,legacy_value,fast_value,difference_type
```

No rows means the compared fields match the legacy reference for that fixture.

## Lifecycle Caveat

The panel carries `listing_lifecycle_status`, `last_trading_date`, and
`lifecycle_exit_date`, but it does not promote inferred lifecycle data to
validation-grade PIT delisting coverage. Snapshot panels and inferred lifecycle
still need the same data-gate caveats as the legacy path.

## Optimization Direction

The goal is local columnar computation, not GPU acceleration and not a
persistent database dependency. Private workspaces can use real local data to
measure speedups, but public docs and tests should stay synthetic and should
not promise private runtime numbers.

## Validation Handoff

The public fast path is organized as:

```text
P3: price/universe panel
P4: DuckDB factor/score panel for supported strategy mechanics
P5: walk-forward direct consumption of the factor/score panel
```

P3 is the upstream acceleration layer. P4 is the default downstream panel
builder where the strategy fits the documented DuckDB support scope. P5 should
consume the factor/score panel for research-scale runs so the walk-forward
engine skips per-rebalance universe/factor/score stage builds.

Private workspaces should keep validating fast-path changes with local real
data, especially after major engine changes. Keep private paths, vendor files,
run outputs, selected tickers, parameters, returns, and conclusions outside
this repository.

Recommended validation experiment:

1. Build the fast panel with the same config, listings, prices, fundamentals,
   start date, end date, and rebalance frequency used by the legacy run.
2. Compare it against the legacy helpers with `compare_fast_panel_to_legacy.py`.
3. Review every diff row before trusting runtime improvements.
4. Build the DuckDB factor/score panel with `--engine duckdb`.
5. Run walk-forward with `--factor-score-panel`.
6. Compare legacy and fast walk-forward artifacts on sampled windows.
7. Treat unexplained field, portfolio, order, equity, or benchmark differences
   as blockers.

Fields that should match or have a documented explanation:

```text
rebalance_date
code
latest_price_date
latest_unadjusted_close
adjusted_close
rebalance_price_available
price_staleness_trading_days
ipo_age_trading_days
median_60d_trading_value
return_12_1
return_6_1
included_flag
exclusion_reason
has_fundamentals
```

Suggested private commands use placeholder paths only:

```powershell
python scripts\build_rebalance_price_universe_panel.py `
  --config path\to\config.yml `
  --listings path\to\listings.csv `
  --prices path\to\prices.csv `
  --fundamentals path\to\fundamentals.csv `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --frequency monthly `
  --out path\to\fast_panel.parquet `
  --input-format auto `
  --output-format parquet

python scripts\compare_fast_panel_to_legacy.py `
  --config path\to\config.yml `
  --listings path\to\listings.csv `
  --prices path\to\prices.csv `
  --fundamentals path\to\fundamentals.csv `
  --fast-panel path\to\fast_panel.parquet `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --frequency monthly `
  --out path\to\fast_panel_diff.csv
```

For research-scale runs, build the factor/score panel and consume it directly:

```powershell
python scripts\build_rebalance_factor_score_panel.py `
  --config path\to\config.yml `
  --price-universe-panel path\to\fast_panel.parquet `
  --prices path\to\prices.csv `
  --fundamentals path\to\fundamentals.csv `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --frequency monthly `
  --strategy-version qvm `
  --engine duckdb `
  --out path\to\factor_score_panel.parquet `
  --output-format parquet

python scripts\run_qvm_walkforward.py `
  --config path\to\config.yml `
  --listings path\to\listings.csv `
  --prices path\to\prices.csv `
  --fundamentals path\to\fundamentals.csv `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --frequency monthly `
  --factor-score-panel path\to\factor_score_panel.parquet `
  --out-dir path\to\fast_walkforward_outputs `
  --report-dir path\to\fast_walkforward_reports
```

This keeps portfolio construction, benchmark accounting, holdings, equity, and
failure-case logic in the existing walk-forward engine while replacing the slow
per-rebalance universe/factor/score stage builds. Compare the fast run against
a legacy run with the same config and runtime parameters before relying on a
new strategy primitive.

Walk-forward parity should cover:

```text
summary
trades
holdings
equity
failure cases
benchmark and market benchmark columns when supplied
```

`cache_fingerprint` may differ because the input source changed. Other
differences need a documented explanation.

A sampled external real-data audit has passed panel-level and walk-forward
parity for the supported DuckDB factor-score path. Public docs intentionally
avoid private file paths, tickers, reports, candidate lists, returns, selected
parameters, and private timing commitments.

## Fast Path Layers

The public engine now has three explicit fast-path layers:

```text
P3: price/universe panel
P4: factor/score panel
P5: walk-forward direct panel consumption
```

P3 is the `rebalance_date x code` price/universe acceleration layer documented
above. P4 is documented in `docs/factor_score_panel.md`; the DuckDB engine is
the recommended downstream builder for supported base Q/V/M-style factor/score
panels. P5 is the explicit `run_qvm_walkforward.py` consumption path:

```text
--price-universe-panel
--factor-score-panel
```

All fast paths must keep passing legacy parity on synthetic fixtures and
sampled private local windows after material engine changes.
