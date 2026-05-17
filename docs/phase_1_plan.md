# Phase 1 Plan

## Theme

Make the quant research loop real before adding platform features.

Phase 1 should produce a working QVM research baseline that a personal investor
can run, inspect, and learn from. Validation is included where it prevents
obvious research mistakes, but the priority is to make the quant loop usable.

## Build Order

### 1. Real J-Quants Data Path

Make the real-data path work first. Synthetic data is useful for tests and
public examples, but it should not block the research loop.

Deliverables:

- documented J-Quants credential preflight
- a small local real-data sample workflow, with outputs ignored by git
- conversion into listings, prices, and fundamentals contracts
- a clear decision on whether to keep the current lightweight adapter, wrap the
  official J-Quants client, or support both
- manifest entries for local derived outputs

Done when:

- an agent can download or use existing local J-Quants data
- the data can be converted into the project CSV contracts
- the QVM pipeline can run on a small real local sample
- no raw vendor data, local manifests, credentials, or private reports are
  tracked by git

### 2. Minimal Contract Validation

Add lightweight validation before the research run.

Deliverables:

- required-column checks
- parseable date and number checks
- duplicate key checks for important tables
- price history coverage checks for configured lookbacks
- `available_date` presence checks for fundamentals
- code coverage checks across listings, prices, and fundamentals

Done when:

- bad inputs fail before factor generation
- validation results are written to the run directory
- checks are strict enough for local research and no-key smoke fixtures

### 3. Candidate Review Output

Add a single candidate-level file that joins the important research facts.

Suggested output:

```text
runs/<run_label>/candidate_review/candidate_review_<date>.csv
```

Fields should include:

- date
- code
- name
- rank
- qvm score
- quality score
- value score
- momentum score
- major raw factors
- selected flag
- executable flag
- target shares
- target value
- cash drag
- constraint reason
- exclusion or failure reason

Done when:

- a user can open one file and understand the selected names
- rejected or constrained names have explicit reasons

### 4. Decision-Oriented Report

Improve the Markdown report after the candidate review output exists.

The report should include:

- sample period
- rebalance frequency
- universe count
- selected count
- executable count
- zero-lot count
- skipped or reduced orders
- cash drag
- cost scenario comparison
- benchmark comparison
- top candidates
- largest failure reasons
- next paper-trading checklist

Done when:

- the report explains the run without requiring users to inspect every CSV
- performance metrics do not hide execution constraints

### 5. Lightweight Tests

Keep tests light. The goal is to catch broken research assumptions, not to build
a large QA framework.

Priority tests:

- `available_date` point-in-time gating
- listed and delisted date filtering
- IPO age and liquidity lookback
- missing factor handling
- rank ordering
- lot-size flooring
- ADV cap handling
- cash drag flagging
- walk-forward rebalance dates
- a minimal no-key smoke fixture

Local or CI checks should run:

- Python compile checks
- focused unit tests
- current smoke test
- a lightweight privacy scan once implemented

### 6. Synthetic Fixtures for Public Testing

Keep synthetic data, but lower its priority. Its job is to let the public repo
run without a J-Quants key and without redistributing vendor data.

Deliverables:

- small deterministic fixtures for validation and smoke tests
- optional longer synthetic fixture only if it is needed for stable walk-forward
  regression tests

Done when:

- tests can run without credentials
- the fixtures demonstrate schema and point-in-time behavior
- synthetic results are not mistaken for real research conclusions

## Deferred

### Single-Command Runner

A runner is useful, but it is not urgent because this project is expected to be
agent-operated. Keep scripts composable first. Add a runner later if repeated
manual use becomes painful.

## Dependency Direction

Phase 1 may add `pandas` and `numpy` if they reduce brittle list/dict logic.
They should be used as computation tools, not as a replacement for CSV
contracts.

Do not make Qlib, Backtrader, Zipline, LEAN, DuckDB, or a dashboard framework a
core dependency in Phase 1.

## Success Criteria

Phase 1 is complete when:

- an agent can run a small real J-Quants-derived QVM sample end to end
- the system produces candidates, executable targets, failure reasons, and a
  report
- the main path has lightweight validation and smoke coverage
- public/private data boundaries remain intact
