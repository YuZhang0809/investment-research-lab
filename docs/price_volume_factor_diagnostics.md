# Price-Volume Factor Diagnostics

Generated price-volume panels can feed `scripts/analyze_factor_forward_returns.py`
directly. This lets proxy fields get the same Alphalens-style diagnostics as
other factor panels without adding a strategy config or portfolio run.

## Build A Synthetic Panel

```powershell
python scripts\build_price_volume_factor_panel.py `
  --prices <synthetic_daily_ohlcv.csv> `
  --rebalance-date 2026-02-15 `
  --rebalance-date 2026-02-28 `
  --group-field sector `
  --out data\processed\factors\synthetic_price_volume_panel.parquet `
  --output-format parquet `
  --no-manifest
```

## Run Diagnostics

`--factor-file` accepts an explicit CSV or Parquet factor panel. The file does
not need to be named `factors_YYYYMM.*`, and it may contain multiple
`rebalance_date` values.

```powershell
python scripts\analyze_factor_forward_returns.py `
  --factor-file data\processed\factors\synthetic_price_volume_panel.parquet `
  --prices <synthetic_daily_ohlcv.csv> `
  --start-date 2026-02-15 `
  --end-date 2026-02-28 `
  --holding-days 5 `
  --factor wq_alpha_005_proxy `
  --factor wq_alpha_011_proxy `
  --factor wq_alpha_101_proxy `
  --grouped-diagnostics `
  --group-field sector `
  --out-dir data\processed\factor_analysis `
  --report-dir reports\factor_analysis `
  --no-manifest
```

Outputs:

```text
factor_forward_returns_<range>_<holding>d.csv
alphalens_factor_data_<range>_<holding>d.csv
factor_forward_returns_<range>_<holding>d.md
factor_forward_returns_grouped_<range>_<holding>d.csv
factor_forward_returns_grouped_<range>_<holding>d.md
```

The diagnostics report IC, rank IC, quantile returns, turnover, coverage,
missing factor rate, and missing forward-return rate. Grouped diagnostics split
those measures by a discrete field such as `sector` or `market`.

## Boundary

These diagnostics are research primitives. They do not imply that a proxy field
is predictive, and they should not be used to brute-force a large factor zoo.
Public examples must use synthetic data only.
