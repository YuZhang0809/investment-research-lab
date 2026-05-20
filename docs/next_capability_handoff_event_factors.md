# Next Capability Handoff: Event and Defensive Factor Research

This note defines the next public-safe engine capabilities to develop. It is a
generic research-engine handoff only. Do not add private datasets, private
strategy conclusions, real security lists, broker data, account values, or
vendor data outputs.

## Development Goal

Extend the engine beyond static value/quality/momentum scoring so it can test
new factor families with point-in-time controls, executable small-account
constraints, and matched benchmarks.

The first development wave should focus on reusable primitives, not on finding
or hard-coding a winning strategy.

## Priority 1: Event-Driven Fundamental Announcement Research

Build a generic event-driven research path for post-disclosure drift and
fundamental improvement signals.

Required capabilities:

- Accept a public-safe event panel with `event_id`, `code`,
  `announcement_datetime`, `available_date`, `available_time`, `event_label`,
  and optional numeric event fields.
- Support entry timing modes such as `next_open`, `next_close`, and
  `t_plus_n_open` after the event becomes tradable.
- Support fixed holding windows such as 20, 60, and 120 trading days, plus
  "hold until next scheduled rebalance" for account-level simulations.
- Compute event-level forward returns and aggregate drift diagnostics by event
  label, sector, size bucket, listing segment, and calendar regime.
- Provide matched-control benchmarks for event windows:
  same date window random basket, same sector basket, and same size bucket
  basket.
- Add synthetic tests that verify no event can use data before its
  `available_date` and no trade can execute before the selected entry rule.

Useful first signals to support as generic examples:

- sales year-over-year change
- operating profit year-over-year change
- net profit year-over-year change
- operating margin change
- return-on-equity or return-on-assets change
- profit turn from negative to positive

These examples must be generated from synthetic fixtures. They must not encode
private thresholds, private universes, or real conclusions.

## Priority 2: PIT Derived Fundamental Factor Panel

Build a reusable transformer that creates derived, point-in-time fundamental
features from a disclosure history.

Required capabilities:

- Deduplicate disclosures by `code`, `period_type`, `period_end`, and
  disclosure timestamp.
- Compute same-period prior-year comparisons without lookahead.
- Emit a factor panel keyed by `rebalance_date` and `code`, or by
  `available_date` and `code` for event workflows.
- Include clear missing-data flags for each derived factor.
- Keep the output compatible with `--factor-score-panel` and
  `external_factor_panels`.

Candidate derived fields:

- `sales_yoy`
- `operating_profit_yoy`
- `net_profit_yoy`
- `operating_margin`
- `operating_margin_delta_yoy`
- `roe`
- `roa`
- `equity_to_assets`
- `shares_outstanding_change_yoy`

Acceptance criteria:

- Unit tests cover annual and quarterly periods, missing prior-year periods,
  duplicate disclosures, restatements, and mixed consolidated/non-consolidated
  examples.
- Synthetic fixtures demonstrate both rebalance-level and event-level output.

## Priority 3: Defensive Price Factor Primitives

Add generic price-derived defensive factors that can be used by the existing
walk-forward engine.

Required capabilities:

- Realized volatility over 3M, 6M, and 12M windows.
- Downside volatility over 6M and 12M windows.
- Rolling max drawdown over 6M and 12M windows.
- Rolling beta to a supplied market benchmark such as TOPIX.
- Optional filters for stale prices, limit-hit days, and insufficient history.
- Output fields that can be consumed through a factor-score panel.

Acceptance criteria:

- Synthetic price fixtures verify split-adjusted return handling,
  insufficient-history behavior, and benchmark beta calculations.
- Reports include return, drawdown, turnover, cash drag, and benchmark-relative
  diagnostics.

## Priority 4: Optional Data Contract Extensions

Prepare optional schema extensions for data sources that may or may not be
available in a given environment. The engine should validate and use these
fields when present, but must not require them for baseline tests.

Dividend and forecast fields:

- forecast dividend per share
- actual dividend per share
- forecast payout ratio
- forecast sales, operating profit, ordinary profit, and net profit
- forecast revision before/after values when available

Balance-sheet value fields:

- cash and equivalents
- interest-bearing debt
- total liabilities
- net cash proxy fields

Crowding and credit fields:

- margin buy balance
- margin sell balance
- short interest or lending balance
- days-to-cover or turnover-normalized crowding metrics

Acceptance criteria:

- Validators distinguish "field missing" from "field present but invalid".
- Documentation states which factors are unavailable without the optional
  fields.
- Examples use synthetic data only.

## Reporting and Diagnostics

All new workflows should produce public-safe diagnostics:

- factor coverage by date and segment
- missing-data rate by factor
- turnover and holding-period statistics
- benchmark-relative performance metrics
- event overlap and duplicate-event diagnostics
- pre/post regime split support as a generic calendar split, without embedding
  private interpretation

## Guardrails

- Do not commit raw or processed vendor data.
- Do not commit private strategy parameters, selected securities, real
  candidate lists, or go/no-go conclusions.
- Do not make the public engine depend on private workspace paths.
- Keep all examples and tests synthetic.
- Keep private research configs outside this repository.

## Suggested Build Order

1. Implement the PIT derived fundamental factor panel. Completed by
   `build_derived_fundamental_factor_panel.py`.
2. Add event-driven entry and holding-window simulation primitives.
3. Add matched-control event benchmarks.
4. Add defensive price factor panel generation. Initial generic builder:
   `build_price_defensive_factor_panel.py`.
5. Add optional contract extensions for dividend, forecast, balance-sheet, and
   crowding fields.
