# Derived Fundamental Factor Panel

`scripts/build_derived_fundamental_factor_panel.py` creates public-safe,
point-in-time derived fundamental features from a disclosure history. It is a
generic transformer for event and factor research; it does not encode private
thresholds, real security lists, or strategy conclusions.

## Inputs

Minimum input fields:

```text
code,available_date,period_type,period_end
```

Useful numeric fields are optional but required for their corresponding derived
features:

```text
sales,operating_profit,net_profit,equity,total_assets,shares_outstanding
```

Accepted aliases include `net_sales`/`revenue` for sales, `operating_income`
for operating profit, `profit`/`profit_attributable_to_owners_of_parent` for
net profit, `net_assets` for equity, `assets` for total assets, and
`shares`/`avg_shares` for shares outstanding.

The PIT gate is `available_date` or `disclosure_date`. `period_end` is used only
to find the same-period prior-year row. Future restatements are not used before
their own availability date. The gate is date-level; workflows that trade
intraday must separately enforce announcement-time eligibility.

Duplicate rows with the same `code`, `period_type`, `period_end`,
availability timestamp, and statement scope are deduplicated deterministically.
The selected row reports `source_duplicate_count`. Statement scope values are
normalized to lowercase tokens, and boolean-like consolidated flags are mapped
to `consolidated` or `non_consolidated`.

## Outputs

Rebalance mode writes one latest-as-of row per `rebalance_date + code`:

```powershell
python scripts\build_derived_fundamental_factor_panel.py `
  --fundamentals <synthetic_fundamentals.csv> `
  --panel-mode rebalance `
  --rebalance-date 2026-03-31 `
  --period-type annual `
  --out data\processed\factors\derived_fundamentals_202603.csv `
  --output-format csv `
  --no-manifest
```

For each code, rebalance mode selects the latest reporting `period_end`
available by the rebalance date, then applies any PIT-eligible restatements for
that reporting period and its same-period prior-year comparison. A later
restatement for an older fiscal period does not replace a more recent reporting
period.

Rebalance output rejects mixed `period_type` values for the same rebalance date
by default because fields such as `roe` and `roa` are not cross-sectionally
comparable across annual and quarterly income periods. Use `--period-type` to
build a comparable panel. `--allow-mixed-period-types` is available for manual
event-style inspection, not for ordinary cross-sectional scoring.

Event mode writes one latest disclosure row per `available_date + code`, which
can be joined as an `external_factor_panels` as-of input:

```powershell
python scripts\build_derived_fundamental_factor_panel.py `
  --fundamentals <synthetic_fundamentals.csv> `
  --panel-mode event `
  --out data\processed\factors\derived_fundamentals_event.parquet `
  --output-format parquet `
  --no-manifest
```

Derived fields:

```text
sales_yoy
operating_profit_yoy
net_profit_yoy
operating_margin
operating_margin_delta_yoy
roe
roa
equity_to_assets
shares_outstanding_change_yoy
profit_turn_positive
```

`missing_flags` lists unavailable derived fields and `missing_prior_year` when
no same-period prior-year comparison row is available as of the current
disclosure timestamp or rebalance as-of date. `profit_turn_positive` is missing
when either current or prior net profit is missing. `roe` and `roa` use
point-in-time net profit divided by end-of-period equity/assets; they are not
average-balance textbook ROE/ROA.

## Integration

Rebalance-mode output can be consumed as a score/factor-side input by later
panel builders. Event-mode output is intentionally compatible with
`external_factor_panels` as an as-of panel keyed by:

```yaml
join_keys:
  - rebalance_date
  - code
asof:
  enabled: true
  date_field: available_date
```

Public examples and tests must remain synthetic.
