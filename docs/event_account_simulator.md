# Event Account Simulator

`scripts/run_event_account_simulator.py` runs a generic long-only,
event-driven account simulation from a public-safe event panel and daily price
bars. It is designed for research workflows that have disclosure/event dates
and daily OHLC data, not for broker-specific or intraday execution modeling.

## Standard Data Boundary

The Standard-compatible boundary is:

- daily OHLC and adjusted prices from daily price data
- event dates and times from disclosure/statement rows
- T+1 or later entry using daily `open` or `close`
- fixed trading-day holding windows

The simulator intentionally rejects `--entry-lag-trading-days 0`. Same-day
post-announcement trading requires intraday/session data and should not be
modeled from daily bars alone.

J-Quants-style statement events can be prepared with
`scripts/build_jquants_statement_event_panel.py`. The resulting signals should
be described as financial-statement, company-forecast-revision,
dividend-forecast-revision, or fundamental-improvement drift proxy inputs. They
are not strict earnings-surprise signals unless an external expected/consensus
dataset is supplied.

## Event Panel

Minimum input fields:

```text
event_id,announcement_datetime,code,event_label
```

`event_id` must be unique. The simulator fails fast on duplicates because
trade, position, and failure rows use it as the audit key.

Optional audit fields include:

```text
company_name,document_type,title,url_or_doc_id,parsed_flag,parse_confidence,notes
```

J-Quants statement rows can be converted into this shape:

```powershell
python scripts\build_jquants_statement_event_panel.py `
  --statements <synthetic_statements.csv> `
  --document-type-contains FinancialStatements `
  --document-type-contains EarnForecastRevision `
  --document-type-contains DividendForecastRevision `
  --out data\processed\events\statement_events.parquet `
  --output-format parquet `
  --no-manifest
```

## Account Simulation

Example:

```powershell
python scripts\run_event_account_simulator.py `
  --events data\processed\events\statement_events.parquet `
  --prices <synthetic_daily_prices.csv> `
  --entry-lag-trading-days 1 `
  --entry-price-mode next_open `
  --holding-trading-days 20 `
  --exit-price-mode close `
  --initial-capital 1000000 `
  --target-event-weight 0.10 `
  --max-concurrent-positions 10 `
  --lot-size 100 `
  --commission-bps 0 `
  --tax-rate 0 `
  --out-dir data\processed\events `
  --run-label statement_drift_proxy `
  --no-manifest
```

Outputs:

```text
event_account_summary_<label>.csv
event_account_trades_<label>.csv
event_account_positions_<label>.csv
event_account_equity_<label>.csv
event_account_failure_cases_<label>.csv
```

The simulator processes exits before entries on the same date, buys only when
the intended daily price is present and tradable, and does not forward-fill or
roll missing entry prices. Entry modes are `next_open` and `next_close`; exit
mode can be `open` or `close`. The summary `trade_count` is a trade-leg count,
not a closed-position count. It tracks cash, event positions, commissions,
estimated realized-gain tax, daily equity, closed-position returns, and skipped
event failure reasons.

## Limits

This is a research simulator:

- It is long-only in v0.1.
- It does not implement margin borrowing or forced liquidation.
- It does not model intraday sequencing within a daily bar.
- It does not infer expected earnings or analyst consensus.
- It does not include private strategy thresholds or conclusions.

Public examples and tests must remain synthetic.
