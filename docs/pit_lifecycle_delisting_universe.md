# PIT Lifecycle and Delisting-Aware Universe

This document describes a generic public-engine capability. It must stay
strategy-agnostic and must not include private universes, private parameter
choices, real candidate lists, or go/no-go conclusions.

## Why This Matters

A walk-forward result is not validation-grade if it uses a current stock list
to test historical periods. That creates survivorship bias: stocks that later
delisted, merged, were acquired, or became untradable can disappear from the
test even though they were valid historical candidates.

The engine should support a point-in-time lifecycle universe so every
rebalance date only sees securities that were eligible at that date, while
still preserving later delistings and other exits in the backtest path.

## Definitions

- Point-in-time (PIT): use only data known as of the rebalance or execution
  date.
- Lifecycle: listed, active, suspended, delisted, merged, acquired, transferred,
  or otherwise removed from normal trading.
- Delisting-aware universe: historical universes include securities that later
  delisted, and the walk-forward runner handles their final trading path instead
  of silently dropping them.
- Snapshot panel: repeated historical master snapshots that improve PIT
  eligibility but may still lack exact lifecycle dates.

## Scope

The public engine should own:

- lifecycle-aware input schemas
- PIT universe eligibility rules
- delisting and missing-price execution policies
- lifecycle data-quality gates
- synthetic fixtures and tests for delisting, suspension, merger, and missing
  lifecycle cases
- generic diagnostics that report lifecycle coverage and exit handling

The public engine must not own:

- private real code lists
- private research universe selection
- private strategy weights or thresholds
- private performance conclusions
- vendor data redistribution

## Data Contract

The listings/lifecycle contract should support these fields:

```text
code
name
market
sector
security_type
is_common_stock
is_etf_reit_infra
listed_date
delisted_date
last_trading_date
listing_lifecycle_status
delisting_reason
successor_code
tradable_flag
lot_size
source_date
source
```

Minimum useful states for `listing_lifecycle_status`:

```text
active
suspended
delisted
merged
acquired
transferred
snapshot_only_missing_lifecycle_dates
pit_snapshot_panel_missing_lifecycle_dates
pit_inferred_lifecycle_active
pit_inferred_lifecycle_terminal
pit_inferred_lifecycle_unknown
unknown
```

The contract may be populated from a vendor master, exchange master snapshots,
manual synthetic fixtures, or private local enrichment. The public repository
should only contain synthetic examples.

## Universe Rules

For each rebalance date, the universe builder should:

1. Select the latest listing snapshot with `source_date <= rebalance_date` when
   snapshot panels are supplied.
2. Include only securities with `listed_date <= rebalance_date` when
   `listed_date` is known.
3. Exclude securities with `delisted_date < rebalance_date` or
   `last_trading_date < rebalance_date` when those dates are known.
4. Keep securities that later delist if they were eligible on the rebalance
   date.
5. Exclude non-common securities when configured, such as ETF, REIT, infra
   funds, preferred shares, and other non-target security types.
6. Emit included and excluded tables with reason codes for every evaluated
   security.

The builder should not infer unknown lifecycle dates from future price gaps
unless an explicit policy says so. Unknown lifecycle should be surfaced as a
data-quality caveat.

## Walk-Forward Behavior

The walk-forward runner should handle lifecycle events explicitly:

- If a holding has a valid final tradable price, value or exit at that price
  according to the configured policy.
- If a holding becomes non-tradable, emit a failure case and apply the
  configured missing-price tail policy.
- If a delisting date is known but no final price is available, do not silently
  delete the position.
- If a successor code is known, record it separately; do not assume a merger
  conversion ratio unless the data contract supplies one.
- Price-limit and trading-halt rows should be treated as execution constraints,
  not ordinary liquid fills.

Initial public policies can stay simple:

```text
warn_only
freeze_last_price
assume_zero_after_n_trading_days
```

These policies are research assumptions, so summaries and reports must print
which one was used.

## Data Gates

The engine should classify lifecycle readiness separately from strategy
performance:

```text
unknown
snapshot_only
partial_lifecycle
pit_snapshot_panel
pit_inferred_lifecycle
pit_no_delistings_observed
pit_with_delistings
```

Recommended interpretation:

- `snapshot_only`: exploratory only.
- `partial_lifecycle`: exploratory only; lifecycle fields are incomplete.
- `pit_snapshot_panel`: useful for research iteration, but not enough for
  validation-grade performance conclusions.
- `pit_inferred_lifecycle`: better than snapshot-only because it has explicit
  inferred entry and exit dates, but still exploratory because the dates are
  not from an authoritative lifecycle feed.
- `pit_no_delistings_observed`: acceptable only for short or synthetic samples
  where no delisting is expected.
- `pit_with_delistings`: validation-grade lifecycle coverage, assuming prices
  and corporate actions also pass audit.

`performance_conclusion_allowed` should be computed from lifecycle readiness
and blocking data-quality issues. It should not be hard-coded to a strategy.

## Diagnostics

Every walk-forward summary or diagnostics pack should be able to report:

```text
lifecycle_data_status
performance_conclusion_allowed
rebalance_count
included_count
excluded_count
delisted_candidates_count
delisted_holdings_count
unknown_lifecycle_count
missing_last_price_count
non_tradable_holdings_count
missing_price_tail_policy
price_after_delisting_count
```

Failure cases should include reason codes such as:

```text
delisted_holding
missing_final_price
non_tradable_holding
price_after_delisting
unknown_lifecycle
snapshot_only_lifecycle
successor_without_conversion_terms
```

## Implementation Plan

Phase 1: contracts and synthetic tests

- Extend listings fixtures with lifecycle fields.
- Add validation for lifecycle dates and state values.
- Add synthetic tests for active, newly listed, delisted, suspended, and merged
  securities.

Phase 2: universe builder

- Make PIT lifecycle filtering explicit in `build_universe.py`.
- Emit exclusion reason counts for lifecycle decisions.
- Preserve later-delisted securities when eligible at rebalance date.

Phase 3: walk-forward exit handling

- Add synthetic walk-forward tests where a held security delists.
- Ensure holdings are valued through the configured missing-price tail policy.
- Log lifecycle-related failure cases.

Phase 4: diagnostics and gates

- Add lifecycle metrics to summaries, run ledger, data-quality audit, and
  strategy diagnostics pack.
- Make `performance_conclusion_allowed` depend on lifecycle readiness and
  blocking data-quality issues.

Phase 5: private integration path

- Private workspaces can provide real lifecycle-enriched listings and real
  research universes.
- Any bug found in private runs should be reduced to a generic synthetic case
  before moving back into the public engine.

## Acceptance Criteria

The public engine is ready when:

- synthetic fixtures prove that current-survivor-only backtests are rejected or
  caveated
- delisted securities remain in historical universes before their exit date
- holdings that delist are not silently dropped
- summaries expose lifecycle status and exit policy
- diagnostics distinguish blocking data errors from execution constraints
- public tests cover lifecycle edge cases without private data
