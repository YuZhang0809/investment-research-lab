# Group Beta Research Primitives

This document covers the first public-safe group beta research layer. It is a
generic engine capability for sectors, industries, regions, size buckets, or
synthetic theme-like groups. It does not encode private groups, private
allocation weights, real holdings, real reports, or strategy conclusions.

The current scope is sidecar group research:

```text
group membership -> group basket returns -> group signal panel
  -> group allocation panel -> security look-through targets -> group attribution
```

These tools do not modify `run_qvm_walkforward.py`, do not place orders, and do
not encode private group definitions or allocation parameters.

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
- `liquidity_weight`, using monetary `trading_value`
- `volume_weight`, using share `volume`
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
security-level orders.

## Group Allocation

`build_group_allocation_panel.py` consumes group signals and produces
benchmark-relative group target weights:

```powershell
python scripts\build_group_allocation_panel.py `
  --group-signals data\processed\groups\group_signals.parquet `
  --benchmark-weights data\synthetic\group_benchmark_weights.csv `
  --score-field group_return_6p `
  --mode score_tilt `
  --active-budget 0.10 `
  --max-active-weight 0.05 `
  --max-turnover 0.10 `
  --out data\processed\groups\group_allocation.parquet `
  --no-manifest
```

Supported allocation modes:

- `score_tilt`: start from benchmark weights and add a centered score tilt.
- `top_n_equal`: allocate equally to the top `--top-n` scored groups.
- `inverse_volatility`: allocate by inverse volatility using `--vol-field`.
  When `--top-n` is supplied, groups are selected by score first, then weighted
  by inverse volatility.

Supported controls:

- `--max-group-weight`
- `--max-active-weight`
- `--max-total-active-weight`
- `--max-turnover`
- `--group-type-cap GROUP_TYPE=MAX_WEIGHT`
- `--cash-weight`

Output contract:

```text
rebalance_date,group_type,group_id,group_name,benchmark_weight,active_weight,target_weight,current_weight,trade_weight,score,constraint_status,constraint_reasons
```

If no benchmark-weight file is supplied, the builder uses an equal-weight group
benchmark for that rebalance date. If no current-weight file is supplied, it
uses the previous target weights, with the first date starting from the
benchmark. Constraint fields are audit fields; clipped weight is not silently
redistributed unless the specific control defines a proportional scale.
Some controls can be mutually infeasible, for example a tight turnover cap and
a much lower maximum group weight. In that case the final row keeps the
deterministic target produced by the ordered cap pipeline and reports
`constraint_status=violation` with a `final_*_violation` reason.

## Look-Through Targets

`expand_group_allocation_to_security_targets.py` expands group target weights
back to security-level look-through weights through the membership panel:

```powershell
python scripts\expand_group_allocation_to_security_targets.py `
  --group-allocation data\processed\groups\group_allocation.parquet `
  --membership-panel data\synthetic\group_membership.csv `
  --weighting-mode equal_weight `
  --single-name-cap 0.05 `
  --out data\processed\groups\group_lookthrough_targets.parquet `
  --no-manifest
```

Output contract:

```text
rebalance_date,code,target_weight,source_group_count,source_groups,lookthrough_constraint_status,lookthrough_constraint_reasons
```

If a security belongs to multiple groups, contributions are summed and
`source_groups` records the group-level contributions. `--single-name-cap`
clips the look-through target and reports `single_name_cap`; it does not
redistribute the excess. This keeps the diagnostic separate from executable
portfolio construction.
`custom_weight` look-through uses the membership-panel field named by
`--custom-weight-field` and does not require price rows.

## Group Attribution

`analyze_group_allocation_attribution.py` joins prior group allocation weights
to subsequent group basket returns:

```powershell
python scripts\analyze_group_allocation_attribution.py `
  --group-allocation data\processed\groups\group_allocation.parquet `
  --basket-returns data\processed\groups\group_basket_returns.parquet `
  --out data\processed\groups\group_allocation_attribution.parquet `
  --no-manifest
```

Output contract:

```text
date,allocation_date,group_type,group_id,group_name,target_weight,benchmark_weight,active_weight,group_return,portfolio_contribution,benchmark_contribution,active_contribution,missing_flags
```

For a return date, the attribution uses the latest allocation date strictly
before that return date. Same-date allocations are not used for same-period
returns.
