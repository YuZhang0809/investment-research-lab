# Factor Registry

This is the public registry for factor-stage fields and expression helpers. It
is deliberately generic: examples are synthetic mechanics, not research
conclusions.

## Built-In Factor Fields

| field | group | meaning |
|---|---|---|
| `operating_profit_to_total_assets` | quality | Operating profit divided by total assets. |
| `equity_to_assets` | quality | Equity divided by total assets. |
| `earnings_yield` | value | Net profit divided by market capitalization. |
| `book_to_market` | value | Equity divided by market capitalization. |
| `return_12_1` | momentum | Calendar-aware 12 month return with a 1 month skip. |
| `return_6_1` | momentum | Calendar-aware 6 month return with a 1 month skip. |

## Raw Expression Inputs

Configured factors may reference these point-in-time numeric inputs:

```text
latest_unadjusted_close
market_cap
operating_profit
net_profit
equity
total_assets
shares
```

They may also reference built-in factor fields and other configured factor
definitions. The engine builds a dependency graph, evaluates dependencies first,
and rejects unknown variables or cycles before row evaluation.

## Expression Functions

| function | behavior |
|---|---|
| `abs(x)` | Absolute value; returns missing when `x` is missing. |
| `avg(a, b, ...)` | Average of available numeric inputs; missing only if all inputs are missing. |
| `clamp(x, low, high)` | Bounds `x` between `low` and `high`; missing if any argument is missing. |
| `log(x)` | Natural log; missing when `x <= 0` or missing. |
| `max(a, b, ...)` | Maximum available numeric input. |
| `min(a, b, ...)` | Minimum available numeric input. |
| `ratio(a, b)` | `a / b`; missing when either side is missing or denominator is zero. |
| `sqrt(x)` | Square root; missing when `x < 0` or missing. |
| `where(condition, a, b)` | Chooses `a` or `b`; missing when the condition is missing. |
| `ts_return(lookback, skip=0)` | Factor-stage only; calendar-aware trailing return. |

Arithmetic, comparisons, boolean operators, and `where()` preserve missing data.
That means a missing condition does not silently become `False`.

## Strategy Expression Surface

`strategy.scoring.mode: weighted_groups` combines group scores:

```yaml
strategy:
  scoring:
    mode: weighted_groups
    weights:
      quality: 0.4
      value: 0.4
      momentum: 0.2
```

`strategy.scoring.mode: weighted_factors` combines z-scored factor fields
directly:

```yaml
strategy:
  scoring:
    mode: weighted_factors
    weights:
      quality_blend: 0.5
      value_blend: 0.3
      return_6_1: 0.2
```

Filters are audit-friendly primitives. Percentile filters may refer to a raw
factor field because ordering is unitless. Threshold filters must refer to the
actual score field or an explicit `_z` field.

```yaml
strategy:
  filters:
    - field: return_6_1
      rule: exclude_bottom_pct
      pct: 20
    - field: quality_blend_z
      rule: exclude_below
      value: -1
```

## Diagnostics

Use `scripts/analyze_factor_forward_returns.py` to generate:

```text
factor_forward_returns_*.csv
alphalens_factor_data_*.csv
factor_forward_returns_*.md
```

The Alphalens-style CSV is intentionally a plain file adapter. It avoids a hard
dependency on Alphalens while preserving the useful shape: `date`, `asset`,
`factor_value`, forward return, quantile, and group.
