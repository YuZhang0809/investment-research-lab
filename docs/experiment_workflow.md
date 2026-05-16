# Experiment Workflow

1. Define or update the experiment config in `configs/`.
2. Convert vendor or synthetic input into the CSV contracts in `docs/data_contracts.md`.
3. Build a point-in-time universe.
4. Build raw QVM factors.
5. Build normalized scores and ranks.
6. Convert ranks into research targets and executable targets.
7. Build order constraints and failure cases.
8. Run a single snapshot or walk-forward simulation.
9. Generate a report.
10. Review failure cases before changing factor rules.

The default workflow is research-only. Live trading and auto-ordering are
outside the scope of this repository.
