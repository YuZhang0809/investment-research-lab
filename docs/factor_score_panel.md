# Factor/Score Panel

This is the preferred public-safe research path for precomputing
rebalance-level factors and scores when the strategy fits the documented
DuckDB support scope. It is not a strategy rewrite and it does not change
portfolio construction, execution, benchmark accounting, or reporting.

The legacy engine remains the reference implementation for validation, audit,
and fallback. It is not deprecated for correctness, but it is no longer the
recommended daily research path for supported base factor/score panels. See
`docs/engine_path_policy.md` for the path policy and retirement approach.

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
duckdb: recommended research path for supported base Q/V/M panels; computes
        PIT fundamentals, ratios, z-scores, group scores, filters, and ranks in
        one DuckDB batch
legacy: reference, validation, and fallback path; reuses build_factors.py and
        build_scores.py
```

The DuckDB engine is intentionally narrower than the legacy engine. It supports
the base Q/V/M raw factors and `qvm`, `qv`, `value_only`, and `weighted_groups`
strategy versions with group-level filters. It rejects `factors.definitions`,
`strategy.group_relative_transforms`, `configurable` / `weighted_factors`, and
field-level filters with a clear error. Use `--engine legacy` explicitly for
those mechanics. There is no automatic fallback because a silent semantic change
would make parity harder to trust.

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

The CLI default remains `--engine legacy` for backward compatibility. Daily
research-scale runs should pass `--engine duckdb` when the strategy is in the
supported scope.
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

Sector caps are portfolio-construction rules, not factor/score-panel rules.
Use `portfolio.sector_cap` or the matching `run_qvm_walkforward.py` CLI
overrides to cap the executable portfolio after ranks are read. Do not pre-edit
panel ranks or candidate lists to simulate a cap; that bypasses the buy/hold
buffer and changes turnover, cost, and tax behavior. The research basket remains
the uncapped top-ranked basket unless a future explicit research-basket cap is
added.

Affordable-lot filtering follows the same boundary. Use
`portfolio.affordable_lot_filter` to skip names whose minimum lot cannot fit the
executable target allocation, then continue to the next ranked candidate. Do not
rewrite factor-score panel ranks to remove expensive names.

Group-relative factor transforms are factor/score rules, not portfolio
construction rules. The legacy panel engine supports
`strategy.group_relative_transforms` and writes fields such as
`sector_relative_book_to_market_z` and
`sector_relative_book_to_market_rank_pct` into the factor-score panel. These
fields can then drive `weighted_factors` scoring or field filters. The DuckDB
panel engine does not support this primitive yet and fails explicitly when it
is configured.

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
<group_relative_output_prefix>_<field>_z
<group_relative_output_prefix>_<field>_rank_pct
```

Configured factor definitions and configurable scoring modes are available in
the legacy engine through the same `build_factors.py` and `build_scores.py`
mechanics used by the original path.

## Required Parity

Before adopting the fast path for a new strategy primitive, and after major
engine changes, compare three paths on the same inputs and parameters:

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
the fast path for research. A sampled external real-data audit has passed
panel-level and walk-forward parity for the supported DuckDB factor-score path;
the public repository records only that abstract result, not private paths,
tickers, reports, candidate lists, returns, or timing commitments. Public
examples and tests must stay synthetic.
