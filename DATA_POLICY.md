# Data Policy

The public repository must contain code, schemas, templates, and synthetic
examples only.

## Allowed

- Source code and configuration templates
- Empty CSV templates
- Synthetic examples created for tests and documentation
- Public-safe Markdown docs that do not identify an investor, account, broker,
  portfolio, or live strategy state

## Not Allowed

- API keys, tokens, cookies, or local credential files
- Real portfolio holdings, quantities, costs, account names, or balances
- Personal IPS, household asset plans, or live allocation documents
- Raw vendor data from J-Quants, TDnet, broker exports, or market data vendors
- Processed datasets that reconstruct or redistribute vendor data
- Local reports with real capital, tax, final equity, account, or execution
  results

## Derived Outputs

Derived reports may be committed only when they are generated from synthetic
data or when they are reduced to public-safe percentages and cannot reconstruct
the underlying vendor data.

## Local Workspace

The following paths are intentionally ignored:

```text
data/raw/**
data/processed/**
data/manifest/data_manifest.csv
reports/**
.env*
jquants-api.toml
credentials.json
```

Use `data/manifest/data_manifest.example.csv` to document the manifest schema
without exposing real paths, row counts, checksums, or vendor fingerprints.
