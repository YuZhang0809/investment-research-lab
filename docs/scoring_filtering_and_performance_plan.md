# Scoring, Filtering, And Performance Plan

This note defines the next small public-engine improvement. It is intentionally
not a strategy framework. The goal is to remove a few hardcoded scoring choices
and speed up repeated walk-forward runs without turning the project into a
general quant platform.

## Scope

The public engine should implement generic mechanics only:

- configurable factor-group weights
- one or more simple factor-tail filters
- clearer score/filter output columns
- better cache reuse for grid-style research runs

The public engine must not encode private strategy conclusions, winning
parameters, real candidate lists, or real benchmark results. Private workspaces
pass real configs and real data at runtime.

## Non-Goals

Do not build:

- a strategy DSL
- a plugin system
- a full backtesting framework
- a dashboard or workflow platform
- a portfolio optimizer
- custom replacements for pandas, NumPy, pyarrow, DuckDB, or regression
  libraries

If a feature cannot be explained with one or two synthetic tests, keep it out
of this phase.

## Minimal Scoring Change

Current named versions such as `qv` and `qvm` are useful for examples, but they
are too rigid for research. The minimal improvement is a generic weighted
score mode that reads group weights from config.

Example shape:

```yaml
strategy:
  scoring:
    mode: weighted_groups
    weights:
      quality: 0.4
      value: 0.4
      momentum: 0.2
```

Implementation rule:

- compute existing group scores as today: `quality_score`, `value_score`,
  `momentum_score`
- compute `composite_score` from configured group weights
- continue writing `qvm_score` during the migration only if needed for backward
  compatibility
- rank by `composite_score` for the new mode
- reject configs with unknown factor groups or all-zero weights

Public example configs should use illustrative parameters only. Real parameter
choices belong in private configs.

## Minimal Filtering Change

The current hardcoded momentum exclusion is too blunt because it can exclude
everything below the cross-sectional average. The generic replacement is a
simple factor-tail filter.

Example shape:

```yaml
strategy:
  filters:
    - group: momentum
      rule: exclude_bottom_pct
      pct: 20
```

Implementation rule:

- filters run after group score calculation and before ranking
- `exclude_bottom_pct` removes only the weakest tail within the current
  cross-section
- filtered rows remain in the score output for auditability
- filtered rows do not receive a tradable rank
- write filter state explicitly:

```text
filter_status
filter_reasons
```

Suggested statuses:

```text
pass
filtered
missing_required_score
```

## Output Contract

The score output should expose enough information to audit why a name ranked or
failed:

```text
rebalance_date
rank
code
name
sector
latest_unadjusted_close
quality_score
value_score
momentum_score
composite_score
filter_status
filter_reasons
missing_score_components
<raw_factor>_z
```

This keeps the public output generic while allowing private runs to explain
candidate differences without reading code.

## Backward Compatibility

Keep the old strategy-version names temporarily:

```text
value_only
qv
qvm
value_dominant_quality_filter_momentum_exclusion
```

But prefer the new config-driven path for future work. Once private and public
examples no longer depend on old names, they can be deprecated in a separate
cleanup.

## Required Tests

Add synthetic tests for:

- weighted group scoring respects config weights
- unknown score groups fail clearly
- all-zero weights fail clearly
- bottom-percentile filter only excludes the configured tail
- filtered rows keep audit columns but receive no rank
- missing required group scores are reported separately from tail filters
- changing score or filter config changes the walk-forward cache namespace
- synthetic walk-forward still runs with old strategy versions
- synthetic walk-forward runs with the new config-driven scoring path

## Performance Problem

Grid runs can be slow because repeated runs often do too much work:

- large CSV inputs are read repeatedly
- universe and factor stages are recomputed for each strategy variant
- subprocess startup happens for each rebalance stage
- strategy, target holdings, ADV cap, date range, and execution settings can be
  mixed into cache keys too early, reducing cache reuse
- human-readable CSV outputs are useful but slower as intermediate storage

For a research grid, universe and raw factors usually depend on data and
universe rules, not on portfolio parameters. They should be reused across many
strategy variants.

## Performance Plan

### Step 1: Use Parquet Cache By Default For Real Runs

Keep CSV as the public import/export contract, but use Parquet for repeated
intermediate reads:

```text
raw contract CSV or Parquet
-> processed input Parquet cache
-> universe Parquet cache
-> factor Parquet cache
-> score Parquet cache
-> CSV summaries/reports for review
```

### Step 2: Split Cache Layers

Separate cache keys by dependency:

```text
input cache:
  input file checksums

universe cache:
  input cache + universe config + rebalance date

factor cache:
  universe cache + factor config + rebalance date

score cache:
  factor cache + scoring/filter config + strategy version

portfolio/walk-forward output:
  score cache + holdings + ADV cap + execution + tax/cost + date range
```

This prevents a target-holdings or ADV change from forcing universe/factor
rebuilds.

### Step 3: Add A Grid Runner

Add a small public-safe grid runner that executes multiple parameter
combinations in one Python process:

- load listings, prices, and fundamentals once
- build price indexes once
- reuse universe/factor caches per rebalance date
- rerun only scoring and portfolio steps when score config changes
- rerun only portfolio steps when holdings or ADV changes

This should stay a script, not a scheduler.

### Step 4: Reduce Subprocess Use In Walk-Forward

The current script-by-script path is good for auditability. For grid runs, call
Python functions directly inside the runner instead of launching subprocesses
for every rebalance stage. Keep script entry points for manual debugging.

### Step 5: Read Only Needed Columns Where Practical

When scanning Parquet via pandas or DuckDB, read only columns needed for the
stage. This matters most for wide score/factor outputs and large price history.

### Step 6: Keep Reports Thin During Grid Runs

For large grids, write compact CSV summaries first. Generate full Markdown
reports only for selected candidates after the grid narrows.

## Validation

Before committing public changes, run:

```powershell
python scripts\smoke_test_universe.py
Get-ChildItem scripts -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
python -m unittest discover -s tests
rg --hidden --glob '!.git/**' -n "portfolio\.json|risk-dashboard|investment_policy|appendix_a|core_layer|TUSHARE_TOKEN|BEGIN (RSA|OPENSSH|PRIVATE) KEY|sk-[A-Za-z0-9]"
```

The privacy scan returning no matches is the desired result. If it matches only
the documented scan command itself, inspect the hit before treating it as safe.

## Expected Outcome

The next implementation should be small:

```text
weighted group score
+ factor-tail filter
+ clearer score output
+ better cache reuse for grids
```

Anything beyond that should require a separate design note.
