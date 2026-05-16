# Handoff

## Current State

This repository is the public-safe split of a private investment workspace.
It contains the experiment/research engine only.

Local path:

```text
D:\Projects\investment-research-lab
```

Branch:

```text
main
```

Initial commit:

```text
35d1621 Initial public research lab skeleton
```

## What This Repository Contains

- QVM research pipeline scripts
- J-Quants adapter scripts
- TDnet event-observation scaffolding
- Example configs ending in `.example.yml`
- Synthetic sample CSVs
- Empty templates for holdings, trades, events, and paper-trading logs
- Public-safe docs: architecture, data contracts, workflow, data policy

## What Was Intentionally Excluded

- Personal IPS and household asset-planning docs
- Core portfolio docs and live allocation records
- GTAA pilot/product docs tied to personal implementation
- Dashboard files with real or near-real portfolio data
- Raw or processed vendor data
- Real manifests with paths, checksums, row counts, or data fingerprints
- API keys, tokens, `.env` files, local credentials, logs, and private reports

## Important Boundaries

This project is a research engine, not a trading system.

Do not add:

- live holdings
- broker exports
- personal tax lots
- real portfolio values
- private reports
- raw J-Quants or other vendor datasets

Use synthetic examples or local ignored data only.

## Validation Already Run

```powershell
python scripts\smoke_test_universe.py
Get-ChildItem scripts -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
```

Both passed before publishing.

Privacy scan also checked for obvious references to private portfolio files,
dashboard files, IPS/core-layer docs, private keys, and common token patterns.

## Next Useful Tasks

1. Add GitHub Actions for smoke test and Python compile checks.
2. Decide whether to add an open-source license.
3. Add a fuller synthetic end-to-end QVM example with enough trading history.
4. Improve CLI ergonomics so all scripts accept a consistent `--config` and `--data-root`.
5. Add unit tests for data contracts, universe construction, factor generation, and order constraints.
6. Keep the private workspace as the only place where real portfolio data and vendor data live.
