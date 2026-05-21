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

## Group-Relative Transforms

`strategy.group_relative_transforms` adds generic within-group factor
standardization before filters and ranking. This is different from
`portfolio.sector_cap`: sector caps constrain portfolio construction after
ranks are read, while group-relative transforms change the score inputs.

Example:

```yaml
strategy:
  group_relative_transforms:
    - group_field: sector
      fields:
        - book_to_market
        - earnings_yield
      methods:
        - zscore
        - rank_pct
      min_group_size: 5
      output_prefix: sector_relative
  scoring:
    mode: weighted_factors
    weights:
      sector_relative_book_to_market_z: 1.0
```

The transform groups rows by `rebalance_date + group_field` and only sees rows
that are already included in the universe stage. Excluded panel rows are kept
for auditability but do not affect group means, standard deviations, or ranks.
Blank group values are treated as `UNKNOWN`.

Output fields follow this pattern:

```text
<output_prefix>_<field>_z
<output_prefix>_<field>_rank_pct
```

`zscore` uses the group's population standard deviation. If the group has fewer
than `min_group_size` valid values, the field is missing. If the group's
standard deviation is zero, the z-score is missing. `rank_pct` is in `[0, 1]`,
where higher source factor values receive higher percentiles.

Group-relative output fields can be referenced by `weighted_factors` and by
field filters:

```yaml
strategy:
  filters:
    - field: sector_relative_book_to_market_rank_pct
      rule: exclude_bottom_pct
      pct: 20
```

`group_field` is not hard-coded to `sector`; it may be any discrete field that
exists in the factor rows, such as `market`, `sector`, or a generic
classification column. Missing group fields or missing source fields fail fast.
The legacy factor/score path and the DuckDB factor-score builder both support
this primitive for supported panel fields. Custom expression-generated fields
remain on the legacy reference path until the DuckDB expression engine has its
own parity coverage.

## External Factor Panels

`external_factor_panels` joins generic point-in-time fields into factor rows
before scoring. The join can be keyed by `rebalance_date + code` or by
`rebalance_date + <group field>` such as `sector`. As-of mode uses
`available_date <= rebalance_date` and never uses future rows.

Joined fields can be referenced by `weighted_factors` and by field filters:

```yaml
external_factor_panels:
  - name: synthetic_risk_flags
    path: data/processed/external/synthetic_risk_flags.parquet
    join_keys: [rebalance_date, code]
    fields:
      - name: risk_score
        dtype: float
      - name: risk_flag
        dtype: string

strategy:
  scoring:
    mode: weighted_factors
    weights:
      risk_score: 1.0
  filters:
    - field: risk_flag
      rule: exclude_equals
      value: blocked
```

See `docs/external_factor_panels.md` for the full contract and validation
command. The DuckDB factor-score builder supports documented exact and as-of
external joins for supported panel fields; custom factor expressions still use
the legacy reference path.

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

    - field: risk_flag
      rule: exclude_equals
      value: blocked

    - field: risk_bucket
      rule: require_in
      values: [low, medium]

    - field: risk_score
      rule: exclude_above_pct
      pct: 90
```

`group` maps to `quality_score`, `value_score`, or `momentum_score`. `field`
may reference a score output field. Percentile filters may also reference a raw
factor name, which is resolved to that factor's z-score column because
percentile ordering is unitless. Threshold filters such as `exclude_below` and
`exclude_above` do not auto-resolve raw factor names; use an explicit z-score
field such as `earnings_yield_z` when the threshold is in z-score units.
String filters support `exclude_equals`, `exclude_in`, `require_equals`, and
`require_in`. Numeric percentile-threshold filters support
`exclude_above_pct` and `exclude_below_pct`, where the percentile threshold is
computed from currently passing rows.

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
from `--group-field` when supplied, otherwise from `sector`.

Use `--factor-file <panel.csv|panel.parquet>` to analyze an explicit generated
panel such as `build_price_volume_factor_panel.py` output. Explicit factor files
may contain multiple `rebalance_date` values and do not need to follow the
`factors_YYYYMM.*` naming convention.

When `--grouped-diagnostics --group-field <field>` is supplied, the script also
writes:

```text
factor_forward_returns_grouped_<range>_<holding>d.csv
factor_forward_returns_grouped_<range>_<holding>d.md
```

The grouped output reports IC, rank IC, top/bottom spread, coverage, missing
factor rate, and missing forward-return rate separately for each
`rebalance_date + factor + group`. `--group-neutral-quantiles` optionally assigns
factor quantiles inside each group instead of across the full cross-section.

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
