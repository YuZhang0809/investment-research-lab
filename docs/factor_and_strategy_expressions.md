# Factor And Strategy Expressions

This note documents the public-safe expression layer used by the research
engine. It borrows the small useful ideas from Qlib, Zipline Pipeline, and
Alphalens without adopting their full platform model.

## Design References

- Qlib: config-defined feature fields and processor-style transforms.
- Zipline Pipeline: separate Factors from Filters and keep filters auditable.
- Alphalens: factor diagnostics should remain separate from portfolio results.

The implementation is intentionally smaller than those projects. This repository
keeps a local, file-first workflow and only adds generic primitives.

## Configured Factor Definitions

`build_factors.py` supports optional factor definitions under:

```yaml
factors:
  definitions:
    - name: profit_margin_proxy
      group: quality
      expr: ratio(net_profit, operating_profit)
      include_in_score: true

    - name: recent_return
      group: momentum
      expr: ts_return(lookback=63, skip=0)
      include_in_score: false
```

Definitions are evaluated by a whitelist AST evaluator, not Python `eval`.
Supported scalar functions are:

```text
abs
avg
clamp
log
max
min
ratio
sqrt
where
```

The factor stage also exposes `ts_return(lookback, skip=0)`, which uses the
same trading-calendar-aware return code as the built-in momentum factors.
Use `skip=0` only when the rebalance timing makes the rebalance-date close
available before signal formation. With `execution_price: rebalance_close`, a
same-day close-based factor can be a look-ahead unless the private workflow has
a separate, defensible timing convention.

Allowed variables are the public factor-stage fields, such as:

```text
latest_unadjusted_close
market_cap
operating_profit
net_profit
equity
total_assets
shares
operating_profit_to_total_assets
equity_to_assets
earnings_yield
book_to_market
return_12_1
return_6_1
```

Configured factor names must be simple identifiers and cannot overwrite
reserved output fields.

Definitions are dependency-checked before row evaluation. A definition may
reference another configured factor even if the dependency is listed later in
the YAML file; the engine builds a small DAG and evaluates dependencies first.
Unknown variables, unsupported function names, and cyclic dependencies fail
fast with `ValueError`.

Missing inputs propagate through expressions. Arithmetic functions, comparison
operators, boolean operators, and `where()` return `None` when the result cannot
be determined from available point-in-time data. This keeps configured factors
aligned with the built-in missing flag behavior.

## Scoring Modes

Existing strategy versions remain available:

```text
value_only
qv
qvm
value_dominant_quality_filter_momentum_exclusion
weighted_groups
```

New generic configs should prefer:

```powershell
--strategy-version configurable
```

with one of these scoring modes:

```yaml
strategy:
  scoring:
    mode: weighted_groups
    weights:
      quality: 0.4
      value: 0.4
      momentum: 0.2
```

or:

```yaml
strategy:
  scoring:
    mode: weighted_factors
    weights:
      earnings_yield: 0.5
      return_6_1: 0.5
```

`weighted_factors` combines z-scored factor columns directly. This is useful for
synthetic research experiments and generic public engine mechanics. Real
winning factor weights belong in a private workspace.

## Filters

Filters run after score calculation and before ranking. Filtered rows remain in
the score output with audit columns:

```text
filter_status
filter_reasons
missing_score_components
```

Supported rules:

```yaml
strategy:
  filters:
    - group: momentum
      rule: exclude_bottom_pct
      pct: 20

    - field: earnings_yield
      rule: exclude_bottom_pct
      pct: 10

    - field: composite_score
      rule: exclude_below
      value: 0

    - field: earnings_yield_z
      rule: exclude_below
      value: 0

    - field: value_score
      rule: require_not_missing
```

`group` maps to `quality_score`, `value_score`, or `momentum_score`. `field`
may reference a score output field. Percentile filters may also reference a raw
factor name, which is resolved to that factor's z-score column because
percentile ordering is unitless. Threshold filters such as `exclude_below` and
`exclude_above` do not auto-resolve raw factor names; use an explicit z-score
field such as `earnings_yield_z` when the threshold is in z-score units.

Unknown weighted factor fields and unknown filter fields fail fast with
`ValueError` instead of producing an empty ranked output.

## Factor Diagnostics

`analyze_factor_forward_returns.py` writes three public-safe artifacts:

```text
factor_forward_returns_<range>_<holding>d.csv
alphalens_factor_data_<range>_<holding>d.csv
factor_forward_returns_<range>_<holding>d.md
```

The summary CSV keeps one row per factor and rebalance date. It includes IC,
rank IC, top/bottom bucket returns, quantile returns, top quantile turnover,
rank autocorrelation, coverage, and missing-data counters.

The `alphalens_factor_data` CSV is a file-first adapter for Alphalens-style
analysis. It uses one row per `(date, asset, factor)` observation and includes:

```text
date
asset
factor
factor_value
forward_return_<holding>d
factor_quantile
group
sector
name
forward_status
```

`factor_quantile` follows the Alphalens convention that `1` is the lowest factor
bucket and the highest number is the strongest factor bucket. `group` is mapped
from `sector` so a downstream notebook can compute grouped IC or grouped
returns without changing the public data pipeline.

The Markdown report is still intentionally lightweight. It is closer to an
Alphalens tear sheet than the earlier report because it now surfaces IC,
quantile returns, turnover, rank autocorrelation, and monthly diagnostics, but
it does not claim sector or size neutralization.

## Cache Fingerprints

Walkforward factor cache fingerprints include both the full `factors.definitions`
block and per-factor definition fingerprints. Each factor fingerprint covers the
definition name, group, scoring inclusion flag, expression text, and immediate
dependency fingerprints, so a changed upstream definition also moves dependent
definition fingerprints. This keeps cache invalidation auditable today and
leaves a clean path to split factor caches by definition later.

## Boundary

Public configs should demonstrate syntax only. They should not encode private
research conclusions, selected tickers, winning parameter choices, candidate
lists, go/no-go decisions, or real portfolio outputs.
