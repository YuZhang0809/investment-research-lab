# DuckDB Price/Universe Panel

This is a public-safe fast path for the slowest cold-run data construction
steps. It is not a rewrite of the research engine and it is not a database
service. The legacy CSV/list-of-dicts path remains the reference
implementation.

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
ratios, scores, portfolios, or reports. Those remain in later phases after the
price/universe panel proves parity with the legacy path.

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

## Private Validation Handoff

This public upgrade covers Phase 1 and Phase 2 only:

```text
Phase 1: DuckDB price/universe fast panel
Phase 2: synthetic parity against the legacy universe and momentum/liquidity path
```

The fast path currently accelerates the rebalance-date price/universe panel. It
does not replace the legacy engine and does not compute fundamentals
latest-as-of, quality/value ratios, scores, portfolios, or walk-forward
backtests. The legacy path remains the reference for research semantics.

Private workspaces should validate the fast path with local real data before
any Phase 3 work begins. Keep private paths, vendor files, run outputs,
selected tickers, parameters, and conclusions outside this repository.

Recommended private experiment:

1. Build the fast panel with the same config, listings, prices, fundamentals,
   start date, end date, and rebalance frequency used by the legacy run.
2. Compare it against the legacy helpers with `compare_fast_panel_to_legacy.py`.
3. Review every diff row before trusting runtime improvements.
4. Record runtime separately for the fast panel build and the comparison step.
5. Treat unexplained field differences as blockers for Phase 3.

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

After the private Phase 1/2 parity run has no unexplained differences, the
next engineering step is to run the full walk-forward with the fast panel as
the universe-stage input:

```powershell
python scripts\run_qvm_walkforward.py `
  --config path\to\config.yml `
  --listings path\to\listings.csv `
  --prices path\to\prices.csv `
  --fundamentals path\to\fundamentals.csv `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --frequency monthly `
  --price-universe-panel path\to\fast_panel.parquet `
  --out-dir path\to\fast_walkforward_outputs `
  --report-dir path\to\fast_walkforward_reports
```

This only replaces the universe stage. Fundamentals, raw factors, scoring,
portfolio construction, benchmark accounting, and reports still use the
existing walk-forward logic. Compare the fast run against a legacy run with the
same config and runtime parameters before using the fast path for research.

Walk-forward parity should cover:

```text
summary
trades
holdings
equity
failure cases
benchmark and market benchmark columns when supplied
```

`cache_fingerprint` may differ because the universe input source changed. Other
differences need a documented explanation.

Later planned phases remain:

```text
broader DuckDB-native fundamentals latest-as-of optimization
broader DuckDB-native raw Q/V/M factor optimization
```

## Fast Path Layers

The public engine now has two explicit fast-path layers:

```text
P3: price/universe panel
P4: factor/score panel
P5: walk-forward direct panel consumption
```

P3 is the `rebalance_date x code` price/universe acceleration layer documented
above. P4 is documented in `docs/factor_score_panel.md`; it precomputes factor
and score rows by reusing the existing `build_factors.py` and
`build_scores.py` semantics. P5 is the explicit `run_qvm_walkforward.py`
consumption path:

```text
--price-universe-panel
--factor-score-panel
```

All fast paths must pass legacy parity on synthetic fixtures and private local
data before they are used for research.
