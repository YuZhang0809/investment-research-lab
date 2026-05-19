# Factor/Score Panel

This is a public-safe fast path for precomputing rebalance-level factors and
scores after the DuckDB price/universe panel has passed parity checks. It is
not a strategy rewrite and it does not change portfolio construction,
execution, benchmark accounting, or reporting.

## Scope

`scripts/build_rebalance_factor_score_panel.py` consumes a validated
price/universe panel and builds a reusable factor/score panel:

```text
price/universe panel rows
included universe rows
factor/score computation
merged rebalance-level factor/score panel
```

Excluded rows can remain in the panel for auditability, but only
`included_flag=true` rows are passed to factor and score computation. This keeps
non-universe names from leaking into ranked candidates.

Two engines are available:

```text
legacy: default/reference path; reuses build_factors.py and build_scores.py
duckdb: optimized base Q/V/M path; computes PIT fundamentals, ratios, z-scores,
        group scores, filters, and ranks in one DuckDB batch
```

The DuckDB engine is intentionally narrower than the legacy engine. It supports
the base Q/V/M raw factors and `qvm`, `qv`, `value_only`, and `weighted_groups`
strategy versions with group-level filters. It rejects `factors.definitions`,
`configurable` / `weighted_factors`, and field-level filters with a clear error.
There is no automatic fallback because a silent semantic change would make
parity harder to trust.

## Usage

```powershell
python scripts\build_rebalance_factor_score_panel.py `
  --config configs\qvm_v0_1.example.yml `
  --price-universe-panel data\processed\factors\rebalance_price_universe_panel.parquet `
  --prices data\processed\prices.csv `
  --fundamentals data\processed\fundamentals.csv `
  --start-date 2020-01-31 `
  --end-date 2026-03-31 `
  --frequency monthly `
  --strategy-version qvm `
  --engine duckdb `
  --out data\processed\factors\rebalance_factor_score_panel.parquet `
  --output-format parquet
```

Omit `--engine duckdb` to use the legacy reference builder.
CSV remains supported, but Parquet is preferred for repeated local research.

## Walk-Forward Consumption

After the panel is built, `run_qvm_walkforward.py` can consume it directly:

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2020-01-31 `
  --end-date 2026-03-31 `
  --frequency monthly `
  --factor-score-panel data\processed\factors\rebalance_factor_score_panel.parquet `
  --cache-format parquet `
  --no-manifest
```

This skips the per-rebalance universe/factor/score stage builds and uses the
panel's included rows and score fields. Portfolio construction, orders,
holdings, equity, failure cases, and benchmark columns still come from the
existing walk-forward engine.

## Minimum Fields

The output keeps existing factor and score field names where possible:

```text
rebalance_date
code
included_flag
exclusion_reason
latest_price_date
latest_unadjusted_close
adjusted_close
median_60d_trading_value
return_12_1
return_6_1
has_fundamentals
fundamentals_available_date
fundamentals_available_time
period_end
document_type
operating_profit_to_total_assets
equity_to_assets
earnings_yield
book_to_market
quality_score
value_score
momentum_score
composite_score
qvm_score
rank_score
rank
candidate_rank
filter_status
filter_reasons
missing_flags
missing_score_components
<raw_factor>_z
```

Configured factor definitions and configurable scoring modes are available in
the legacy engine through the same `build_factors.py` and `build_scores.py`
mechanics used by the original path.

## Required Parity

Before research use, compare three paths on the same inputs and parameters:

```text
A. legacy walk-forward
B. --price-universe-panel walk-forward
C. --factor-score-panel walk-forward
```

Expected parity:

```text
summary: identical except cache_fingerprint
trades: identical
holdings: identical
equity: identical
failure cases: identical
benchmark and market benchmark columns: identical when supplied
```

Any unexplained portfolio, order, equity, or benchmark difference blocks use of
the fast path for research. Public examples and tests must stay synthetic.
