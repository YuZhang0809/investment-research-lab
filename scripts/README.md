# Scripts

All scripts are local command-line tools. They do not place orders.

## Core Flow

```powershell
python scripts\build_universe.py `
  --config configs\qvm_v0_1.example.yml `
  --rebalance-date 2026-05-15 `
  --listings examples\synthetic_listings.csv `
  --prices examples\synthetic_prices.csv `
  --fundamentals examples\synthetic_fundamentals.csv `
  --no-manifest
```

The bundled synthetic examples are schema examples. For a full pipeline run,
provide enough trading history for the configured lookback windows.

## Table IO

Shared IO goes through `research_common.read_table()` and
`research_common.write_table()`. They support:

```text
.csv
.parquet
directory-style Parquet datasets
```

Script-level `read_csv()` and `write_csv()` remain compatibility wrappers. New
large intermediates should use Parquet cache files; human review outputs should
stay CSV.

## Walk-Forward Cache

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --cache-format parquet `
  --rebalance quarterly `
  --target-holdings 15 `
  --adv-cap 0.005 `
  --strategy-version qvm `
  --no-manifest
```

`--cache-format parquet` enables `data/processed/cache` by default unless
`--cache-dir` is provided. Cache files live under layer-specific fingerprinted
namespaces: inputs, universe, factors, scores, and run-dependent candidate
tables. Portfolio parameter changes such as target holdings, ADV cap, capital,
cost, or execution timing reuse upstream universe/factor/score cache files when
their dependencies are unchanged. Config or input changes still create new
fingerprinted namespaces instead of silently reusing stale tables.
`--force-rebuild` refreshes files inside the current namespaces. Summary, trades,
holdings, equity, and failure-case outputs remain CSV.

## Recommended Research Workflow

For supported base Q/V/M-style strategies, the recommended research-scale path
is:

```text
Step A: build_rebalance_price_universe_panel.py
Step B: build_rebalance_factor_score_panel.py --engine duckdb
Step C: run_qvm_walkforward.py --factor-score-panel
```

Step A builds the upstream price/universe panel:

```powershell
python scripts\build_rebalance_price_universe_panel.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --frequency monthly `
  --out <rebalance_price_universe_panel.parquet> `
  --output-format parquet
```

Step B builds the factor/score panel with the optimized DuckDB engine:

```powershell
python scripts\build_rebalance_factor_score_panel.py `
  --config configs\qvm_v0_1.example.yml `
  --price-universe-panel <rebalance_price_universe_panel.parquet> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --frequency monthly `
  --strategy-version qvm `
  --engine duckdb `
  --out <rebalance_factor_score_panel.parquet> `
  --output-format parquet
```

`--engine duckdb` is the recommended research path for supported factor/score
panels. `--engine legacy` is the reference, validation, and fallback
implementation; the CLI default remains legacy for backward compatibility.
DuckDB supports base Q/V/M factors, documented group-relative transforms,
external factor panels, field filters, and configurable weighted factors when
the inputs are supported panel fields. It still rejects custom factor
expressions instead of falling back silently.

Step C consumes the factor/score panel directly:

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --rebalance monthly `
  --factor-score-panel <rebalance_factor_score_panel.parquet> `
  --cache-format parquet `
  --no-manifest
```

This skips per-rebalance universe/factor/score stage builds, but keeps the
existing portfolio, execution, benchmark, holdings, equity, and failure-case
logic.

### Execution Timing

`run_qvm_walkforward.py` supports:

```text
--execution-price rebalance_close | next_open | next_close
```

`rebalance_close` is same-day accounting: the rebalance signal, fill, cash
change, holdings update, tax lot update, and equity observation all use the
rebalance date.

`next_open` and `next_close` keep the rebalance date as the signal date but
fill orders on the next trading date at the configured execution price. Trade
rows carry both `signal_date` and `execution_date`; summary/equity rows expose
`last_execution_date` and execution diagnostics. New holdings are not marked as
if they existed before the fill date. Order deltas use adjusted-share retargeting
when signal and fill dates differ, so splits between signal and fill do not leave
residual positions. If a code has no executable price on the intended next
trading date, the order is skipped; the engine does not forward-fill or roll the
order by default. Failure cases distinguish `missing_execution_price_row`,
`execution_date_not_tradable`, and
`execution_price_unavailable_on_execution_date`, while
`missing_execution_price_count` remains a broad summary aggregate.

### Sector Cap

Sector caps are generic portfolio construction controls. They run after
scores/ranks are loaded and before targets/orders are built, so they do not
mutate factor-score panels or ranks.

Config example:

