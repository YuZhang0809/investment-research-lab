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
