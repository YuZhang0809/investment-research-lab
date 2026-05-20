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
accounting period end. Sources may emit multiple rows for the same
`code + available_date + available_time + document_type`, for example current
and comparison periods in one disclosure. When present, `period_end` and
`disclosure_number` are part of the uniqueness key. Factor builders select the
latest usable accounting row by disclosure timestamp, period end, and disclosure
number, ignoring rows that do not contain factor inputs when a usable row exists.

## Derived Fundamental Factor Panels

`build_derived_fundamental_factor_panel.py` creates point-in-time derived
features from disclosure history. Rebalance-mode output is keyed by:

```text
rebalance_date,code
```

Event-mode output is keyed by:

```text
available_date,code
```

Both modes may include:

```text
available_time,period_type,period_end,document_type,disclosure_number,statement_scope,prior_year_available_date,prior_year_period_end,source_duplicate_count,source_disclosure_count,sales,operating_profit,net_profit,equity,total_assets,shares_outstanding,sales_yoy,operating_profit_yoy,net_profit_yoy,operating_margin,operating_margin_delta_yoy,roe,roa,equity_to_assets,shares_outstanding_change_yoy,profit_turn_positive,missing_flags
```

The script uses `available_date`/`disclosure_date` as the PIT gate and matches
same-period prior-year rows by `code`, `period_type`, `period_end`, and
statement scope. Missing prior-year comparisons are reported through
`missing_flags`. Rebalance-mode output selects the latest reporting period as
of the rebalance date, so a later restatement for an older period does not
replace a newer period row. It rejects mixed `period_type` values for the same
rebalance date unless explicitly allowed; use `--period-type` for ordinary
cross-sectional scoring. Public committed outputs must be synthetic.

## External Factor Panels

Optional external factor panels can be joined by the legacy factor stage through
`external_factor_panels` config. Supported file formats are CSV and Parquet.
Exact joins require the configured join keys, such as:

```text
rebalance_date,code,<external_field_1>,<external_field_2>
rebalance_date,sector,<external_field_1>,<external_field_2>
```

As-of joins use a configured availability column:

```text
code,available_date,<external_field_1>,<external_field_2>
sector,available_date,<external_field_1>,<external_field_2>
```

Supported external field dtypes are `float`, `int`, `string`, and `bool`.
Duplicate contract keys fail by default. Joined fields are preserved in factor
outputs and factor-score panels and may be used by configurable scoring and
filters. Public committed panels must be synthetic.

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

## Rebalance Price/Universe Panel

`build_rebalance_price_universe_panel.py` writes a local DuckDB fast-path panel
that can be compared against the legacy `build_universe.py` and
`build_factors.py` path before it is used by any walk-forward runner.

Minimum output contract:

```text
rebalance_date,code,name,market,sector,source_date,source,listing_lifecycle_status,listed_date,delisted_date,last_trading_date,lifecycle_exit_date,security_type,lot_size,included_flag,exclusion_reason,latest_price_date,latest_unadjusted_close,adjusted_close,rebalance_price_available,latest_price_stale,price_staleness_trading_days,ipo_age_trading_days,median_60d_trading_value,has_fundamentals,tradable_flag,price_limit_flag,return_12_1,return_6_1
```

This panel is a price/universe acceleration layer only. It does not select
fundamental rows, compute quality/value ratios, score stocks, or run a
portfolio. `compare_fast_panel_to_legacy.py` should be used to verify parity on
synthetic and private local fixtures before any downstream integration.

## Rebalance Factor/Score Panel

`build_rebalance_factor_score_panel.py` writes a reusable factor/score panel
from a validated rebalance price/universe panel. The default `legacy` engine
reuses the existing `build_factors.py` and `build_scores.py` semantics. The
optional `duckdb` engine is the optimized path for supported base Q/V/M factors,
group-relative transforms, external factor panels, field filters, and
configurable weighted-factor scoring. Unsupported custom factor expressions
must use the legacy engine.

Minimum output contract:

```text
rebalance_date,code,included_flag,exclusion_reason,latest_price_date,latest_unadjusted_close,adjusted_close,median_60d_trading_value,return_12_1,return_6_1,has_fundamentals,fundamentals_available_date,fundamentals_available_time,period_end,document_type,operating_profit_to_total_assets,equity_to_assets,earnings_yield,book_to_market,quality_score,value_score,momentum_score,composite_score,qvm_score,rank_score,rank,candidate_rank,filter_status,filter_reasons,missing_flags,missing_score_components,<raw_factor>_z
```

When `strategy.group_relative_transforms` is configured, the panel also
emits dynamic fields:

```text
<output_prefix>_<field>_z
<output_prefix>_<field>_rank_pct
<external_factor_panel_field>
```

