# Price Defensive Factor Panel

`scripts/build_price_defensive_factor_panel.py` creates generic defensive price
factors from adjusted daily prices. It is a public-safe factor primitive and
does not encode private universes, thresholds, or conclusions.

## Inputs

Minimum price fields:

```text
date,code,adjusted_close
```

If `adjusted_close` is missing, the script can synthesize an effective adjusted
close from `unadjusted_close` and positive `adjustment_factor`, matching the
public engine's split-adjusted return convention.

Optional benchmark input:

```text
date,adjusted_close
```

Accepted benchmark value aliases are `adjusted_close`, `close`, `index_value`,
`value`, and `price`.

## Output

The output is keyed by:

```text
rebalance_date,code
```

It includes:

```text
realized_vol_3m
realized_vol_6m
realized_vol_12m
downside_vol_6m
downside_vol_12m
max_drawdown_6m
max_drawdown_12m
beta_to_benchmark
latest_price_stale
price_limit_flag
defensive_filter_reasons
missing_flags
```

Volatility fields are annualized with 252 trading days. Downside volatility is
the annualized root mean square of negative daily returns. Drawdown fields use
the worst peak-to-trough adjusted-close drawdown inside the window. Beta is
computed from paired daily returns against the supplied benchmark.

Example:

```powershell
python scripts\build_price_defensive_factor_panel.py `
  --prices <synthetic_prices.csv> `
  --market-benchmark-prices <synthetic_topix.csv> `
  --rebalance-date 2026-03-31 `
  --stale-filter-days 1 `
  --flag-price-limit `
  --out data\processed\factors\price_defensive_202603.parquet `
  --output-format parquet `
  --no-manifest
```

The panel can be joined through `external_factor_panels` using an exact
`rebalance_date + code` join. Rows with insufficient history keep the key and
audit fields while the unavailable factor values remain blank and are listed in
`missing_flags`.
