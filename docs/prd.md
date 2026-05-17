# Product Requirements

## Product

Investment Research Lab is a local, public-safe quant research workbench for
personal Japan equity research.

The product goal is not to become a general quant platform. The goal is to help
a personal investor learn, test, and refine low-frequency equity research ideas
by producing:

- clean J-Quants or contract research inputs
- point-in-time candidate universes
- factor values and ranks
- research and executable target portfolios
- simple walk-forward results
- clear reasons for selected and rejected names

Research comes first. Validation and auditability are built into the workflow,
but they should not become the main product.

## Target User

The target user is a personal investor or solo researcher who:

- is still learning what strategies to test
- wants to start with simple, explainable factor research
- works locally with private data and vendor data
- needs reproducible results without maintaining a full platform
- wants to avoid future data leakage, execution fantasy, and accidental data
  disclosure

## Core User Jobs

1. Convert local J-Quants, vendor, or synthetic data into stable CSV contracts.
2. Run a QVM-style research loop for a date or date range.
3. See which stocks rank highly and why.
4. See whether ranked names are executable for a personal capital size.
5. Compare simple walk-forward results against a baseline.
6. Review failure cases before changing rules or paper trading.
7. Keep private holdings, broker data, API keys, and vendor snapshots outside
   this public repository.

## Phase 1 Outcome

Phase 1 is successful when an agent or user can run the existing scripts on
local J-Quants-derived contract data and get a complete QVM research package:

```text
inputs
  -> validation summary
  -> point-in-time universe and exclusions
  -> raw factors
  -> normalized scores and ranks
  -> research targets
  -> executable targets and constraints
  -> walk-forward summary
  -> candidate and failure-case report
```

The first version may be script-by-script and agent-orchestrated. It must be
reproducible, inspectable, and easy to rerun in the same local workspace.

## Functional Requirements

### Data Contracts

- Accept listings, daily prices, and fundamentals in the CSV contracts defined
  in `docs/data_contracts.md`.
- Support local J-Quants data conversion as the primary real-data path.
- Treat `fundamentals.available_date` as the point-in-time gate.
- Keep generated raw and processed data out of git by default.
- Register derived outputs in a local manifest unless disabled.

### Research Pipeline

- Build a point-in-time universe for a rebalance date.
- Generate QVM factors from prices and fundamentals.
- Normalize factor values and produce ranks.
- Build equal-weight research targets from ranks.
- Convert research targets into executable targets using lot size and capital.
- Run low-frequency walk-forward simulations for monthly and quarterly
  rebalancing.
- Emit failure cases for missing data, insufficient history, liquidity, lot
  size, skipped orders, cash drag, and execution constraints.

### Agent-Orchestrated Workflow

- Keep stage scripts composable and easy for an agent to run.
- Prefer clear script inputs and outputs over a premature orchestration layer.
- Add a single-command runner later only if repeated local use proves it is
  worth the extra surface area.

### Candidate Explanation

For each selected or rejected name, the system should make the reason visible:

- rank and QVM score
- quality, value, and momentum sub-scores
- key raw factor values
- missing components
- universe inclusion or exclusion reason
- target shares and target value
- executable status
- constraint reason

### Reporting

- Produce concise Markdown reports first.
- Reports should support quant research decisions, not marketing-style
  performance presentation.
- The first report should emphasize candidate quality, execution constraints,
  walk-forward summary, and failure cases.

## Non-Functional Requirements

- Local-first: no hosted service dependency.
- Public-safe: no real holdings, broker exports, API keys, vendor snapshots, or
  private reports in git.
- Lightweight: avoid platform dependencies until the research loop proves it
  needs them.
- Inspectable: CSV outputs should remain the primary contract and review layer.
- Reproducible: every run should be tied to input paths, date ranges, config,
  checksums, and a run label.
- Conservative: prefer explicit rules and small scripts over broad abstractions.

## Non-Goals

This project should not become:

- a live trading system
- a broker integration
- a general event-driven backtester
- a dashboard-first app
- a full data platform or model registry
- a real tax-lot accounting system; research simulations may include rough tax
  estimates, but they are not tax records
- a multi-user SaaS product
- a strategy plugin marketplace
- an automated ML or reinforcement-learning platform

## Open Questions

- Which simple strategy families should be tested after QVM v0.1?
- Should `pandas` and `numpy` become core dependencies for internal
  computation while preserving CSV inputs and outputs?
- Should Qlib be used only for an ML research spike, or should it remain a
  reference project for now?
- What is the smallest useful candidate report for a real monthly paper
  rebalance?
- Should the J-Quants path use the existing lightweight adapter, the official
  client, or both?