Excluded rows may be present for auditability, but only `included_flag=true`
rows are consumed by `run_qvm_walkforward.py --factor-score-panel`.
Portfolio construction and accounting outputs still need legacy parity before
the fast path is used for research.

Score-only factor-score panels may omit raw factor fields if they keep the
universe cache fields needed by portfolio construction and provide either
`rank`/`candidate_rank` or a numeric `rank_score`/`composite_score`/`qvm_score`.
When only a numeric score is supplied, `run_qvm_walkforward.py` derives ranks by
descending score and ascending code.

## Walk-Forward Execution Timing

`run_qvm_walkforward.py` separates the rebalance signal date from the execution
fill date.

`rebalance_close` is same-day accounting:

```text
rebalance_date == signal_date == execution_date == equity observation date
```

`next_open` and `next_close` form the signal on `rebalance_date` and fill
orders on the next trading date where the selected execution price exists.
Cash, holdings, tax lots, realized gains, costs, trade rows, holdings rows, and
the equity observation row are updated at the fill-date valuation point. This
prevents a strategy from receiving return between the signal close and the
next-day fill. Order deltas are computed in adjusted-share terms when the fill
date differs from the signal date, so splits between signal and fill do not
leave residual holdings or under-size target buys.

Summary rows include execution diagnostics:

```text
last_execution_date,execution_lag_days,pending_order_count,filled_order_count,unexecuted_order_count,missing_execution_price_count,missing_execution_price_row_count,execution_date_not_tradable_count,execution_price_unavailable_on_execution_date_count
```

Equity rows include both the observation date and the rebalance signal date:

```text
date,rebalance_date,last_execution_date
```

Portfolio, research-basket, filtered-universe benchmark, and market-benchmark
equity are all updated to the same observation date for the row.

Trade rows continue to expose:

```text
signal_date,execution_date
```

If a `next_open` or `next_close` order has no executable price on the intended
next trading date, the engine treats it as a no-fill. It does not forward-fill
the execution price and does not roll the order to a later date by default.
Specific failure-case types distinguish the reason:

```text
missing_execution_price_row
execution_date_not_tradable
execution_price_unavailable_on_execution_date
```

The broad `missing_execution_price_count` summary field remains as an aggregate
for backward-compatible monitoring.

When `reporting.execution_diagnostics.enabled` is true, summary rows also
include:

```text
execution_diagnostics_enabled,high_cash_threshold,high_cash_flag,average_cash_weight,max_cash_weight,periods_with_cash_weight_above_threshold,target_slots_filled_ratio,selected_but_untradeable_count,selected_but_unaffordable_count,skipped_due_to_affordable_lot_count,skipped_due_to_adv_cap_count,small_account_path_dependency_flag,small_account_path_dependency_detail,buy_turnover,sell_turnover,period_cost_drag,period_tax_drag
```

The optional execution diagnostics CSV uses:

```text
rebalance_date,valuation_date,execution_price,cash_weight,high_cash_threshold,high_cash_flag,selected_count,target_holdings,holdings_count,target_slots_filled_ratio,selected_but_untradeable_count,selected_but_unaffordable_count,skipped_due_to_affordable_lot_count,skipped_due_to_adv_cap_count,small_account_path_dependency_flag,small_account_path_dependency_detail,pending_order_count,filled_order_count,skipped_orders,buy_turnover,sell_turnover,turnover,estimated_cost_base,period_cost_drag,period_tax_drag,cash_drag,selected_lot_value_min,selected_lot_value_median,selected_lot_value_max,skipped_lot_value_min,skipped_lot_value_median,skipped_lot_value_max,average_cash_weight,max_cash_weight,periods_with_cash_weight_above_threshold,realized_holdings_count_avg,realized_holdings_count_min,realized_holdings_count_max
```

## Walk-Forward Margin Outputs

`run_qvm_walkforward.py` can run broker-neutral long-margin research accounting.
It is disabled by default. When enabled, margin changes target gross exposure
and financing-cost accounting, but it does not bypass lot-size, affordable-lot,
ADV, cost, tax, sector-cap, or buy/hold-buffer constraints.

Config fields:

```text
margin.enabled
margin.account_type
margin.target_gross_leverage
margin.max_gross_leverage
margin.annual_borrow_rate
margin.initial_margin_requirement
margin.maintenance_margin_requirement
margin.minimum_required_equity
margin.interest_day_count
margin.margin_call_action
```

Summary rows include:

```text
margin_enabled,margin_account_type,target_gross_leverage,effective_target_gross_leverage,max_gross_leverage_config,annual_borrow_rate,initial_margin_requirement,maintenance_margin_requirement,minimum_required_equity,margin_call_action,gross_exposure,borrowed_value,net_account_equity,gross_leverage,margin_ratio,financing_cost_period,financing_cost_cumulative,portfolio_equity_after_cost_after_financing,min_margin_ratio,margin_breach_count,first_margin_breach_date,minimum_equity_breach_count,max_margin_gross_leverage,avg_margin_gross_leverage,max_borrowed_value
```

