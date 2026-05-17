# Agent Instructions

This is the public research engine. Keep it public-safe.

## Do

- Edit generic research code, configs, docs, synthetic examples, and tests here.
- Keep examples synthetic.
- Keep configs as `.example.yml` unless they are intentionally generic.
- Implement strategy expression features only as generic engine mechanics, such
  as configurable score weights or factor-tail filters.
- Run validation before pushing:

```powershell
python scripts\smoke_test_universe.py
Get-ChildItem scripts -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
```

Also run a privacy scan for private portfolio, dashboard, IPS, core-layer,
credential, private-key, and token patterns. Treat any hit as a blocker unless
it is clearly a synthetic example or placeholder.

## Do Not

- Add private portfolio data.
- Add IPS, core-layer, or GTAA personal planning documents.
- Add dashboard files that contain real or near-real holdings.
- Add `.env`, API keys, broker exports, raw vendor data, processed vendor data,
  private manifests, or private reports.
- Add real JPY capital, tax, holdings, or final-equity reports from a private
  workspace.
- Add private strategy decisions, real winning parameter choices, selected
  tickers, candidate lists, or go/no-go conclusions.

## Private Workspace Integration

Private workspaces should call this repo as a tool. This repo must not reference
private paths or depend on private files.

If private work needs a new strategy capability, implement the generic
primitive here with synthetic tests, then let the private workspace pass its
real config at runtime. See `docs/strategy_boundary.md`.
