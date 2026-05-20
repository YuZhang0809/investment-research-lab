# External Factor Panels

External factor panels are generic, public-safe inputs for research fields that
are not produced by the base universe/factor builders. Examples include market
state flags, liquidity risk labels, short-pressure proxies, or human-reviewed
risk flags. This contract is intentionally neutral; private workspaces provide
their own files and configs at runtime.

## Config

```yaml
external_factor_panels:
  - name: synthetic_risk_flags
    path: data/processed/external/synthetic_risk_flags.parquet
    join_keys:
      - rebalance_date
      - code
    fields:
      - name: margin_long_to_volume
        dtype: float
      - name: risk_flag
        dtype: string
```

Sector or market-level joins use a different discrete key:

```yaml
external_factor_panels:
  - name: synthetic_sector_pressure
    path: data/processed/external/synthetic_sector_pressure.csv
    join_keys:
      - rebalance_date
      - sector
    fields:
      - name: sector_short_selling_ratio
        dtype: float
```

Supported dtypes are `float`, `int`, `string`, and `bool`. The legacy factor
stage validates required fields and duplicate keys before joining. Joined
fields are written to factor outputs and factor-score panels, can be used by
`weighted_factors`, and can be used by field filters.

## As-Of Join

As-of mode uses the latest external row whose availability date is no later
than the rebalance date:

```yaml
external_factor_panels:
  - name: synthetic_asof_flags
    path: data/processed/external/synthetic_asof_flags.parquet
    join_keys:
      - rebalance_date
      - code
    fields:
      - name: synthetic_risk_score
        dtype: float
    asof:
      enabled: true
      date_field: available_date
      max_lag_days: 30
```

Future rows are never used. If the latest available row is older than
`max_lag_days`, the joined field is missing and is added to `missing_flags`.
Duplicate keys fail by default. `duplicate_policy: latest_available_date` is
reserved for as-of panels where the availability timestamp is the intended
canonicalization key. It does not choose among different availability dates;
as-of joins already keep distinct dates and choose the latest date no later than
the rebalance date. The policy only permits duplicate rows with the same match
key and availability date to be canonicalized by file order.

## Validation

Validate a synthetic or private-generated panel before wiring it into a run:

```powershell
python scripts\validate_external_factor_panel.py `
  --panel data\processed\external\synthetic_risk_flags.parquet `
  --join-key rebalance_date `
  --join-key code `
  --field margin_long_to_volume:float `
  --field risk_flag:string
```

For as-of panels, add:

```powershell
  --asof-date-field available_date
```

The validator checks required columns, field dtypes, blank keys, invalid dates,
and duplicate contract keys. It does not download data and does not encode any
private endpoint, ticker, candidate list, or research conclusion.

## Fast-Path Support

The legacy factor/score path supports external factor panels. The DuckDB
factor-score builder currently rejects `external_factor_panels` with a clear
error instead of silently ignoring the join or falling back to legacy. Use
`--engine legacy` for configs that require external joins until DuckDB support
is implemented and parity-tested.
