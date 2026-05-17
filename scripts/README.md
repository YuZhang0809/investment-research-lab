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
