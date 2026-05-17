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

    - field: value_score
      rule: require_not_missing
```

`group` maps to `quality_score`, `value_score`, or `momentum_score`. `field`
may reference a score output field or a raw factor name; raw factor names are
resolved to their z-score columns when needed.

## Boundary

Public configs should demonstrate syntax only. They should not encode private
research conclusions, selected tickers, winning parameter choices, candidate
lists, go/no-go decisions, or real portfolio outputs.
