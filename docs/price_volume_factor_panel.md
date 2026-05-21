# Price-Volume Factor Panel

`scripts/build_price_volume_factor_panel.py` builds generic price-volume proxy
features from daily OHLCV data. The output is an external factor panel keyed by
`rebalance_date + code`, so existing `external_factor_panels`, filters,
weighted scores, and walk-forward paths can consume it without changing
portfolio construction.

## Boundary

This tool is inspired by common WorldQuant 101 style operators, but it is not a
full reproduction of the 101 original formulas. Output fields are named
`wq_alpha_<id>_proxy` deliberately:

- they are generic research features, not alpha claims
- they should not be treated as 101 standalone strategies
- they are suitable for filters, diagnostics, weak score inputs, and execution
  timing experiments
- industry neutralization, subindustry hierarchy, and full 101 formula coverage
  are out of scope for v0.1

## Inputs

Minimum useful price columns:

```text
date,code,adjusted_close,unadjusted_open,unadjusted_high,unadjusted_low,unadjusted_close,volume,trading_value,price_limit_flag
```

J-Quants-style aliases such as `Date`, `Code`, `LocalCode`,
`AdjustmentClose`, `Open`, `High`, `Low`, `Close`, `Volume`, and
`TurnoverValue` are accepted where possible.

Optional:

- `--rebalance-date` / `--rebalance-dates`; defaults to all price dates
- `--universe-panel` with `rebalance_date + code` to restrict output rows
- `--group-field` to preserve a discrete group column from the universe panel
  or price rows when present; universe-panel values take precedence and no
  neutralization is performed in v0.1

Duplicate `(code, date)` price rows fail fast.

## Features

Base derived fields:

```text
returns
price_staleness_calendar_days
dollar_volume
adv20
adv60
vwap_proxy
intraday_return
range_position
candle_pressure
close_to_vwap
high_low_range
```

Proxy fields:

```text
wq_alpha_005_proxy
wq_alpha_011_proxy
wq_alpha_012_proxy
wq_alpha_024_proxy
wq_alpha_028_proxy
wq_alpha_032_proxy
wq_alpha_033_proxy
wq_alpha_034_proxy
wq_alpha_041_proxy
wq_alpha_042_proxy
wq_alpha_043_proxy
wq_alpha_047_proxy
wq_alpha_053_proxy
wq_alpha_057_proxy
wq_alpha_060_proxy
wq_alpha_083_proxy
wq_alpha_101_proxy
```

Audit fields:

```text
missing_flags
coverage_flags
vwap_proxy_flag
operator_version
```

`vwap_proxy` is `trading_value / volume`. If volume or trading value is missing,
the field is blank and the issue is surfaced in `vwap_proxy_flag` and
`missing_flags`.

## Usage

```powershell
python scripts\build_price_volume_factor_panel.py `
  --prices <synthetic_daily_prices.csv> `
  --rebalance-date 2026-03-31 `
  --out data\processed\factors\price_volume_factors_202603.parquet `
  --output-format parquet `
  --no-manifest
```

Join it through existing config:

```yaml
external_factor_panels:
  - name: price_volume
    path: data/processed/factors/price_volume_factors_202603.parquet
    join_keys:
      - rebalance_date
      - code
    fields:
      - name: wq_alpha_101_proxy
        dtype: float
      - name: wq_alpha_005_proxy
        dtype: float
```

Public tests and examples must stay synthetic. Private workspaces can generate
the panel from local full-market daily data and pass the output into the
existing research pipeline at runtime.
