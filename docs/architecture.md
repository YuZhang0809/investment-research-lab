# Architecture

This project is a local research engine, not a trading platform.

## Design

```text
vendor or synthetic inputs
  -> contract CSVs
  -> universe builder
  -> factor builder
  -> score builder
  -> target builder
  -> order builder
  -> backtest or walk-forward report
```

The system is intentionally CSV-first. It favors transparent intermediate files
over database infrastructure while the research rules are still changing.

## Boundaries

The public engine owns:

- data contracts
- reproducible transformations
- scoring and portfolio construction logic
- simulation reports
- failure-case logs

The public engine must not own:

- personal allocation policy
- live holdings
- broker account records
- tax lots from a real account
- vendor data redistribution

## Private Integration

A private workspace can call this project as a tool by passing local config and
data paths. The dependency direction should remain one way: private workspaces
may depend on the public research engine, but the public engine must not depend
on private files.
