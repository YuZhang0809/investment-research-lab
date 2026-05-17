# Scripts

All scripts are local command-line tools. They do not place orders.

## Core Flow

```powershell
python scripts\build_universe.py `
  --config configs\qvm_v0_1.example.yml `
  --rebalance-date 2026-05-15 `
  --listings examples\synthetic_listings.csv `
  --prices examples\synthetic_prices.csv `
  --fundamentals examples\synthetic_fundamentals.csv `
  --no-manifest
```

The bundled synthetic examples are schema examples. For a full pipeline run,
provide enough trading history for the configured lookback windows.

## Table IO

Shared IO goes through `research_common.read_table()` and
`research_common.write_table()`. They support:

```text
.csv
.parquet
directory-style Parquet datasets
```

Script-level `read_csv()` and `write_csv()` remain compatibility wrappers. New
large intermediates should use Parquet cache files; human review outputs should
stay CSV.

## Walk-Forward Cache

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --cache-format parquet `
  --rebalance quarterly `
  --target-holdings 15 `
  --adv-cap 0.005 `
  --strategy-version qvm `
  --no-manifest
```

`--cache-format parquet` enables `data/processed/cache` by default unless
`--cache-dir` is provided. Cache files live under layer-specific fingerprinted
namespaces: inputs, universe, factors, scores, and run-dependent candidate
tables. Portfolio parameter changes such as target holdings, ADV cap, capital,
cost, or execution timing reuse upstream universe/factor/score cache files when
their dependencies are unchanged. Config or input changes still create new
fingerprinted namespaces instead of silently reusing stale tables.
`--force-rebuild` refreshes files inside the current namespaces. Summary, trades,
holdings, equity, and failure-case outputs remain CSV.

`--strategy-version weighted_groups` uses `strategy.scoring.weights` and
`strategy.filters` from the config. It writes `composite_score`,
`filter_status`, and `filter_reasons` while keeping `qvm_score` populated for
the older target/order scripts during the migration.
`--strategy-version configurable` supports the same group-weighted mode plus a
generic `weighted_factors` mode that combines configured factor z-scores
directly. Factor definitions under `factors.definitions` are evaluated by the
whitelist expression engine documented in `docs/factor_and_strategy_expressions.md`.

The research universe can retain names whose latest price is stale at the
rebalance date. Those rows carry `rebalance_price_available=false` and
`latest_price_stale=true`. Executable target generation treats those names as
non-orderable unless `strict_rebalance_price_filter` removes them upstream.
Walk-forward tail gaps are explicit through `missing_price_tail_policy`: the
public example defaults to `warn_only`, while conservative runs can use
`assume_zero_after_n_trading_days` with `max_stale_trading_days`.

## Run Ledger And Decision Notes

Run ledgers are CSV files intended for private workspaces. The public repo only
contains the generic schema and scripts.

```powershell
python scripts\append_run_record.py `
  --summary <qvm_walkforward_summary.csv> `
  --config configs\qvm_v0_1.example.yml `
  --ledger <private-run-ledger.csv> `
  --run-id <stable-run-id> `
  --decision REVIEW

python scripts\generate_decision_note.py `
  --ledger <private-run-ledger.csv> `
  --run-id <stable-run-id> `
  --out <private-decision-note.md>
```

Allowed decisions are `EXPLORATORY`, `REVIEW`, `REJECT`, and `PAPER_TEST`.
These tools create research notes only. They do not implement approvals,
permissions, immutable logs, compliance reports, dashboards, or schedulers.

## Walk-Forward Tear Sheet

Generate a static public-safe performance report from an existing walk-forward
run:

```powershell
python scripts\generate_walkforward_tearsheet.py `
  --summary <qvm_walkforward_summary.csv> `
  --failures <qvm_walkforward_failure_cases.csv> `
  --out reports\walkforward\walkforward_tearsheet.md `
  --no-manifest
```

The tear sheet writes a metrics CSV and SVG charts next to the Markdown report.
Metrics are sampled at the run's rebalance frequency, so monthly and quarterly
risk statistics should not be compared as if they were daily metrics.

## J-Quants

```powershell
$env:JQUANTS_API_KEY="..."
python scripts\validate_jquants.py --preflight-only
```

J-Quants downloads use the official `jquants-api-client` package and normalize
responses into this repository's CSV contracts.

Raw downloaded files are written under `data/raw/` and must remain untracked.

## Phase 1 Real Data Path

For the current real-data workflow, see:

```text
docs/phase_1_real_data_runbook.md
```

New lightweight helpers:

```powershell
python scripts\validate_contracts.py --help
python scripts\generate_candidate_review.py --help
python scripts\analyze_factor_forward_returns.py --help
python scripts\compare_walkforward_runs.py --help
```

## Smoke Test

```powershell
python scripts\smoke_test_universe.py
```
