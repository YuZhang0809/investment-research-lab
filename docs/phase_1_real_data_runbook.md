# Phase 1 Real Data Runbook

This runbook documents the small real-data path used for Phase 1 validation.
J-Quants access goes through the official `jquants-api-client` package.
It intentionally omits credentials. Set `JQUANTS_API_KEY` in the local shell or
agent environment before running live API commands.

Do not commit raw downloads, processed outputs, reports, manifests, `.env`
files, or API keys.

## 1. Validate API Access

```powershell
$env:JQUANTS_API_KEY="<set locally>"
python scripts\validate_jquants.py --date 2026-05-15 --code 86970
```

Expected result:

- equities master rows are returned
- daily bar rows are returned
- financial summary rows are returned
- no samples are written unless `--write-samples` is passed

## 2. Download a Small Research Sample

The current Phase 1 sample uses five liquid Japan equity codes:

```text
86970, 72030, 67580, 99840, 83060
```

```powershell
python scripts\download_jquants.py `
  --date 2026-05-15 `
  --prices-from 2024-01-01 `
  --prices-to 2026-05-15 `
  --price-code 86970 `
  --price-code 72030 `
  --price-code 67580 `
  --price-code 99840 `
  --price-code 83060 `
  --code 86970 `
  --code 72030 `
  --code 67580 `
  --code 99840 `
  --code 83060 `
  --out-dir data\raw\jquants\contracts\phase1_sample `
  --continue-on-error `
  --sleep-seconds 0.1
```

Expected local outputs:

```text
data/raw/jquants/contracts/phase1_sample/listings_20260515.csv
data/raw/jquants/contracts/phase1_sample/prices_86970_72030_67580_99840_83060_20240101_20260515.csv
data/raw/jquants/contracts/phase1_sample/fundamentals_86970_72030_67580_99840_83060.csv
```

These files are ignored by git.

## 3. Validate Contracts

```powershell
python scripts\validate_contracts.py `
  --config configs\qvm_v0_1.example.yml `
  --rebalance-date 2026-05-15 `
  --listings data\raw\jquants\contracts\phase1_sample\listings_20260515.csv `
  --prices data\raw\jquants\contracts\phase1_sample\prices_86970_72030_67580_99840_83060_20240101_20260515.csv `
  --fundamentals data\raw\jquants\contracts\phase1_sample\fundamentals_86970_72030_67580_99840_83060.csv `
  --label phase1_real_sample
```

Expected local outputs:

```text
data/processed/validation/contract_validation_issues_phase1_real_sample.csv
data/processed/validation/contract_validation_summary_phase1_real_sample.json
```

Important: the J-Quants v2 `/equities/master` snapshot used by
`download_jquants.py` does not provide full listed/delisted lifecycle dates.
Validation should therefore flag `listing_lifecycle_coverage` for this sample.
Treat that as a blocker for performance conclusions. The sample is still useful
for API, CSV-contract, and pipeline smoke testing.

## 4. Run a Single Rebalance Snapshot

```powershell
python scripts\build_universe.py `
  --config configs\qvm_v0_1.example.yml `
  --rebalance-date 2026-05-15 `
  --listings data\raw\jquants\contracts\phase1_sample\listings_20260515.csv `
  --prices data\raw\jquants\contracts\phase1_sample\prices_86970_72030_67580_99840_83060_20240101_20260515.csv `
  --fundamentals data\raw\jquants\contracts\phase1_sample\fundamentals_86970_72030_67580_99840_83060.csv

python scripts\build_factors.py `
  --rebalance-date 2026-05-15 `
  --universe data\processed\universe\universe_202605.csv `
  --prices data\raw\jquants\contracts\phase1_sample\prices_86970_72030_67580_99840_83060_20240101_20260515.csv `
  --fundamentals data\raw\jquants\contracts\phase1_sample\fundamentals_86970_72030_67580_99840_83060.csv

python scripts\build_scores.py `
  --config configs\qvm_v0_1.example.yml `
  --rebalance-date 2026-05-15 `
  --factors data\processed\factors\factors_202605.csv

python scripts\build_targets.py `
  --config configs\qvm_v0_1.example.yml `
  --rebalance-date 2026-05-15 `
  --scores data\processed\scores\scores_202605.csv `
  --universe data\processed\universe\universe_202605.csv `
  --capital-jpy 5000000

python scripts\build_orders.py `
  --config configs\qvm_v0_1.example.yml `
  --rebalance-date 2026-05-15 `
  --targets data\processed\portfolio\targets_202605.csv

python scripts\run_backtest.py `
  --rebalance-date 2026-05-15 `
  --orders data\processed\execution\orders_202605.csv `
  --capital-jpy 5000000
```

## 5. Generate Candidate Review and Report

```powershell
python scripts\generate_candidate_review.py `
  --rebalance-date 2026-05-15 `
  --scores data\processed\scores\scores_202605.csv `
  --factors data\processed\factors\factors_202605.csv `
  --targets data\processed\portfolio\targets_202605.csv `
  --orders data\processed\execution\orders_202605.csv `
  --exclusions data\processed\universe\excluded_202605.csv

python scripts\generate_qvm_report.py `
  --rebalance-date 2026-05-15 `
  --factors data\processed\factors\factors_202605.csv `
  --scores data\processed\scores\scores_202605.csv `
  --targets data\processed\portfolio\targets_202605.csv `
  --orders data\processed\execution\orders_202605.csv `
  --backtest-summary data\processed\backtest\summary_202605.csv `
  --candidate-review data\processed\candidate_review\candidate_review_202605.csv `
  --out reports\monthly\qvm_research_phase1_real_sample_202605.md
```

## 6. Run a Small Walk-Forward

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings data\raw\jquants\contracts\phase1_sample\listings_20260515.csv `
  --prices data\raw\jquants\contracts\phase1_sample\prices_86970_72030_67580_99840_83060_20240101_20260515.csv `
  --fundamentals data\raw\jquants\contracts\phase1_sample\fundamentals_86970_72030_67580_99840_83060.csv `
  --start-date 2025-01-31 `
  --end-date 2026-05-15 `
  --frequency monthly `
  --execution-price rebalance_close `
  --cost-scenario base `
  --run-label phase1_real_sample `
  --capital-jpy 5000000 `
  --skip-stage-manifest `
  --allow-snapshot-listings
```

`--allow-snapshot-listings` is intentionally explicit. It marks this as an
exploratory survivor-biased sample run. For real historical research, provide
PIT listings with `listed_date` and `delisted_date` instead and omit the flag.

Expected local outputs:

```text
data/processed/walkforward/qvm_walkforward_summary_phase1_real_sample_202501_202605.csv
data/processed/walkforward/qvm_walkforward_failure_cases_phase1_real_sample_202501_202605.csv
reports/walkforward/qvm_walkforward_phase1_real_sample_202501_202605.md
```

## Notes

- This five-code sample proves the real-data path works. It is not a strategy
  conclusion.
- A snapshot-only listings file is not a valid historical universe. Use it only
  for local plumbing checks unless you have a separate PIT lifecycle source.
- Console output on some Windows shells may render Japanese company and sector
  names incorrectly. The CSV files are written as UTF-8.
- Synthetic fixtures remain useful for no-key smoke tests and public examples,
  but real J-Quants-derived local data is the main research path.
