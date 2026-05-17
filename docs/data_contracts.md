# Data Contracts

These are the minimum CSV contracts used by the v0.1 pipeline. Real vendor
fields should be converted into these contracts before research steps run.

## Listings

```text
code,name,market,sector,listed_date,delisted_date,security_type,is_common_stock,is_etf_reit_infra,tradable_flag,lot_size
```

## Daily Prices

```text
date,code,unadjusted_close,adjusted_close,trading_value,tradable_flag,price_limit_flag
```

## Fundamentals

```text
code,available_date,available_time,document_type,operating_profit,net_profit,equity,total_assets,shares_outstanding
```

`available_date` is the point-in-time gate. Use the disclosure date, not the
accounting period end.

## Manifest

```text
source,file_path,downloaded_at,vendor,schema_version,date_range,checksum,notes
```

The real manifest is local-only and ignored by git. Commit only
`data/manifest/data_manifest.example.csv`.

## Run Ledger

Public-safe template:

```text
experiments/run_ledger.example.csv
```

Minimum CSV contract:

```text
run_id,run_at,experiment_id,phase,config_hash,data_hash,universe_label,period_start,period_end,strategy_label,rebalance_frequency,cost_scenario,execution_price,key_metric_after_cost,key_metric_after_tax,key_metric_benchmark,market_benchmark_id,market_beta,market_alpha,tracking_error,information_ratio,max_drawdown,avg_cash_pct,avg_turnover,notes_path,decision,decision_reason
```

Allowed `decision` values:

```text
EXPLORATORY
REVIEW
REJECT
PAPER_TEST
```

The optional market fields are placeholders for later alpha/beta analysis and
may be empty. Real run ledgers belong in private workspaces because they can
contain real research results and decisions.
