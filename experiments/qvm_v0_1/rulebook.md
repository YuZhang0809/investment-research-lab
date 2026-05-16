# Japan Small/Micro QVM v0.1 Rulebook

This experiment is research-only. It does not generate live orders.

## Objective

Test whether a simple Quality + Value + Momentum ranking process has research
value in Japan small and micro-cap equities after point-in-time, lot-size,
liquidity, cost, tax, and failure-case constraints.

## Prohibitions

- No shorting
- No leverage
- No intraday trading
- No auto-ordering
- No parameter changes based only on a single good backtest
- No random train/test split for future walk-forward ML work

## Data Rules

- Use point-in-time available data only.
- Financial data must pass an `available_date` gate.
- Price returns may use adjusted close.
- Order sizing must use unadjusted price and 100-share lots.
- Every derived output should be reproducible from config plus input files.

## Factors

```text
qvm_score = 0.4 * quality_score + 0.4 * value_score + 0.2 * momentum_score
```

Initial raw variables:

- Quality: `operating_profit_to_total_assets`, `equity_to_assets`
- Value: `earnings_yield`, `book_to_market`
- Momentum: `return_12_1`, `return_6_1`

## Portfolio Rules

- Research portfolio: top basket for signal validation.
- Executable portfolio: constrained by capital, 100-share lots, ADV cap, and
  cash drag.
- Buy threshold: top 10% or top 50.
- Hold threshold: top 20% or top 100.

## Execution Rules

- Default execution assumption: next trading day open.
- Round order size down to 100-share lots.
- Cap order value by recent median trading value.
- Mark price-limit or missing-price cases explicitly.

## Go / No-Go

The experiment can advance only when results are reproducible, failure cases are
understood, and after-cost results do not rely only on optimistic assumptions.
