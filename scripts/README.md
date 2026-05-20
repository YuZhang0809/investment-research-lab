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

`--engine duckdb` is the recommended research path for supported base
factor/score panels. `--engine legacy` is the reference, validation, and
fallback implementation; the CLI default remains legacy for backward
compatibility. DuckDB supports `qvm`, `qv`, `value_only`, and `weighted_groups`
with group filters, and it rejects custom factor expressions,
`strategy.group_relative_transforms`, `weighted_factors`, and field filters
instead of falling back silently. Configs with `external_factor_panels` should
also use `--engine legacy` until the DuckDB join path is implemented and
parity-tested.

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

For now this primitive is supported by the legacy factor/score path:

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
  --engine legacy `
  --out <rebalance_factor_score_panel.parquet> `
  --output-format parquet
```

The resulting panel keeps fields such as
`sector_relative_book_to_market_z` and
`sector_relative_book_to_market_rank_pct`, and
`run_qvm_walkforward.py --factor-score-panel` can consume them through the
normal score rows. DuckDB currently rejects this config explicitly.

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

Then run the legacy factor/score path with a config that contains
`external_factor_panels`. Joined fields can be used by `weighted_factors` and
field filters such as `exclude_equals`, `require_in`, `exclude_above_pct`, and
`exclude_below_pct`. Public examples must use synthetic external panels only.

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
