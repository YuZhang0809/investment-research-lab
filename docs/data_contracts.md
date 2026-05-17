# Data Contracts

These are the minimum CSV contracts used by the v0.1 pipeline. Real vendor
fields should be converted into these contracts before research steps run.

## Listings

```text
code,name,market,sector,listed_date,delisted_date,security_type,is_common_stock,is_etf_reit_infra,tradable_flag,lot_size
```

J-Quants master snapshots may also include:

```text
source_date,listing_lifecycle_status
```

When multiple `source_date` snapshots are present in the listings file,
`build_universe.py` uses only the latest snapshot available on or before the
rebalance date. This is cleaner than using a current master snapshot for all
historical dates, but if exact `listed_date` and `delisted_date` are still
missing the run is marked `pit_snapshot_panel`, not performance-conclusive.

## Daily Prices

```text
date,code,unadjusted_close,adjusted_close,trading_value,tradable_flag,price_limit_flag
```

## Market Benchmark Prices

Optional input for `run_qvm_walkforward.py --market-benchmark-prices`.

Minimum CSV contract:

```text
date,benchmark_id,close
```

The ID column is optional when the file contains only one series. Accepted ID
columns are `benchmark_id`, `index_code`, `code`, `id`, or `ticker`. Accepted
value columns are `adjusted_close`, `close`, `index_value`, `value`, `price`, or
`unadjusted_close`, in that priority order.

When a market benchmark is supplied, walk-forward summaries include
`market_benchmark_id`, `market_benchmark_equity`, and
`market_benchmark_return`. The run ledger derives market beta, annualized simple
alpha, annualized tracking error, and information ratio from period returns.

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
run_id,run_at,experiment_id,phase,config_hash,data_hash,code_version,engine_hash,universe_label,period_start,period_end,rebalance_count,strategy_label,rebalance_frequency,cost_scenario,execution_price,lifecycle_data_status,performance_conclusion_allowed,missing_price_tail_policy,missing_price_tail_max_stale_days,key_metric_after_cost,key_metric_after_tax,key_metric_benchmark,market_benchmark_id,market_beta,market_alpha,tracking_error,information_ratio,max_drawdown,avg_cash_pct,avg_turnover,notes_path,decision,decision_reason
```

Allowed `decision` values:

```text
EXPLORATORY
REVIEW
REJECT
PAPER_TEST
```

`code_version` and `engine_hash` distinguish runs with identical config and data
but different public-engine code. The optional market fields are populated when
the summary has a market benchmark series; otherwise they remain empty. Real run
ledgers belong in private workspaces because they can contain real research
results and decisions.

`lifecycle_data_status` is a caveat field, not an approval flag. Current
walk-forward values include `snapshot_only`, `partial_lifecycle`,
`pit_snapshot_panel`, `pit_no_delistings_observed`, and `pit_with_delistings`.
