# Investment Research Lab

Public-safe research infrastructure for Japan equity experiments.

This repository contains a local, file-based research engine for QVM-style
experiments, walk-forward simulations, reporting, and paper-trading logs. It is
designed to be reproducible and auditable, but it intentionally excludes private
portfolio data and vendor data snapshots.

## Scope

- Japan equity research experiments
- J-Quants API download adapters
- CSV-first data contracts
- Point-in-time universe construction
- QVM factor, score, target, order, and walk-forward pipelines
- TDnet event-observation scaffolding
- Synthetic examples and empty templates

## Out Of Scope

- Personal IPS or household asset planning
- Real portfolio holdings
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

## Data Policy

This repository does not include raw J-Quants, TDnet, broker, or personal
portfolio exports. See `DATA_POLICY.md` before adding any data files.

## License

No open-source license has been selected yet. Treat this repository as source
available for review until a license is explicitly added.
