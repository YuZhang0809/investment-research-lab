# Performance Reporting

This note documents the public-safe performance, risk, and reporting layer.
The goal is to make walk-forward outputs easier to review without turning this
repository into a dashboard product.

## Design References

- QuantStats: compact portfolio metrics, drawdown views, and HTML tear sheets.
- empyrical: common financial risk metrics and rolling-metric conventions.
- pyfolio-reloaded: returns, positions, transactions, and tear sheet structure.
- Alphalens: keep factor diagnostics separate from portfolio performance.

The public engine borrows these reporting shapes, but keeps the implementation
file-first and dependency-light.

## Output Contracts

`generate_walkforward_tearsheet.py` reads existing walk-forward CSV outputs:

```text
qvm_walkforward_summary_*.csv
qvm_walkforward_failure_cases_*.csv
```

It writes:

```text
walkforward_tearsheet.md
walkforward_tearsheet_metrics.csv
walkforward_tearsheet_charts/equity_curve.svg
walkforward_tearsheet_charts/drawdown.svg
walkforward_tearsheet_charts/implementation.svg
```

The metrics CSV uses this schema:

```text
category,metric,value,formatted_value
```

The SVG charts are static artifacts. They are intended for local inspection and
Markdown reports, not for an interactive dashboard.

## Metrics

The current generic metrics include:

- total return
- annualized return
- annualized volatility
- Sharpe ratio
- Sortino ratio
- Calmar ratio
- maximum drawdown
- longest drawdown length in sampled periods
- win rate
- best and worst sampled-period return
- benchmark total return
- active total return
- beta
- alpha
- tracking error
- information ratio
- correlation
- up and down capture
- average cash
- average turnover
- average holdings
- average zero-lot targets
- average skipped orders
- cost drag
- tax drag
- failure-case counts

## Sampling Caveat

Metrics are computed at the walk-forward rebalance frequency. A monthly
walk-forward has monthly return samples; a quarterly walk-forward has quarterly
return samples. Sharpe, Sortino, volatility, beta, information ratio, and
drawdown should only be compared across runs with compatible sampling.

Do not present monthly sampled risk as daily risk.

Annualized and risk-adjusted metrics are suppressed when the sampled period
count is too small. The current public thresholds are:

```text
daily: 60 periods
weekly: 26 periods
monthly: 12 periods
quarterly: 8 periods
yearly: 5 periods
```

When a run is below the threshold, `risk_metric_status` is written as:

```text
insufficient_sample_count
```

Total return, final equity, drawdown, implementation metrics, and failure-case
counts are still reported because they describe what happened in the run rather
than estimating an annualized or risk-adjusted statistic.

Sharpe, Sortino, and downside deviation currently assume a 0 risk-free/MAR
rate. This is explicit in the tear sheet caveats.

Drawdown uses initial capital as the starting peak. A first-period loss is
therefore visible as drawdown instead of being reset to zero at the first
post-rebalance equity point.

## Boundary

Public reports should summarize generic engine behavior only. They must not
include private run ledgers, private candidate lists, real selected tickers,
private go/no-go decisions, personal portfolio values, broker exports, or
private dashboard files.

Interactive dashboards belong in private workspaces because they are likely to
combine real strategy results, private parameters, and real holdings.
