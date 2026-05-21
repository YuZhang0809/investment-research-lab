# Price-Volume Factor Panel Profile

`scripts/profile_price_volume_factor_panel.py` profiles engineering behavior for
the price-volume proxy factor builder. It is a public-safe scale and memory
check, not a strategy result.

## What It Measures

The profile writes a CSV summary and a Markdown report with:

- input price rows and optional universe-panel rows
- output factor-panel rows
- rebalance date count
- runtime
- Python `tracemalloc` peak memory
- output panel memory
- `missing_flags` row rate
- `coverage_flags` row rate
- `vwap_proxy_flag` counts

`tracemalloc` does not capture every native allocation used by pandas or
pyarrow. For full-market private runs, also watch process RSS from the operating
system.

## Synthetic Smoke

Use synthetic data to test shape and runtime without committing vendor data:

```powershell
python scripts\profile_price_volume_factor_panel.py `
  --synthetic-codes 400 `
  --synthetic-days 756 `
  --synthetic-rebalances 36 `
  --work-dir data\scratch\price_volume_profile `
  --summary-out reports\engineering\price_volume_factor_panel_profile.csv `
  --report reports\engineering\price_volume_factor_panel_profile.md `
  --no-manifest
```

The default synthetic profile is intentionally small enough for local smoke
checks. Increase `--synthetic-codes`, `--synthetic-days`, and
`--synthetic-rebalances` gradually before trying full-market private data.

## Full-Market Private Use

For large local data, pass explicit rebalance dates and a universe panel:

```powershell
python scripts\profile_price_volume_factor_panel.py `
  --prices <local_daily_ohlcv.parquet> `
  --universe-panel <local_rebalance_universe_panel.parquet> `
  --rebalance-dates <local_rebalance_dates.csv> `
  --group-field sector `
  --panel-out <local_price_volume_factor_panel.parquet> `
  --summary-out reports\engineering\price_volume_factor_panel_profile.csv `
  --report reports\engineering\price_volume_factor_panel_profile.md `
  --no-manifest
```

Keep private paths, real tickers, and generated full-market panels outside the
public repo.

## Memory Controls

- Prefer Parquet for repeated large runs.
- Always pass `--rebalance-dates` or repeated `--rebalance-date` for full-market
  profiles.
- Prefer `--universe-panel` so output rows are limited to
  `rebalance_date x included codes`.
- Do not rely on the builder default of all price dates for full-market runs.
- Treat 32 GB memory as an environment constraint to verify with this profile,
  not as a public performance promise.