```yaml
portfolio:
  sector_cap:
    enabled: true
    group_field: sector
    mode: name_count
    max_names_per_group: 9
```

CLI override example:

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --factor-score-panel <rebalance_factor_score_panel.parquet> `
  --sector-cap-mode name_count `
  --sector-cap-group-field sector `
  --max-names-per-sector 9 `
  --no-manifest
```

`name_count` caps executable `selected_codes` only. It preserves the existing
hold buffer where the cap allows it, blocks new names in full groups, allows a
below-target portfolio when the cap is too strict, and writes sector-cap fields
to summary/failure-case outputs. `target_weight` is reserved for a later
implementation and currently fails clearly instead of applying partial logic.

### Affordable Lot Filter

The affordable-lot filter is a generic portfolio construction control. It runs
after ranks are loaded and before target construction, so expensive high-ranked
names do not consume executable selected slots when one lot cannot fit the
target allocation.

Config example:

```yaml
portfolio:
  affordable_lot_filter:
    enabled: true
    max_single_lot_weight: 0.05
    min_single_lot_weight: null
    cash_buffer_weight: 0.02
```

`max_single_lot_weight` is required when enabled. `min_single_lot_weight` is
optional. `cash_buffer_weight` reserves part of equity before equal-weight target
sizing. Excluded names are reported through `affordability_excluded` and
`zero_lot_avoided`; normal cash diagnostics still report `cash_drag` when cash
is high after executable targets and fills.

### Group-Relative Scoring

Group-relative transforms are generic factor/score mechanics. They run before
ranking and can express within-group comparisons such as "high value relative to
the same sector". They are not a substitute for `portfolio.sector_cap`, which is
a later portfolio construction constraint.

Config example:

```yaml
strategy:
  group_relative_transforms:
    - group_field: sector
      fields:
        - book_to_market
      methods:
        - zscore
        - rank_pct
      min_group_size: 5
      output_prefix: sector_relative
  scoring:
    mode: weighted_factors
    weights:
      sector_relative_book_to_market_z: 1.0
```

This primitive is supported by the legacy reference path and by the DuckDB
factor-score builder for supported panel fields:

```powershell
python scripts\build_rebalance_factor_score_panel.py `
  --config configs\group_relative_transform.example.yml `
  --price-universe-panel <rebalance_price_universe_panel.parquet> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --frequency monthly `
  --strategy-version configurable `
  --engine duckdb `
  --out <rebalance_factor_score_panel.parquet> `
  --output-format parquet
```

The resulting panel keeps fields such as
`sector_relative_book_to_market_z` and
`sector_relative_book_to_market_rank_pct`, and
`run_qvm_walkforward.py --factor-score-panel` can consume them through the
normal score rows.

### External Factor Panels

External panels join generic PIT fields into factor rows before scoring. They
support exact joins such as `rebalance_date + code`, grouped exact joins such
as `rebalance_date + sector`, and as-of joins using `available_date <=
rebalance_date`.

Validate a panel contract first:

```powershell
python scripts\validate_external_factor_panel.py `
  --panel <synthetic_external_panel.parquet> `
  --join-key rebalance_date `
  --join-key code `
  --field risk_score:float `
  --field risk_flag:string
```

Then run the factor/score path with a config that contains
`external_factor_panels`. Joined fields can be used by `weighted_factors` and
field filters such as `exclude_equals`, `require_in`, `exclude_above_pct`, and
`exclude_below_pct`. Public examples must use synthetic external panels only.

### Derived Fundamental Factor Panels

`build_derived_fundamental_factor_panel.py` turns disclosure history into
point-in-time derived factors for event workflows or rebalance workflows.

Event/as-of panel example:

```powershell
python scripts\build_derived_fundamental_factor_panel.py `
  --fundamentals <synthetic_fundamentals.csv> `
  --panel-mode event `
  --out <derived_fundamentals_event.parquet> `
  --output-format parquet `
  --no-manifest
```

Rebalance panel example:

```powershell
python scripts\build_derived_fundamental_factor_panel.py `
  --fundamentals <synthetic_fundamentals.csv> `
  --panel-mode rebalance `
  --rebalance-date 2026-03-31 `
  --period-type fy `
  --document-type-contains FinancialStatements `
  --require-useful `
  --out <derived_fundamentals_202603.csv> `
  --output-format csv `
  --no-manifest
```

The output includes generic fields such as `sales_yoy`,
`operating_profit_yoy`, `operating_margin_delta_yoy`, `roe`, `roa`, and
`profit_turn_positive`. The script uses `available_date` as the PIT gate and
does not use future restatements before their availability date. Rebalance mode
selects the latest reporting period as of the rebalance date and rejects mixed
`period_type` values by default, because annual and quarterly ROE/ROA should
not be ranked together without an explicit research decision. J-Quants-style
period types such as `FY` and `1Q` normalize to `fy` and `1q`.

