# J-Quants Setup

The J-Quants adapters read credentials from the environment.

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
