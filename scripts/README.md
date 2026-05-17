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
`--cache-dir` is provided. Cache files live under a fingerprinted namespace
derived from the effective config, input checksums, strategy, frequency, and
parameter overrides, so config or input changes do not silently reuse stale
tables. `--force-rebuild` refreshes files inside the current namespace. Summary,
trades, holdings, equity, and failure-case outputs remain CSV.

The research universe can retain names whose latest price is stale at the
rebalance date. Those rows carry `rebalance_price_available=false` and
`latest_price_stale=true`. Executable target generation treats those names as
non-orderable unless `strict_rebalance_price_filter` removes them upstream.
Walk-forward tail gaps are explicit through `missing_price_tail_policy`: the
public example defaults to `warn_only`, while conservative runs can use
`assume_zero_after_n_trading_days` with `max_stale_trading_days`.

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
