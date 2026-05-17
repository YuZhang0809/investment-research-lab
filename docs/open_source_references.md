# Open Source References

This project should learn from open-source quant tools without turning into a
full platform. Libraries are adopted only when they make the personal research
loop simpler, clearer, or more reliable.

## Use or Consider Soon

### pandas and numpy

Use as the internal computation layer for tabular data, joins, rolling windows,
ranking, and statistics.

Adoption rule:

- keep CSV inputs and outputs as the public contract
- avoid hand-writing fragile table operations when pandas is clearer

### J-Quants API Client

Use the official J-Quants Python client for API access.

Adoption rule:

- normalize outputs into this repository's CSV contracts
- keep credentials outside git
- keep raw downloads ignored
- keep strategy code independent from the client package by using local adapter
  scripts

Reference:

- `https://github.com/J-Quants/jquants-api-client-python`

## Borrow Ideas From

### Alphalens Reloaded

Use as a reference for factor research reports:

- forward returns
- quantile returns
- IC and Rank IC
- factor turnover
- factor tear sheets

Adoption rule:

- borrow report structure first
- only integrate directly if the data format friction is low

Reference:

- `https://github.com/stefan-jansen/alphalens-reloaded`

### QuantStats

Use as a reference for performance and risk reporting:

- drawdown
- volatility
- Sharpe-like metrics
- rolling statistics
- tear sheet layout

Adoption rule:

- reports should remain decision-oriented and concise
- do not turn the product into a dashboard

Reference:

- `https://github.com/ranaroussi/quantstats`

### vectorbt

Use as a comparison tool for fast signal and portfolio experiments.

Good fit:

- quick signal tests
- parameter sweeps
- sanity-checking return calculations

Adoption rule:

- keep it optional
- compare against the local QVM pipeline before replacing any core logic

Reference:

- `https://vectorbt.dev/`

## Time-Boxed Spikes

### Microsoft Qlib

Qlib is worth studying if the project moves toward ML-based alpha research.

Spike questions:

- Can local Japan equity contract data be converted cleanly into Qlib format?
- Does Qlib make ML factor research materially faster?
- Does Qlib preserve enough transparency for personal research?
- What parts of Qlib are useful without adopting the full platform?

Adoption rule:

- do not make Qlib a core dependency for the QVM baseline
- isolate experiments under `spikes/qlib/` or similar

Reference:

- `https://github.com/microsoft/qlib`

### bt

Useful to study portfolio-level rebalancing APIs.

Adoption rule:

- reference its portfolio construction concepts
- avoid replacing the simple QVM runner unless it clearly reduces complexity

Reference:

- `https://github.com/pmorissette/bt`

## Study Only for Now

### Backtrader and Zipline Reloaded

Useful for understanding event-driven backtesting, orders, broker abstractions,
and analyzers.

Not a Phase 1 fit because this project is not trying to become a trading engine.

References:

- `https://www.backtrader.com/`
- `https://github.com/stefan-jansen/zipline-reloaded`

### QuantConnect LEAN

Useful as a professional-grade architecture reference.

Not a Phase 1 fit because it is too large for a local personal research
workbench.

Reference:

- `https://github.com/QuantConnect/Lean`

### skfolio

Useful later if the project moves beyond equal-weight or simple constrained
portfolios.

Reference:

- `https://github.com/skfolio/skfolio`

### TA-Lib

Useful later for technical indicators if the research roadmap includes RSI,
MACD, Bollinger Bands, or other technical strategies.

Reference:

- `https://ta-lib.org/`

### FinRL

Useful as a learning reference for reinforcement learning in finance.

Not a near-term dependency. Reinforcement learning is out of scope until the
basic factor research loop is stable.

Reference:

- `https://github.com/AI4Finance-Foundation/FinRL`

## Adoption Checklist

Before adding any external library as a dependency, answer:

1. Does it make the Phase 1 research loop simpler?
2. Does it preserve local reproducibility?
3. Does it keep CSV contracts inspectable?
4. Does it support Japan equity constraints or stay isolated from them?
5. Does it avoid pulling the project toward live trading or platform scope?
6. Can it be removed without rewriting the whole project?