### Price Defensive Factor Panels

`build_price_defensive_factor_panel.py` builds low-volatility and drawdown
factors from adjusted daily prices:

```powershell
python scripts\build_price_defensive_factor_panel.py `
  --prices <synthetic_prices.csv> `
  --market-benchmark-prices <synthetic_topix.csv> `
  --rebalance-date 2026-03-31 `
  --stale-filter-days 1 `
  --flag-price-limit `
  --out <price_defensive_202603.parquet> `
  --output-format parquet `
  --no-manifest
```

The output is keyed by `rebalance_date + code` and can be joined through
`external_factor_panels`. It includes 3M/6M/12M realized volatility, 6M/12M
downside volatility, 6M/12M max drawdown, benchmark beta, stale-price flags,
price-limit flags, and insufficient-history `missing_flags`.

### Price-Volume Proxy Factor Panels

`build_price_volume_factor_panel.py` builds WQ-style price-volume proxy fields
from daily OHLCV data. It does not replicate the full WorldQuant 101 formulas
and does not create strategy conclusions:

```powershell
python scripts\build_price_volume_factor_panel.py `
  --prices <synthetic_daily_ohlcv.csv> `
  --rebalance-date 2026-03-31 `
  --out <price_volume_factors_202603.parquet> `
  --output-format parquet `
  --no-manifest
```

The output is keyed by `rebalance_date + code` and can be joined through
`external_factor_panels`. It includes base OHLCV features such as
`effective_close`, `vwap_proxy`, `candle_pressure`, `range_position`, `adv20`, and 17
`wq_alpha_*_proxy` fields intended for filters, diagnostics, weak score inputs,
or execution-timing experiments. `effective_close` is selected row-by-row from
adjusted close aliases first, then close/unadjusted close aliases; fallback rows
are flagged for data-quality review.

Profile scale and memory behavior before larger runs:

```powershell
python scripts\profile_price_volume_factor_panel.py `
  --synthetic-codes 400 `
  --synthetic-days 756 `
  --synthetic-rebalances 36 `
  --summary-out reports\engineering\price_volume_factor_panel_profile.csv `
  --report reports\engineering\price_volume_factor_panel_profile.md `
  --no-manifest
```

For full-market runs, use explicit rebalance dates plus a universe panel. That
lets the builder trim price history to requested codes and the required lookback
window before rolling feature calculation. For long histories, validate year or
rebalance-block shards before attempting a one-shot run.

Run Alphalens-style diagnostics directly from a generated panel:

```powershell
python scripts\analyze_factor_forward_returns.py `
  --factor-file <price_volume_factors.parquet> `
  --prices <synthetic_daily_ohlcv.csv> `
  --start-date 2026-02-15 `
  --end-date 2026-02-28 `
  --holding-days 5 `
  --factor wq_alpha_005_proxy `
  --factor wq_alpha_011_proxy `
  --factor wq_alpha_101_proxy `
  --grouped-diagnostics `
  --group-field sector `
  --no-manifest
```

### Optional Factor Contracts And Crowding Panels

Validate optional dividend, balance-sheet, or crowding inputs before joining or
transforming them:

```powershell
python scripts\validate_optional_factor_contract.py `
  --panel <synthetic_crowding_panel.csv> `
  --contract crowding `
  --require-numeric long_margin_balance
```

Build a generic crowding factor panel:

```powershell
python scripts\build_crowding_factor_panel.py `
  --crowding-panel <synthetic_crowding_panel.csv> `
  --prices <synthetic_prices_with_volume.csv> `
  --rebalance-date 2026-03-31 `
  --out <crowding_202603.parquet> `
  --output-format parquet `
  --no-manifest
```

Crowding outputs are also exact-join issuer-level external factor panels. Missing issuer
volume, margin balance, or short-interest fields remain blank and are listed in
`missing_flags`; the script does not use sector-level short-sale data as an
issuer-level proxy. Sector-only short-sale panels should be joined separately
with `external_factor_panels` using the relevant sector key. `crowding_raw` is a
simple unweighted mean of available issuer ratios, so strategy configs should
prefer explicit component weights when a specific interpretation matters.

### Execution Diagnostics

Small-account execution diagnostics are optional:

```yaml
reporting:
  execution_diagnostics:
    enabled: true
    high_cash_threshold: 0.30