Equity rows also include:

```text
portfolio_equity_after_cost_after_financing
```

When margin is enabled, the daily diagnostics CSV uses:

```text
date,rebalance_date,gross_exposure,borrowed_value,account_equity,gross_leverage,margin_ratio,maintenance_margin_breach,minimum_equity_breach
```

The margin summary CSV uses:

```text
start_date,end_date,account_type,target_gross_leverage,effective_target_gross_leverage,max_gross_leverage_config,annual_borrow_rate,initial_margin_requirement,maintenance_margin_requirement,minimum_required_equity,margin_call_action,min_margin_ratio,margin_breach_count,first_margin_breach_date,minimum_equity_breach_count,max_margin_gross_leverage,avg_margin_gross_leverage,max_borrowed_value,cumulative_financing_cost
```

Failure-case rows may include:

```text
margin_call_flag
minimum_equity_breach
financing_cost_drag
leverage_cap_reduction
```

`margin_call_action: flag_only` reports breaches only. It is not an executable
forced-liquidation model.

## Walk-Forward Sector Cap Outputs

`run_qvm_walkforward.py` can apply a generic portfolio construction sector cap
after ranking and before target construction. This does not rewrite
factor/score panels, ranks, or research basket rows.

The public engine currently supports `portfolio.sector_cap.mode: name_count`.
The cap is disabled by default. When enabled, summary rows include:

```text
sector_cap_enabled,sector_cap_mode,sector_cap_group_field,sector_cap_limit,sector_cap_blocked_candidates,sector_cap_unfilled_slots,max_sector_weight_selected,max_sector_weight_actual,sector_cap_violation_count
```

Failure-case rows may include:

```text
sector_cap_blocked_candidate
sector_cap_unfilled_target
sector_cap_actual_violation
```

When sector-cap diagnostics are produced, the exposure output uses:

```text
date,group,selected_count,target_weight,actual_weight,cap_limit,violation
```

These fields are generic portfolio-construction diagnostics. Public examples
must remain synthetic and must not encode private sector-cap parameter
conclusions.

## Walk-Forward Affordable Lot Filter

`run_qvm_walkforward.py` can apply a generic affordable-lot filter during
portfolio selection. It is disabled by default and does not mutate score ranks
or factor-score panels. When enabled, top-ranked names whose minimum lot cannot
fit the executable target allocation are excluded from `selected_codes`, and the
selector continues to lower-ranked affordable candidates.

Config fields:

```text
portfolio.affordable_lot_filter.enabled
portfolio.affordable_lot_filter.max_single_lot_weight
portfolio.affordable_lot_filter.min_single_lot_weight
portfolio.affordable_lot_filter.cash_buffer_weight
```

Summary rows include:

```text
affordable_lot_filter_enabled,max_single_lot_weight,min_single_lot_weight,cash_buffer_weight,affordability_excluded,zero_lot_avoided
```

Failure-case rows may include:

```text
affordability_excluded
zero_lot_avoided
affordability_unfilled_target
cash_drag
```

`cash_buffer_weight` reduces the targetable equity used for target sizing. It
does not create synthetic fills or change research basket membership.

## Factor Forward Return Diagnostics

`analyze_factor_forward_returns.py` writes:

```text
factor_forward_returns_<range>_<holding>d.csv
alphalens_factor_data_<range>_<holding>d.csv
factor_forward_returns_<range>_<holding>d.md
```

When `--grouped-diagnostics` is enabled it also writes:

```text
factor_forward_returns_grouped_<range>_<holding>d.csv
factor_forward_returns_grouped_<range>_<holding>d.md
```

Grouped rows use this core contract:

```text
rebalance_date,factor,group,rows,observations,coverage,pearson_ic,rank_ic,top_count,bottom_count,bucket_status,top_return,bottom_return,top_bottom_spread,quantile_count,quantile_status,top_quantile_return,bottom_quantile_return,top_bottom_quantile_spread,missing_factor,missing_forward_return,missing_factor_rate,missing_forward_return_rate
```

`group` is taken from `--group-field` and blank groups are reported as
`UNKNOWN`. Public fixtures must remain synthetic.

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
`pit_snapshot_panel`, `pit_inferred_lifecycle`, `pit_no_delistings_observed`,
and `pit_with_delistings`.

`pit_inferred_lifecycle` means lifecycle dates were derived from snapshot and
price evidence instead of an authoritative listing/delisting feed. It is useful
for research iteration, but it is not performance-conclusive.

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
