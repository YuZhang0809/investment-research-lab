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
- PIT derived fundamental factor panel tooling for public-safe event and factor research
- Price defensive and crowding factor panel tooling for synthetic/public-safe factor research
- Example configs ending in `.example.yml`
- Synthetic sample CSVs
- Empty templates for holdings, trades, events, and paper-trading logs
- Public-safe docs: architecture, data contracts, workflow, data policy
- Product direction docs: PRD, Phase 1 plan, open-source references

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
python -m unittest discover -s tests
```

These checks passed locally.

Phase 1 real-data path was also validated locally with a five-code J-Quants
sample using `JQUANTS_API_KEY` from the local environment. Raw and processed
outputs are ignored by git. See `docs/phase_1_real_data_runbook.md` for the
credential-free command sequence.

Privacy scan also checked for obvious references to private portfolio files,
dashboard files, IPS/core-layer docs, private keys, and common token patterns.

## Next Useful Tasks

1. Continue the event and defensive factor capability plan in `docs/next_capability_handoff_event_factors.md`.
2. Build event-driven entry and holding-window simulation primitives on top of public-safe event panels.
3. Add matched-control event benchmarks for same date, same group, and same size-bucket controls.
4. Add defensive price factor panel generation for realized volatility, downside volatility, drawdown, and benchmark beta.
5. Expand optional data-contract extensions for dividend, forecast, balance-sheet, and crowding fields.
6. Add lightweight privacy scanning.
7. Add CI for compile checks, focused unit tests, and smoke test.
8. Keep synthetic fixtures only for public no-key tests and schema examples.
9. Decide whether to add an open-source license.
10. Keep the private workspace as the only place where real portfolio data and vendor data live.