```

When enabled, `run_qvm_walkforward.py` writes
`qvm_walkforward_execution_diagnostics_<token>.csv` with cash-weight, target
slot fill ratio, affordability skips, ADV reductions, buy/sell turnover, cost
and tax drag for the period, and selected/skipped lot-value distributions.
The summary and diagnostics also expose `small_account_path_dependency_flag`
and detail text when lot size, affordability, ADV caps, costs, taxes, or cash
can change future holdings. In that case, cost scenarios are path-dependent
simulations rather than a simple monotonic cost sensitivity.

### Margin and Leverage Diagnostics

Margin accounting is a generic long-margin research primitive and is disabled
by default. It does not encode broker-specific parameters or strategy
conclusions.

Config example:

```yaml
margin:
  enabled: true
  account_type: margin_long
  target_gross_leverage: 1.5
  max_gross_leverage: 2.0
  annual_borrow_rate: 0.03
  initial_margin_requirement: 0.50
  maintenance_margin_requirement: 0.25
  minimum_required_equity: 100000
  interest_day_count: 365
  margin_call_action: flag_only
```

CLI overrides are also available:

```powershell
python scripts\run_qvm_walkforward.py `
  --config configs\qvm_v0_1.example.yml `
  --listings <listings.csv> `
  --prices <prices.csv> `
  --fundamentals <fundamentals.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --margin-enabled `
  --target-gross-leverage 1.5 `
  --max-gross-leverage 2.0 `
  --annual-borrow-rate 0.03 `
  --initial-margin-requirement 0.50 `
  --maintenance-margin-requirement 0.25 `
  --minimum-required-equity 100000 `
  --margin-call-action flag_only
```

When enabled, target construction uses gross exposure capped by
`max_gross_leverage` and `initial_margin_requirement`, while lot size,
affordable-lot filtering, ADV caps, costs, tax lots, sector caps, and buy/hold
buffers still apply. Financing cost is reported separately from execution cost
and tax drag. The engine writes `qvm_walkforward_margin_daily_<token>.csv` and
`qvm_walkforward_margin_summary_<token>.csv`. `flag_only` reports margin and
minimum-equity breaches; it is not a forced liquidation model.

### Grouped Factor Diagnostics

`analyze_factor_forward_returns.py` can report factor behavior by group:

```powershell
python scripts\analyze_factor_forward_returns.py `
  --factors-dir <factors_dir> `
  --prices <prices.csv> `
  --start-date 2026-01-01 `
  --end-date 2026-12-31 `
  --holding-days 21 `
  --factor book_to_market `
  --group-field sector `
  --grouped-diagnostics `
  --no-manifest
```

The grouped CSV/Markdown outputs report IC, rank IC, top-bottom spread,
coverage, and missing rates by `rebalance_date + factor + group`. Add
`--group-neutral-quantiles` to assign quantile buckets inside each group.

## Validation Workflow

Legacy remains the reference path for audit and fallback. Use sampled windows
after major engine changes or when adding new strategy primitives:

```text
legacy walk-forward
DuckDB price/universe panel
DuckDB factor/score panel
walk-forward with --factor-score-panel
artifact parity comparison
```

Expected parity is summary except `cache_fingerprint`, trades, holdings,
equity, failure cases, and benchmark columns when supplied. Unexplained
differences block research use for the changed path. Public examples and tests
must remain synthetic; real-data parity artifacts and timing records belong in
private workspaces.

`--strategy-version weighted_groups` uses `strategy.scoring.weights` and
`strategy.filters` from the config. It writes `composite_score`,
`filter_status`, and `filter_reasons` while keeping `qvm_score` populated for
the older target/order scripts during the migration.
`--strategy-version configurable` supports the same group-weighted mode plus a
generic `weighted_factors` mode that combines configured factor z-scores
directly. Factor definitions under `factors.definitions` are evaluated by the
whitelist expression engine documented in `docs/factor_and_strategy_expressions.md`.

The research universe can retain names whose latest price is stale at the
rebalance date. Those rows carry `rebalance_price_available=false` and
`latest_price_stale=true`. Executable target generation treats those names as
non-orderable unless `strict_rebalance_price_filter` removes them upstream.
Walk-forward tail gaps are explicit through `missing_price_tail_policy`: the
public example defaults to `warn_only`, while conservative runs can use
`assume_zero_after_n_trading_days` with `max_stale_trading_days`.

## Run Ledger And Decision Notes

Run ledgers are CSV files intended for private workspaces. The public repo only
contains the generic schema and scripts.

```powershell
python scripts\append_run_record.py `
  --summary <qvm_walkforward_summary.csv> `
  --config configs\qvm_v0_1.example.yml `
  --ledger <private-run-ledger.csv> `
  --run-id <stable-run-id> `
  --hypothesis "Plain-language research hypothesis" `
  --predefined-metric after_cost_return `
  --go-no-go-criterion "Review threshold chosen before the run" `
  --decision REVIEW

