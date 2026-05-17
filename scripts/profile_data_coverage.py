from __future__ import annotations

import argparse
import calendar
from datetime import date
from pathlib import Path
from typing import Any

from build_universe import listings_as_of_snapshot
from research_common import append_manifest, parse_bool, parse_date, read_csv, write_csv


COVERAGE_FIELDS = [
    "rebalance_date",
    "listing_source_date",
    "listing_rows",
    "common_stock_codes",
    "price_any_history_codes",
    "price_on_or_before_codes",
    "price_on_date_codes",
    "fundamentals_available_codes",
    "common_with_price_history",
    "common_with_price_on_or_before",
    "common_with_price_on_date",
    "common_with_fundamentals",
    "common_with_price_and_fundamentals",
    "common_missing_price_on_or_before",
    "common_missing_fundamentals",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile generic data coverage across research rebalance dates.")
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="quarterly")
    parser.add_argument("--calendar", type=Path, help="Optional CSV/Parquet with a date column.")
    parser.add_argument("--out", type=Path, default=Path("reports/engineering/data_coverage_profile.csv"))
    parser.add_argument("--report", type=Path, default=Path("reports/engineering/data_coverage_profile.md"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def is_quarter_month(value: date, frequency: str) -> bool:
    return frequency == "monthly" or value.month in {3, 6, 9, 12}


def generated_month_ends(start: date, end: date, frequency: str) -> list[date]:
    values: list[date] = []
    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        if frequency == "monthly" or month in {3, 6, 9, 12}:
            last_day = calendar.monthrange(year, month)[1]
            candidate = date(year, month, last_day)
            if start <= candidate <= end:
                values.append(candidate)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return values


def rebalance_dates_from_calendar(
    *,
    start: date,
    end: date,
    frequency: str,
    calendar_rows: list[dict[str, str]],
) -> list[date]:
    groups: dict[str, date] = {}
    for row in calendar_rows:
        row_date = parse_date(row.get("date"), field_name="calendar.date")
        if row_date is None or row_date < start or row_date > end:
            continue
        if not is_quarter_month(row_date, frequency):
            continue
        key = row_date.strftime("%Y-%m")
        groups[key] = max(groups.get(key, row_date), row_date)
    return [groups[key] for key in sorted(groups)]


def resolve_rebalance_dates(
    *,
    start: date,
    end: date,
    frequency: str,
    prices: list[dict[str, str]],
    calendar_path: Path | None,
) -> list[date]:
    if calendar_path:
        dates = rebalance_dates_from_calendar(
            start=start,
            end=end,
            frequency=frequency,
            calendar_rows=read_csv(calendar_path),
        )
    else:
        dates = rebalance_dates_from_calendar(
            start=start,
            end=end,
            frequency=frequency,
            calendar_rows=prices,
        )
    return dates or generated_month_ends(start, end, frequency)


def is_common_research_stock(row: dict[str, str]) -> bool:
    if parse_bool(row.get("is_common_stock"), default=False) is not True:
        return False
    if parse_bool(row.get("is_etf_reit_infra"), default=False):
        return False
    security_type = (row.get("security_type") or "").strip().lower().replace(" ", "_").replace("-", "_")
    return not security_type or security_type == "common_stock"


def group_price_dates(rows: list[dict[str, str]]) -> dict[str, set[date]]:
    grouped: dict[str, set[date]] = {}
    for row in rows:
        code = (row.get("code") or "").strip()
        row_date = parse_date(row.get("date"), field_name="prices.date")
        if code and row_date is not None:
            grouped.setdefault(code, set()).add(row_date)
    return grouped


def group_fundamental_dates(rows: list[dict[str, str]]) -> dict[str, set[date]]:
    grouped: dict[str, set[date]] = {}
    for row in rows:
        code = (row.get("code") or "").strip()
        row_date = parse_date(
            row.get("available_date") or row.get("disclosure_date"),
            field_name="fundamentals.available_date",
        )
        if code and row_date is not None:
            grouped.setdefault(code, set()).add(row_date)
    return grouped


def codes_on_or_before(grouped: dict[str, set[date]], target: date) -> set[str]:
    return {code for code, values in grouped.items() if any(value <= target for value in values)}


def codes_on_date(grouped: dict[str, set[date]], target: date) -> set[str]:
    return {code for code, values in grouped.items() if target in values}


def selected_listing_source_date(rows: list[dict[str, str]]) -> str:
    values = sorted({row.get("source_date") or row.get("snapshot_date") or "" for row in rows})
    values = [value for value in values if value]
    return values[-1] if values else ""


def profile_coverage(
    *,
    listings: list[dict[str, str]],
    prices: list[dict[str, str]],
    fundamentals: list[dict[str, str]],
    rebalance_dates: list[date],
) -> list[dict[str, Any]]:
    price_dates = group_price_dates(prices)
    fundamental_dates = group_fundamental_dates(fundamentals)
    price_any_codes = set(price_dates)

    rows: list[dict[str, Any]] = []
    for rebalance_date in rebalance_dates:
        listing_snapshot = listings_as_of_snapshot(listings, rebalance_date)
        listing_codes = {(row.get("code") or "").strip() for row in listing_snapshot if row.get("code")}
        common_codes = {(row.get("code") or "").strip() for row in listing_snapshot if is_common_research_stock(row)}
        price_on_or_before = codes_on_or_before(price_dates, rebalance_date)
        price_on_rebalance = codes_on_date(price_dates, rebalance_date)
        fundamentals_available = codes_on_or_before(fundamental_dates, rebalance_date)
        common_with_price_and_fundamentals = common_codes & price_on_or_before & fundamentals_available

        rows.append(
            {
                "rebalance_date": rebalance_date,
                "listing_source_date": selected_listing_source_date(listing_snapshot),
                "listing_rows": len(listing_codes),
                "common_stock_codes": len(common_codes),
                "price_any_history_codes": len(price_any_codes),
                "price_on_or_before_codes": len(price_on_or_before),
                "price_on_date_codes": len(price_on_rebalance),
                "fundamentals_available_codes": len(fundamentals_available),
                "common_with_price_history": len(common_codes & price_any_codes),
                "common_with_price_on_or_before": len(common_codes & price_on_or_before),
                "common_with_price_on_date": len(common_codes & price_on_rebalance),
                "common_with_fundamentals": len(common_codes & fundamentals_available),
                "common_with_price_and_fundamentals": len(common_with_price_and_fundamentals),
                "common_missing_price_on_or_before": len(common_codes - price_on_or_before),
                "common_missing_fundamentals": len(common_codes - fundamentals_available),
            }
        )
    return rows


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Data Coverage Profile",
        "",
        "This report is strategy-agnostic. It checks whether listings, prices, and fundamentals overlap on each research date.",
        "",
    ]
    if not rows:
        lines.append("No rebalance dates were profiled.")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    final = rows[-1]
    lines.extend(
        [
            "## Latest Snapshot",
            "",
            "| metric | value |",
            "|---|---:|",
            f"| rebalance date | {final['rebalance_date']} |",
            f"| listing source date | {final['listing_source_date']} |",
            f"| common stock codes | {final['common_stock_codes']} |",
            f"| common with price on or before date | {final['common_with_price_on_or_before']} |",
            f"| common with fundamentals | {final['common_with_fundamentals']} |",
            f"| common with price and fundamentals | {final['common_with_price_and_fundamentals']} |",
            "",
            "## Period Coverage",
            "",
            "| rebalance | listings | common | price<=date | fundamentals<=date | common both |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| {rebalance_date} | {listing_rows} | {common_stock_codes} | {common_with_price_on_or_before} | "
            "{common_with_fundamentals} | {common_with_price_and_fundamentals} |".format(**row)
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    start = parse_date(args.from_date, field_name="from")
    end = parse_date(args.to_date, field_name="to")
    if start is None or end is None:
        raise ValueError("--from and --to are required YYYY-MM-DD dates.")

    listings = read_csv(args.listings)
    prices = read_csv(args.prices)
    fundamentals = read_csv(args.fundamentals)
    rebalance_dates = resolve_rebalance_dates(
        start=start,
        end=end,
        frequency=args.frequency,
        prices=prices,
        calendar_path=args.calendar,
    )
    rows = profile_coverage(
        listings=listings,
        prices=prices,
        fundamentals=fundamentals,
        rebalance_dates=rebalance_dates,
    )
    write_csv(args.out, rows, COVERAGE_FIELDS)
    write_report(args.report, rows)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_data_coverage_profile",
            file_path=args.out,
            vendor="local",
            schema_version="data_coverage_profile_v0_1",
            date_range=f"{start}..{end}",
            notes=f"{len(rows)} rebalance rows; frequency={args.frequency}",
        )
    print(f"Wrote {len(rows)} data coverage rows to {args.out}")
    print(f"Wrote data coverage report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
