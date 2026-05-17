# Architecture

This project is a local quant research workbench, not a trading platform.

The architecture is optimized for a personal investor who is still learning and
testing research ideas. The system should make the quant loop runnable first,
then keep the outputs inspectable enough to catch common research mistakes.

## Design

```text
J-Quants, vendor, or synthetic data
  -> contract CSVs
  -> optional validation
  -> point-in-time universe
  -> factors
  -> scores and ranks
  -> research targets
  -> executable targets and order constraints
  -> walk-forward summary
  -> candidate and failure-case report
```

The system is intentionally CSV-first. It favors transparent intermediate files
over database infrastructure while the research rules are still changing. CSV
is the user-facing contract; internal computation may use libraries such as
`pandas` and `numpy` when they reduce code complexity.

## Core Modules

- Data adapters normalize vendor or synthetic inputs into CSV contracts.
- Universe builders apply point-in-time eligibility rules.
- Factor builders compute raw QVM variables.
- Score builders normalize and rank candidates.
- Portfolio builders convert ranks into research and executable targets.
- Walk-forward runners simulate low-frequency rebalance loops.
- Reports explain candidates, constraints, and failure cases.

The common path should remain easy for an agent to run script-by-script. A
single QVM research runner can be added later if repeated manual use justifies
it.

## Boundaries

The public engine owns:

- data contracts
- reproducible transformations
- scoring and portfolio construction logic
- low-frequency research simulations
- candidate and failure-case reports
- failure-case logs

The public engine must not own:

- personal allocation policy
- live holdings
- broker account records
- tax lots from a real account
- vendor data redistribution
- live trading or broker execution
- a general-purpose backtesting platform

## Private Integration

A private workspace can call this project as a tool by passing local config and
data paths. The dependency direction should remain one way: private workspaces
may depend on the public research engine, but the public engine must not depend
on private files.

## External Libraries

Open-source quant libraries should be used as references or optional adapters
until the QVM baseline is stable.

- `pandas` and `numpy` are acceptable as computation tools.
- vectorbt, Alphalens, and QuantStats are useful comparison or reporting
  references.
- Qlib is worth a time-boxed ML research spike, but should not replace the QVM
  baseline in Phase 1.
- Backtrader, Zipline Reloaded, and QuantConnect LEAN are architecture
  references for trading engines, not near-term core dependencies.