python scripts\generate_decision_note.py `
  --ledger <private-run-ledger.csv> `
  --run-id <stable-run-id> `
  --out <private-decision-note.md>
```

Allowed decisions are `EXPLORATORY`, `REVIEW`, `REJECT`, and `PAPER_TEST`.
These tools create research notes only. They do not implement approvals,
permissions, immutable logs, compliance reports, dashboards, or schedulers.

## Event Account Simulation

Build a generic event panel from J-Quants-style statements rows using only
Standard-compatible disclosure fields:

```powershell
python scripts\build_jquants_statement_event_panel.py `
  --statements <synthetic_statements.csv> `
  --document-type-contains FinancialStatements `
  --document-type-contains EarnForecastRevision `
  --document-type-contains DividendForecastRevision `
  --out <statement_events.parquet> `
  --output-format parquet `
  --no-manifest
```

Run a daily-bar event account simulation:

```powershell
python scripts\run_event_account_simulator.py `
  --events <statement_events.parquet> `
  --prices <synthetic_daily_prices.csv> `
  --entry-lag-trading-days 1 `
  --entry-price-mode next_open `
  --holding-trading-days 20 `
  --exit-price-mode close `
  --initial-capital 1000000 `
  --target-event-weight 0.10 `
  --max-concurrent-positions 10 `
  --lot-size 100 `
  --out-dir data\processed\events `
  --run-label statement_drift_proxy `
  --no-manifest
```

The simulator is long-only v0.1. It tracks cash, event positions, daily equity,
trades, estimated realized-gain tax, and skipped-event failures. It requires
T+1 or later entry because Standard daily bars cannot prove same-day
post-announcement intraday execution. Statement-derived event signals should be
named financial-statement or forecast-revision drift proxies, not strict
earnings surprise, unless an external expectation dataset is supplied.

## Generic Diagnostics

These scripts provide strategy-agnostic diagnostics for QVM, event studies, and
future rankers:

```powershell
python scripts\audit_data_quality.py `
  --prices <prices.csv> `
  --listings <listings.csv> `
  --out reports\engineering\data_quality_issues.csv `
  --summary-out reports\engineering\data_quality_summary.csv `
  --report reports\engineering\data_quality_report.md `
  --no-manifest

python scripts\analyze_benchmark_attribution.py `
  --summary <walkforward_summary.csv> `
  --benchmark size=<size_benchmark.csv> `
  --out reports\benchmark\benchmark_attribution.csv `
  --report reports\benchmark\benchmark_attribution.md `
  --no-manifest

python scripts\generate_strategy_diagnostics_pack.py `
  --summary <walkforward_summary.csv> `
  --failures <failure_cases.csv> `
  --data-quality-summary reports\engineering\data_quality_summary.csv `
  --benchmark-attribution reports\benchmark\benchmark_attribution.csv `
  --out reports\strategy\strategy_diagnostics.md `
  --no-manifest
```

The diagnostics pack consumes only explicitly supplied artifacts. It does not
invent candidate, exposure, contribution, or ADV sections when those source
files are not passed.

## Walk-Forward Tear Sheet

Generate a static public-safe performance report from an existing walk-forward
run:

```powershell
python scripts\generate_walkforward_tearsheet.py `
  --summary <qvm_walkforward_summary.csv> `
  --failures <qvm_walkforward_failure_cases.csv> `
  --out reports\walkforward\walkforward_tearsheet.md `
  --no-manifest
```

The tear sheet writes a metrics CSV and SVG charts next to the Markdown report.
Metrics are sampled at the run's rebalance frequency, so monthly and quarterly
risk statistics should not be compared as if they were daily metrics.

## J-Quants

```powershell
$env:JQUANTS_API_KEY="..."
python scripts\validate_jquants.py --preflight-only
```

J-Quants downloads use the official `jquants-api-client` package and normalize
responses into this repository's CSV contracts.

Raw downloaded files are written under `data/raw/` and must remain untracked.

## Phase 1 Real Data Path

For the current real-data workflow, see:

```text
docs/phase_1_real_data_runbook.md
```

New lightweight helpers:

```powershell
python scripts\validate_contracts.py --help
python scripts\generate_candidate_review.py --help
python scripts\analyze_factor_forward_returns.py --help
python scripts\compare_walkforward_runs.py --help
```

## Smoke Test

```powershell
python scripts\smoke_test_universe.py
```
