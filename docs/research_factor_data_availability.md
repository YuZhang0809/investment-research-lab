# Research Factor Data Availability

This note records public-safe data availability assumptions for generic factor
builders. It summarizes official J-Quants API documentation checked during
development and should be revisited when API plans change.

## J-Quants Availability Snapshot

The official API index lists price, market, index, financial, dividend, and
financial-statement-detail endpoints, including `/prices/daily_quotes`,
`/indices/topix`, `/fins/statements`, `/fins/dividend`,
`/fins/fs_details`, `/markets/weekly_margin_interest`,
`/markets/short_selling`, `/markets/short_selling_positions`, and
`/markets/daily_margin_interest`.

The official Python client README separates wrappers by plan. In the current
client documentation, Standard-or-higher includes short-sale ratio, short-sale
position report, and weekly margin interest wrappers, while Premium-or-higher
includes dividend and financial-statement-detail wrappers. A JPX news release
for the daily margin endpoint states that margin trading outstanding for daily
publication issues, short-sale positions, margin trading outstandings, and
short-sale sector data are Standard/Premium, while cash dividend data and
financial statement details are Premium.

## Practical Engine Implications

Low-volatility defensive factors are data-ready with daily prices and optional
TOPIX benchmark prices. No extra vendor endpoint is required.

Dividend sustainability is partially available from `/fins/statements` fields
such as forecast dividend per share, result dividend per share, forecast EPS,
and payout-ratio-style fields when present in the processed source. The richer
cash dividend endpoint `/fins/dividend` is treated as an optional Premium input,
not a baseline Standard dependency.

Net cash and detailed balance-sheet value factors require explicit cash,
debt, and liability fields. `/fins/statements` exposes some summary financial
fields such as assets, equity, and cash/equivalents in statement summaries, but
full BS/PL/CF details are an optional Premium input. The engine must leave
`net_cash_to_market_cap` and related factors missing when cash/debt/liability
fields are absent; it must not substitute weak proxies.

Crowding factors can be built from generic external panels derived from weekly
margin, daily-publication margin, or short-interest/short-position sources.
Sector short-sale data is sector-level, not a full historical issuer-level
short-interest panel. Builders should expose missing fields instead of
pretending that sector proxies are issuer-level short interest.

Strict PEAD or true earnings surprise needs before/after forecast or consensus
data with event timing. When only realized fundamentals or broad forecast rows
are available, public docs should call the result a fundamental improvement
drift proxy, not true earnings surprise.

## Source Links

- J-Quants API index: https://jpx.gitbook.io/j-quants-ja/api-reference
- `/fins/statements` field list: https://jpx.gitbook.io/j-quants-ja/api-reference/statements
- `/fins/dividend` field list: https://jpx.gitbook.io/j-quants-ja/api-reference/dividend
- `/markets/weekly_margin_interest`: https://jpx.gitbook.io/j-quants-ja/api-reference/weekly_margin_interest
- `/markets/short_selling`: https://jpx.gitbook.io/j-quants-en/api-reference/short_selling
- `/markets/daily_margin_interest`: https://jpx.gitbook.io/j-quants-ja/api-reference/daily_margin_interest
- Official J-Quants Python client plan grouping: https://github.com/J-Quants/jquants-api-client-python
- JPX daily margin data release and plan table: https://www.jpx.co.jp/english/corporate/news/news-releases/6020/20250822-01.html

## Public-Safe Contract Tools

Use `scripts/validate_optional_factor_contract.py` for synthetic or locally
processed optional inputs:

```powershell
python scripts\validate_optional_factor_contract.py `
  --panel <synthetic_dividend_panel.csv> `
  --contract dividend `
  --require-numeric forecast_dividend_per_share
```

Supported contracts are `dividend`, `balance_sheet`, and `crowding`. The
validator checks that a key, date, and any present numeric fields are parseable.
The `crowding` contract is issuer-level and requires an issuer code; sector-only
short-sale panels should be joined through `external_factor_panels` under a
sector-level field. The validator does not download or infer vendor data.
