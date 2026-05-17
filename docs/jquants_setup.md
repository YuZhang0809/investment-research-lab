# J-Quants Setup

The J-Quants adapters use the official `jquants-api-client` package and read
credentials from the environment.

```powershell
$env:JQUANTS_API_KEY="..."
python scripts\validate_jquants.py --preflight-only
```

Do not commit API keys, local `.env` files, or raw J-Quants outputs.

## Validation

```powershell
python scripts\validate_jquants.py --preflight-only
```

## Download

```powershell
python scripts\download_jquants.py `
  --date 2026-05-15 `
  --prices-from 2025-04-01 `
  --prices-to 2026-05-15 `
  --price-codes-file configs\universe.example.csv `
  --codes-file configs\universe.example.csv `
  --continue-on-error
```

The generated files are written under `data/raw/` and ignored by git.

## Market Benchmark

TOPIX can be downloaded into the market benchmark contract:

```powershell
python scripts\download_jquants_market_benchmark.py `
  --benchmark topix `
  --from 2016-05-17 `
  --to 2026-05-15
```

Use the output with walk-forward runs:

```powershell
python scripts\run_qvm_walkforward.py `
  ... `
  --market-benchmark-prices data\raw\jquants\contracts\market_benchmark_topix_20160517_20260515.csv `
  --market-benchmark-id TOPIX
```

## Listing Snapshot Panel

For historical research, prefer a source-dated listing panel over a single
current master snapshot:

```powershell
python scripts\download_jquants_listings_panel.py `
  --from 2017-06-01 `
  --to 2026-04-30 `
  --frequency quarterly `
  --calendar data\raw\jquants\contracts\market_benchmark_topix_20160517_20260515.csv
```

The output contains one listing master snapshot per requested source date. This
is still not a full lifecycle master unless exact `listed_date` and
`delisted_date` are present, so downstream reports mark it as a
`pit_snapshot_panel`.

Create a generic code list from the panel:

```powershell
python scripts\select_research_codes.py `
  --listings data\raw\jquants\contracts\listings_panel_quarterly_20170601_20260430.csv `
  --out configs\universe_codes.local.csv `
  --min-snapshots 1
```

In public repos, commit only example or synthetic code lists. Real code lists
belong in the private workspace.

## Bulk Downloads

Bulk downloads are supported for prices, financial summaries, and listing
master files:

```powershell
python scripts\download_jquants_bulk.py `
  --endpoint /fins/summary `
  --from 2024-01 `
  --to 2026-05 `
  --codes-file configs\universe.example.csv
```

```powershell
python scripts\download_jquants_bulk.py `
  --endpoint /equities/bars/daily `
  --from 2024-01 `
  --to 2026-05 `
  --codes-file configs\universe.example.csv
```

The script uses the official bulk list/get APIs, optionally filters to requested
codes, and converts outputs to the local contracts. Omit `--codes-file` only
when intentionally downloading all codes for the endpoint and date range.

## Coverage Profiles

Before running a strategy, profile data coverage:

```powershell
python scripts\profile_data_coverage.py `
  --listings data\raw\jquants\contracts\listings_panel_quarterly_20170601_20260430.csv `
  --prices data\raw\jquants\contracts\prices_bulk_1500codes_201706_202605.csv `
  --fundamentals data\raw\jquants\contracts\fundamentals_bulk_1500codes_201706_202605.csv `
  --from 2017-06-01 `
  --to 2026-04-30 `
  --frequency quarterly
```

Then profile the configured research universe constraints:

```powershell
python scripts\profile_research_universe.py `
  --config configs\qvm_v0_1.example.yml `
  --listings data\raw\jquants\contracts\listings_panel_quarterly_20170601_20260430.csv `
  --prices data\raw\jquants\contracts\prices_bulk_1500codes_201706_202605.csv `
  --fundamentals data\raw\jquants\contracts\fundamentals_bulk_1500codes_201706_202605.csv `
  --from 2017-06-01 `
  --to 2026-04-30 `
  --frequency quarterly
```

These profiles are strategy-agnostic or strategy-light diagnostics. They should
run before alpha or portfolio conclusions.
