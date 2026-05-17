# Experiment Workflow

The workflow is quant-first. The first job is to make a research idea runnable;
the second job is to keep the result inspectable enough to avoid obvious
mistakes.

## Standard QVM Flow

1. Define or update the experiment config in `configs/`.
2. Convert J-Quants, vendor, or synthetic input into the CSV contracts in
   `docs/data_contracts.md`.
3. Validate the minimum input contract and lookback coverage.
4. Build a point-in-time universe.
5. Build raw QVM factors.
6. Build normalized scores and ranks.
7. Convert ranks into research targets and executable targets.
8. Build order constraints and failure cases.
9. Run a single snapshot or walk-forward simulation.
10. Generate candidate, failure-case, and performance reports.
11. Review the output before changing factor rules or paper trading.

## Phase 1 Workflow Target

Phase 1 should make the real-data path work under agent orchestration. A single
runner command is useful later, but it is not required for the first research
loop.

```powershell
python scripts\validate_jquants.py --preflight-only
python scripts\download_jquants.py <local research options>
python scripts\build_universe.py <contract data options>
python scripts\build_factors.py <contract data options>
python scripts\build_scores.py <factor options>
python scripts\build_targets.py <score and universe options>
python scripts\run_qvm_walkforward.py <contract data options>
```

The exact command sequence can be run by an agent and should remain explicit
until the repeated workflow is stable enough to justify a wrapper.

## Learning Loop

For a personal research project, strategy discovery should stay incremental:

1. Start with a simple QVM baseline.
2. Run a small local J-Quants-derived data sample.
3. Use synthetic fixtures only for public no-key smoke tests.
4. Inspect candidates and failure cases.
5. Change one research rule at a time.
6. Compare monthly and quarterly variants.
7. Only then consider additional strategy families or external frameworks.

The default workflow is research-only. Live trading and auto-ordering are
outside the scope of this repository.
