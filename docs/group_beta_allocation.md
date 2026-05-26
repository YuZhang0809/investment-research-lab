# Group Beta Research Primitives

This document covers the first public-safe group beta research layer. It is a
generic engine capability for sectors, industries, regions, size buckets, or
synthetic theme-like groups. It does not encode private groups, private
allocation weights, real holdings, real reports, or strategy conclusions.

The current scope is data and signal research:

```text
group membership -> group basket returns -> group signal panel
```

Group allocation, look-through expansion, and benchmark attribution are planned
as later layers.

## Group Membership

Validate membership panels before using them:

```powershell
python scripts\validate_group_membership_panel.py `
  --panel data\synthetic\group_membership.csv
```

Exact snapshot contract:

```text
rebalance_date,code,group_type,group_id,group_name,membership_weight,purity_score,source,notes
```

As-of contract:

```text
code,available_date,group_type,group_id,group_name,membership_weight,purity_score,source,notes
```

Rules:

- `group_type` is a neutral label such as `sector`, `industry`, `theme`, or
  `custom_group`.
- `group_id` is the stable join key; `group_name` is display text.
- `membership_weight` defaults to `1.0` when blank and must be positive when
  supplied.
- `purity_score` is optional and must be in `[0, 1]` when supplied.
- Exact duplicate keys fail by default. Use `--duplicate-policy aggregate` only
  when duplicate memberships are intentionally additive.
- As-of membership carries the latest row for each
  `code + group_type + group_id` with `available_date <= rebalance_date`.

Synthetic theme examples should use neutral identifiers like `theme_a` and
`theme_b`.

## Basket Returns

Build group basket returns from daily prices and membership:

```powershell
python scripts\build_group_basket_return_panel.py `
  --prices data\synthetic\prices.csv `
  --membership-panel data\synthetic\group_membership.csv `
  --date 2026-01-31 `
  --date 2026-02-28 `
  --weighting-mode equal_weight `
  --out data\processed\groups\group_basket_returns.parquet `
  --no-manifest
```

Supported weighting modes:

- `equal_weight`
- `liquidity_weight`, using `trading_value` or `volume`
- `market_cap_weight`, using an explicit market-cap price field
- `custom_weight`, using a membership-panel field passed by
  `--custom-weight-field`

Output contract:

```text
date,group_type,group_id,group_name,constituent_count,weighting_mode,basket_return,basket_value,turnover,coverage,missing_return_count,top_constituent_weight,weight_concentration
```

Basket period returns use prices available on or before each observation date.
Weights for a return period are based on the prior observation date, so the
return row does not use future membership or future price data.

## Group Signals

Build group-level signals from basket returns, optional single-name factor
panels, optional external group panels, and an optional benchmark:

```powershell
python scripts\build_group_signal_panel.py `
  --basket-returns data\processed\groups\group_basket_returns.parquet `
  --membership-panel data\synthetic\group_membership.csv `
  --factor-panel data\synthetic\single_name_factors.csv `
  --factor-aggregation book_to_market:weighted_mean `
  --factor-aggregation roe:median `
  --external-panel data\synthetic\group_risk_flags.csv `
  --external-field risk_state `
  --external-asof-date-field available_date `
  --market-benchmark data\synthetic\market_benchmark.csv `
  --rebalance-date 2026-02-28 `
  --out data\processed\groups\group_signals.parquet `
  --no-manifest
```

Built-in basket-return signals:

- `group_return_<N>p`
- `group_vol_<N>p`
- `group_downside_vol_<N>p`
- `group_max_drawdown_<N>p`
- `group_beta_to_benchmark`

Single-name factor aggregations use current membership weights and support:

- `mean`
- `median`
- `weighted_mean`
- `coverage_rate`
- `pNN`, for example `p25` or `p75`

Output contract:

```text
rebalance_date,group_type,group_id,group_name,coverage,constituent_count,<signal_fields>,<aggregated_factor_fields>,<external_fields>,missing_flags
```

The signal panel is a research input. It does not produce allocation weights or
security-level orders. Later layers should consume this panel to build
benchmark-relative group allocations and look-through diagnostics.
