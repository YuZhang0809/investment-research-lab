# Generic Margin and Leverage Model

This public engine implements broker-neutral long-margin research mechanics.
It does not encode broker-specific parameters, private account settings,
private strategy conclusions, real tickers, or real run results.

Margin is disabled by default. Cash-only runs keep the existing accounting
path unless a config or CLI override enables margin.

## Config

```yaml
margin:
  enabled: true
  account_type: margin_long
  target_gross_leverage: 1.0
  max_gross_leverage: 1.0
  annual_borrow_rate: 0.03
  initial_margin_requirement: 0.50
  maintenance_margin_requirement: 0.25
  minimum_required_equity: 100000
  interest_day_count: 365
  margin_call_action: flag_only
```

The field names are intentionally generic:

- `target_gross_leverage`: desired gross exposure divided by account equity.
- `max_gross_leverage`: hard cap used during target sizing and buy-order cash checks.
- `initial_margin_requirement`: caps target leverage at `1 / initial_margin_requirement`.
- `maintenance_margin_requirement`: daily diagnostic breach threshold.
- `annual_borrow_rate`: annualized financing rate used for borrowed value.
- `minimum_required_equity`: account-equity diagnostic threshold.
- `margin_call_action`: currently supports `flag_only`. Forced deleveraging is reserved for a later engine version.

## Accounting

For each valuation row, the engine reports:

```text
gross_exposure
borrowed_value
net_account_equity
gross_leverage
margin_ratio
financing_cost_period
financing_cost_cumulative
portfolio_equity_after_cost_after_financing
```

Borrowing and interest are computed as:

```text
borrowed_value = max(gross_exposure - account_equity, 0)
interest = borrowed_value * annual_borrow_rate * days_held / interest_day_count
```

Financing cost reduces cash and after-cost equity. It is reported separately
from execution costs and estimated tax so downstream analysis can split cost
drag, tax drag, and financing drag.

## Target Sizing

When margin is enabled, equal-weight target sizing uses target gross exposure:

```text
target_gross_exposure = targetable_equity * effective_target_gross_leverage
effective_target_gross_leverage = min(target_gross_leverage, max_gross_leverage, 1 / initial_margin_requirement)
```

Margin does not bypass existing constraints. Lot size, affordable-lot filters,
ADV caps, spread/slippage/commission, sector caps, buy/hold buffers, tax lots,
and failure-case reporting still apply.

## Daily Diagnostics

Quarterly or monthly rebalance rows are not enough to understand margin risk.
When margin is enabled, `run_qvm_walkforward.py` writes:

```text
qvm_walkforward_margin_daily_<token>.csv
qvm_walkforward_margin_summary_<token>.csv
```

Daily rows mark the held portfolio using daily close prices and report:

```text
date,rebalance_date,gross_exposure,borrowed_value,account_equity,gross_leverage,margin_ratio,maintenance_margin_breach,minimum_equity_breach
```

The summary file reports:

```text
min_margin_ratio,margin_breach_count,first_margin_breach_date,minimum_equity_breach_count,max_margin_gross_leverage,avg_margin_gross_leverage,max_borrowed_value,cumulative_financing_cost
```

`margin_call_action: flag_only` only reports breaches. It is not an executable
forced-liquidation model and should not be treated as a live broker simulation.

## CLI Overrides

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --margin-enabled `
  --target-gross-leverage 1.5 `
  --max-gross-leverage 2.0 `
  --annual-borrow-rate 0.03 `
  --initial-margin-requirement 0.5 `
  --maintenance-margin-requirement 0.25 `
  --minimum-required-equity 100000 `
  --margin-call-action flag_only
```

Use synthetic or private runtime configs for actual parameter values. Public
examples should remain generic and synthetic.
