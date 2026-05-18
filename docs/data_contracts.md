# Data Contracts

These are the minimum CSV contracts used by the v0.1 pipeline. Real vendor
fields should be converted into these contracts before research steps run.

## Listings

```text
code,name,market,sector,listed_date,delisted_date,last_trading_date,security_type,is_common_stock,is_etf_reit_infra,tradable_flag,lot_size
```

Lifecycle-enriched listings or source-dated master snapshots may also include:

```text
source_date,source,listing_lifecycle_status,delisting_reason,successor_code
```

When multiple `source_date` snapshots are present in the listings file,
`build_universe.py` uses only the latest snapshot available on or before the
rebalance date. This is cleaner than using a current master snapshot for all
historical dates, but if exact `listed_date` and `delisted_date` are still
missing the run is marked `pit_snapshot_panel`, not performance-conclusive.
`last_trading_date` is preferred over `delisted_date` for execution cutoffs
when both are available. The engine does not infer missing lifecycle dates from
future price gaps.

`download_jquants_listings_panel.py` writes a repeated snapshot panel, and
`select_research_codes.py` can derive a generic research code list from that
panel. Real code lists should stay in private workspaces because they can reveal
research scope.

## Daily Prices

```text
date,code,unadjusted_close,adjusted_close,trading_value,tradable_flag,price_limit_flag
```

`adjusted_close` should be populated when the source provides it. If it is not
available, the price file must include a positive `adjustment_factor` column so
the engine can synthesize an adjusted series for returns and corporate-action
neutral valuation. Without either adjusted prices or adjustment factors,
split/reverse-split periods are not research-safe.

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

`analyze_benchmark_attribution.py` can also compare a summary CSV against
custom benchmark files passed as `label=path`. Custom benchmark files must have
`date` plus either a return column (`return` or `benchmark_return`) or one value
column (`close`, `equity`, or `value`). Files that mix return and value columns
are rejected instead of guessed.

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

## Coverage Profiles

`profile_data_coverage.py` writes a strategy-agnostic coverage table:

```text
rebalance_date,listing_source_date,listing_rows,common_stock_codes,price_any_history_codes,price_on_or_before_codes,price_on_date_codes,fundamentals_available_codes,common_with_price_history,common_with_price_on_or_before,common_with_price_on_date,common_with_fundamentals,common_with_price_and_fundamentals,common_missing_price_on_or_before,common_missing_fundamentals
```

`profile_research_universe.py` writes configured universe diagnostics without
running strategy scoring:

```text
rebalance_date,listing_source_date,included_count,excluded_count,evaluated_count,stale_price_included,missing_rebalance_price_included,with_fundamentals_included,median_60d_trading_value_p10,median_60d_trading_value_median,median_60d_trading_value_p90,top_exclusion_reasons
```

and a reason-count table:

```text
rebalance_date,reason,count
```

## Run Ledger

Public-safe template:

```text
experiments/run_ledger.example.csv
```

Minimum CSV contract:

```text
run_id,run_at,experiment_id,phase,hypothesis,predefined_metrics,go_no_go_criteria,config_hash,data_hash,code_version,engine_hash,universe_label,period_start,period_end,rebalance_count,strategy_label,rebalance_frequency,cost_scenario,execution_price,lifecycle_data_status,performance_conclusion_allowed,missing_price_tail_policy,missing_price_tail_max_stale_days,key_metric_after_cost,key_metric_after_tax,key_metric_benchmark,market_benchmark_id,market_beta,market_alpha,tracking_error,information_ratio,max_drawdown,avg_cash_pct,avg_turnover,notes_path,decision,decision_reason
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

`append_run_record.py` requires `--hypothesis`, at least one
`--predefined-metric`, and at least one `--go-no-go-criterion`. This keeps
exploratory, validation, and paper-test runs from being registered as
post-hoc metric hunts.

`lifecycle_data_status` is a caveat field, not an approval flag. Current
walk-forward values include `snapshot_only`, `partial_lifecycle`,
`pit_snapshot_panel`, `pit_no_delistings_observed`, and `pit_with_delistings`.

## Data Quality Audit

`audit_data_quality.py` writes issue-level and summary-level outputs:

```text
issue_type,severity,date,code,detail,value,threshold
issue_type,severity,count
```

The audit flags missing adjusted prices, missing adjustment factors, invalid
prices, large adjusted-price jumps, price calendar gaps, stale adjusted-price
runs, not-tradable rows, price-limit rows, prices after delisting, and optional
single-name abnormal contribution rows. It does not repair or synthesize data.

Audit severity values are research gate categories:

```text
blocking_error
execution_constraint
review_required
info
```

`blocking_error` means the data can directly pollute returns or lifecycle
validity, such as missing adjusted-price basis on a tradable row or prices
after delisting. `execution_constraint` means the row is a known trading
constraint, such as not-tradable or price-limit rows. `review_required` means
the run can be inspected but needs sampling before validation, such as large
price jumps, long stale-price runs, price calendar gaps, or abnormal
single-name contribution. `info` records context such as adjustment-factor
changes.

Blank `adjusted_close` is valid only when a positive `adjustment_factor` is
present for that row. In that case the audit treats the source shape as valid
because the engine has an explicit adjusted-price basis.

## Strategy Diagnostics Pack

`generate_strategy_diagnostics_pack.py` consumes public-engine artifacts and
writes one static Markdown pack plus a metrics CSV. Required input is a
walk-forward summary. Optional inputs are explicit files for failures, trades,
candidates, contributions, exposures, data-quality summary, and benchmark
attribution. Optional sections are shown only when their source file is passed.
When a data-quality summary is supplied, the pack renders a top-level data gate
with `research_safe_for_exploration`, `research_safe_for_validation`,
`performance_conclusion_allowed`, and `performance_blocked_reason`. Formal
performance conclusions require no blocking data-quality issues, no pending
review-required audit items, and `lifecycle_data_status=pit_with_delistings`.

## Event Drift

`analyze_event_drift.py` expects the event log columns:

```text
event_id,announcement_datetime,code,event_label
```

The richer TDnet event contract may also include company name, document type,
title, URL/document ID, parse flag, confidence, and notes. The drift output adds
`entry_date`, `tradable_timestamp`, event overlap/duplicate counts, and
`next_<window>d_return/status` columns for configured trading-day windows.
