from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from research_common import (
    append_manifest,
    month_key,
    parse_date,
    parse_float,
    read_csv,
    trading_calendar_from_rows,
    trading_day_offset,
    write_csv,
)


TRADING_DAYS_1M = 21
TRADING_DAYS_6M = 126
TRADING_DAYS_12M = 252


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build QVM raw factor CSV for a rebalance date.")
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/factors"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def group_by_code(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        code = (row.get("code") or "").strip()
        if code:
            grouped[code].append(row)
    return grouped


def price_date(row: dict[str, str]) -> date | None:
    return parse_date(row.get("date"), field_name="prices.date")


def rows_until_rebalance(rows: list[dict[str, str]], rebalance_date: date) -> list[dict[str, str]]:
    clean = [row for row in rows if price_date(row) and price_date(row) <= rebalance_date]
    return sorted(clean, key=lambda row: price_date(row) or date.min)


def adjusted_close(row: dict[str, str] | None) -> float | None:
    if row is None:
        return None
    return parse_float(row.get("adjusted_close") or row.get("unadjusted_close"))


def price_on_or_before(rows: list[dict[str, str]], target: date) -> dict[str, str] | None:
    latest: dict[str, str] | None = None
    for row in rows:
        row_date = price_date(row)
        if row_date is None:
            continue
        if row_date > target:
            break
        latest = row
    return latest


def return_with_skip(
    rows: list[dict[str, str]],
    calendar: list[date],
    rebalance_date: date,
    lookback_days: int,
    skip_days: int,
) -> float | None:
    end_date = trading_day_offset(calendar, rebalance_date, -skip_days, mode="on_or_before")
    start_date = trading_day_offset(calendar, rebalance_date, -lookback_days, mode="on_or_before")
    if start_date is None or end_date is None or start_date >= end_date:
        return None
    start_row = price_on_or_before(rows, start_date)
    end_row = price_on_or_before(rows, end_date)
    if start_row is None or end_row is None:
        return None
    end_price = adjusted_close(end_row)
    start_price = adjusted_close(start_row)
    if end_price is None or start_price is None or start_price <= 0:
        return None
    return end_price / start_price - 1.0


def latest_fundamental(rows: list[dict[str, str]], rebalance_date: date) -> dict[str, str] | None:
    candidates = []
    for row in rows:
        available_date = parse_date(row.get("available_date"), field_name="fundamentals.available_date")
        if available_date and available_date <= rebalance_date:
            candidates.append(row)
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (row.get("available_date", ""), row.get("available_time", "")))[-1]


def first_number(*values: Any) -> float | None:
    for value in values:
        parsed = parse_float(value)
        if parsed is not None:
            return parsed
    return None


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def build_factors(
    *,
    rebalance_date: date,
    universe_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    prices_by_code = group_by_code(price_rows)
    calendar = trading_calendar_from_rows(price_rows)
    fundamentals_by_code = group_by_code(fundamental_rows)
    factor_rows: list[dict[str, Any]] = []

    for asset in universe_rows:
        code = asset.get("code", "")
        prices = rows_until_rebalance(prices_by_code.get(code, []), rebalance_date)
        latest_price_row = prices[-1] if prices else None
        latest_close = parse_float(asset.get("latest_unadjusted_close")) or adjusted_close(latest_price_row)
        fundamental = latest_fundamental(fundamentals_by_code.get(code, []), rebalance_date)

        operating_profit = first_number(fundamental.get("operating_profit") if fundamental else None)
        net_profit = first_number(fundamental.get("net_profit") if fundamental else None)
        equity = first_number(fundamental.get("equity") if fundamental else None)
        total_assets = first_number(fundamental.get("total_assets") if fundamental else None)
        shares = first_number(
            fundamental.get("shares_outstanding") if fundamental else None,
            fundamental.get("avg_shares") if fundamental else None,
        )
        market_cap = latest_close * shares if latest_close is not None and shares is not None else None

        raw_values = {
            "operating_profit_to_total_assets": safe_ratio(operating_profit, total_assets),
            "equity_to_assets": safe_ratio(equity, total_assets),
            "earnings_yield": safe_ratio(net_profit, market_cap),
            "book_to_market": safe_ratio(equity, market_cap),
            "return_12_1": return_with_skip(
                prices,
                calendar,
                rebalance_date,
                TRADING_DAYS_12M,
                TRADING_DAYS_1M,
            ),
            "return_6_1": return_with_skip(
                prices,
                calendar,
                rebalance_date,
                TRADING_DAYS_6M,
                TRADING_DAYS_1M,
            ),
        }
        missing_flags = [name for name, value in raw_values.items() if value is None]

        factor_rows.append(
            {
                "rebalance_date": rebalance_date,
                "code": code,
                "name": asset.get("name", ""),
                "market": asset.get("market", ""),
                "sector": asset.get("sector", ""),
                "price_date": latest_price_row.get("date", "") if latest_price_row else "",
                "latest_unadjusted_close": latest_close,
                "fundamentals_available_date": fundamental.get("available_date", "") if fundamental else "",
                "document_type": fundamental.get("document_type", "") if fundamental else "",
                "market_cap": market_cap,
                "operating_profit": operating_profit,
                "net_profit": net_profit,
                "equity": equity,
                "total_assets": total_assets,
                "shares": shares,
                **raw_values,
                "missing_flags": ";".join(missing_flags),
            }
        )
    return factor_rows


def main() -> int:
    args = build_parser().parse_args()
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")

    rows = build_factors(
        rebalance_date=rebalance_date,
        universe_rows=read_csv(args.universe),
        price_rows=read_csv(args.prices),
        fundamental_rows=read_csv(args.fundamentals),
    )
    suffix = month_key(rebalance_date)
    output_path = args.out_dir / f"factors_{suffix}.csv"
    fieldnames = [
        "rebalance_date",
        "code",
        "name",
        "market",
        "sector",
        "price_date",
        "latest_unadjusted_close",
        "fundamentals_available_date",
        "document_type",
        "market_cap",
        "operating_profit",
        "net_profit",
        "equity",
        "total_assets",
        "shares",
        "operating_profit_to_total_assets",
        "equity_to_assets",
        "earnings_yield",
        "book_to_market",
        "return_12_1",
        "return_6_1",
        "missing_flags",
    ]
    write_csv(output_path, [{key: fmt(value) for key, value in row.items()} for row in rows], fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_factors",
            file_path=output_path,
            vendor="local",
            schema_version="factors_v0_1",
            date_range=args.rebalance_date,
            notes=f"{len(rows)} rows",
        )
    print(f"Wrote {len(rows)} factor rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
