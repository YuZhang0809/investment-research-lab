# Investment Research Lab

Public-safe research infrastructure for Japan equity experiments.

This repository contains a local, file-based research workbench for QVM-style
experiments, walk-forward simulations, reporting, and paper-trading logs. It is
designed to make quant research runnable first and auditable second, while
intentionally excluding private portfolio data and vendor data snapshots.

The project is for learning and running personal Japan equity research. It is
not a general quant platform, live trading system, or broker integration.

## Scope

- Japan equity research experiments
- J-Quants API download adapters
- Parquet processed/cache data with CSV import/export contracts
- Local DuckDB queries over Parquet files
- Point-in-time universe construction
- Generic strategy expression primitives for factors, scores, filters, target,
  order, and walk-forward pipelines
- TDnet event-observation scaffolding
- Synthetic examples and empty templates

## Current Product Direction

The first product milestone is a complete QVM research loop:

```text
clean contract data
  -> Parquet processed/cache tables
  -> point-in-time universe
  -> QVM factors and ranks
  -> research and executable targets
  -> walk-forward summary
  -> candidate and failure-case report
```

Quant research comes first. Validation, failure cases, and privacy checks are
built into the workflow to keep results trustworthy, not to turn the project
into a compliance or platform product.

## Out Of Scope

- Personal IPS or household asset planning
- Real portfolio holdings
- Real private strategy decisions, parameter choices, candidate lists, or
  go/no-go conclusions
- Live trading or auto-ordering
- Raw or processed vendor datasets
- API keys, tokens, local `.env` files, or private reports

## Repository Layout

```text
configs/              Example strategy and event configs
data/                 Local data workspace; raw and processed files are ignored
docs/                 Architecture, data policy, and workflow notes
examples/             Synthetic schema examples
experiments/          QVM and TDnet experiment specifications
reports/examples/     Public-safe example report artifacts
scripts/              Local research pipeline scripts
tests/                Lightweight validation space
```

Key planning docs:

- `docs/prd.md`
- `docs/phase_1_plan.md`
- `docs/phase_1_real_data_runbook.md`
- `docs/open_source_references.md`
- `docs/architecture.md`
- `docs/data_contracts.md`
- `docs/experiment_workflow.md`
- `docs/strategy_boundary.md`

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

J-Quants access is optional for local code review and synthetic smoke tests. If
you use the API, set the key outside git:

```powershell
$env:JQUANTS_API_KEY="..."
python scripts\validate_jquants.py --preflight-only
```

## Smoke Test

```powershell
python scripts\smoke_test_universe.py
```

The smoke test generates synthetic data in a temporary directory and verifies
the point-in-time universe logic without using real vendor data.

Walk-forward runs can opt into the Parquet cache and parameter overrides while
keeping human review outputs as CSV:

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <public-safe-listings.csv> `
  --prices <public-safe-prices.csv> `
  --fundamentals <public-safe-fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --cache-format parquet `
  --rebalance quarterly `
  --strategy-version qvm `
  --target-holdings 15 `
  --adv-cap 0.005 `
  --no-manifest
```

## Data Policy

This repository does not include raw J-Quants, TDnet, broker, or personal
portfolio exports. See `DATA_POLICY.md` before adding any data files.

## License

No open-source license has been selected yet. Treat this repository as source
available for review until a license is explicitly added.
