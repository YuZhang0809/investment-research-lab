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
10. Run data-quality, benchmark, and strategy diagnostics.
11. Generate candidate, failure-case, and performance reports.
12. Append the run summary to a private run ledger when the result is worth
    remembering.
13. Generate a lightweight decision note for REVIEW, REJECT, or PAPER_TEST
    candidates.
14. Review the output before changing factor rules or paper trading.

The public repository provides only generic templates and scripts for this
recordkeeping. Real ledgers, real protocols, real decision notes, and real
conclusions stay private.

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

## Experiment Notes

Before an important run, copy `experiments/experiment_protocol.example.md` into
a private workspace and write down the question, hypothesis, phase, periods,
planned variants, metrics, and reject conditions. This is meant to prevent
post-hoc storytelling; it is not an approval document.

After a walk-forward run, `scripts/append_run_record.py` can append one row from
a summary CSV into a private CSV ledger. It records hashes, period, strategy
label, execution settings, headline metrics, market alpha/beta fields,
hypothesis, predefined metrics, go/no-go criteria, and a lightweight decision
label:

```text
EXPLORATORY
REVIEW
REJECT
PAPER_TEST
```

`scripts/generate_decision_note.py` can turn a ledger row or summary CSV into a
Markdown research note with a decision, short reason, key metrics, caveats, and
next action.

Generic diagnostics are split into explicit file-first tools:

```powershell
python scripts\audit_data_quality.py --prices <prices.csv> --listings <listings.csv>
python scripts\analyze_benchmark_attribution.py --summary <summary.csv> --benchmark size=<size_benchmark.csv>
python scripts\generate_strategy_diagnostics_pack.py --summary <summary.csv> --data-quality-summary <audit_summary.csv>
```

These scripts consume supplied artifacts only. They do not infer missing
diagnostics from unrelated files.

These notes are deliberately not:

- an audit system
- a compliance workflow
- an approval process
- an immutable log
- a database or dashboard
