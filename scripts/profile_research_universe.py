from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from build_universe import evaluate_row, group_fundamentals, group_prices, listings_as_of_snapshot
from profile_data_coverage import resolve_rebalance_dates, selected_listing_source_date
from research_common import append_manifest, load_yaml, parse_bool, parse_date, parse_float, read_csv, write_csv


SUMMARY_FIELDS = [
    "rebalance_date",
    "listing_source_date",
    "included_count",
    "excluded_count",
    "evaluated_count",
    "stale_price_included",
    "missing_rebalance_price_included",
    "with_fundamentals_included",
    "median_60d_trading_value_p10",
    "median_60d_trading_value_median",
    "median_60d_trading_value_p90",
    "top_exclusion_reasons",
]

REASON_FIELDS = ["rebalance_date", "reason", "count"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile research universe constraints across rebalance dates.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="quarterly")
    parser.add_argument("--calendar", type=Path, help="Optional CSV/Parquet with a date column.")
    parser.add_argument("--out-summary", type=Path, default=Path("reports/engineering/research_universe_profile.csv"))
    parser.add_argument("--out-reasons", type=Path, default=Path("reports/engineering/research_universe_exclusion_reasons.csv"))
    parser.add_argument("--report", type=Path, default=Path("reports/engineering/research_universe_profile.md"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def percentile(values: list[float], pct: float) -> float | str:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(clean) - 1)
    weight = position - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def reason_base(value: str) -> str:
    return value.split(":", 1)[0]


def count_exclusion_reasons(exclusion_rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in exclusion_rows:
        reasons = str(row.get("reason") or "").split(";")
        for reason in reasons:
            clean = reason_base(reason.strip())
            if clean:
                counts[clean] += 1
    return counts


def top_reason_text(counts: Counter[str], limit: int = 5) -> str:
    return ";".join(f"{reason}:{count}" for reason, count in counts.most_common(limit))


def profile_research_universe(
    *,
    config: dict[str, Any],
    listings: list[dict[str, str]],
    prices: list[dict[str, str]],
    fundamentals: list[dict[str, str]],
    rebalance_dates: list[date],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows: list[dict[str, Any]] = []
    reason_rows: list[dict[str, Any]] = []
    prices_by_code = group_prices(prices)
    market_calendar = sorted({point.date for points in prices_by_code.values() for point in points})

    for rebalance_date in rebalance_dates:
        snapshot = listings_as_of_snapshot(listings, rebalance_date)
        fundamentals_by_code = group_fundamentals(fundamentals, rebalance_date)
        active_calendar = [value for value in market_calendar if value <= rebalance_date]
        universe_rows: list[dict[str, Any]] = []
        exclusion_rows: list[dict[str, Any]] = []
        for listing in snapshot:
            output_row, reasons = evaluate_row(
                listing,
                config=config,
                rebalance_date=rebalance_date,
                prices_by_code=prices_by_code,
                market_calendar=active_calendar,
                fundamentals_by_code=fundamentals_by_code,
            )
            if reasons:
                exclusion_rows.append(
                    {
                        "rebalance_date": rebalance_date,
                        "code": output_row["code"],
                        "name": output_row["name"],
                        "reason": ";".join(reasons),
                        "detail": "",
                    }
                )
            else:
                universe_rows.append(output_row)
        counts = count_exclusion_reasons(exclusion_rows)
        trading_values = [
            value
            for value in (parse_float(row.get("median_60d_trading_value")) for row in universe_rows)
            if value is not None
        ]
        summary_rows.append(
            {
                "rebalance_date": rebalance_date,
                "listing_source_date": selected_listing_source_date(snapshot),
                "included_count": len(universe_rows),
                "excluded_count": len(exclusion_rows),
                "evaluated_count": len(universe_rows) + len(exclusion_rows),
                "stale_price_included": sum(1 for row in universe_rows if parse_bool(row.get("latest_price_stale"))),
                "missing_rebalance_price_included": sum(
                    1 for row in universe_rows if parse_bool(row.get("rebalance_price_available"), default=False) is False
                ),
                "with_fundamentals_included": sum(1 for row in universe_rows if parse_bool(row.get("has_fundamentals"))),
                "median_60d_trading_value_p10": percentile(trading_values, 0.10),
                "median_60d_trading_value_median": percentile(trading_values, 0.50),
                "median_60d_trading_value_p90": percentile(trading_values, 0.90),
                "top_exclusion_reasons": top_reason_text(counts),
            }
        )
        for reason, count in counts.most_common():
            reason_rows.append({"rebalance_date": rebalance_date, "reason": reason, "count": count})

    return summary_rows, reason_rows


def write_report(path: Path, summary_rows: list[dict[str, Any]], reason_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Research Universe Profile",
        "",
        "This report applies the configured universe constraints only. It does not score or select a strategy portfolio.",
        "",
    ]
    if not summary_rows:
        lines.append("No rebalance dates were profiled.")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    final = summary_rows[-1]
    latest_reasons = [row for row in reason_rows if row["rebalance_date"] == final["rebalance_date"]][:10]
    lines.extend(
        [
            "## Latest Snapshot",
            "",
            "| metric | value |",
            "|---|---:|",
            f"| rebalance date | {final['rebalance_date']} |",
            f"| listing source date | {final['listing_source_date']} |",
            f"| included | {final['included_count']} |",
            f"| excluded | {final['excluded_count']} |",
            f"| stale price included | {final['stale_price_included']} |",
            f"| missing rebalance price included | {final['missing_rebalance_price_included']} |",
            "",
            "## Latest Exclusion Reasons",
            "",
            "| reason | count |",
            "|---|---:|",
        ]
    )
    for row in latest_reasons:
        lines.append(f"| {row['reason']} | {row['count']} |")
    lines.extend(
        [
            "",
            "## Period Summary",
            "",
            "| rebalance | included | excluded | evaluated | p10 ADV | median ADV | p90 ADV |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary_rows:
        lines.append(
            "| {rebalance_date} | {included_count} | {excluded_count} | {evaluated_count} | "
            "{median_60d_trading_value_p10} | {median_60d_trading_value_median} | {median_60d_trading_value_p90} |".format(**row)
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    start = parse_date(args.from_date, field_name="from")
    end = parse_date(args.to_date, field_name="to")
    if start is None or end is None:
        raise ValueError("--from and --to are required YYYY-MM-DD dates.")

    config = load_yaml(args.config)
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
    summary_rows, reason_rows = profile_research_universe(
        config=config,
        listings=listings,
        prices=prices,
        fundamentals=fundamentals,
        rebalance_dates=rebalance_dates,
    )
    write_csv(args.out_summary, summary_rows, SUMMARY_FIELDS)
    write_csv(args.out_reasons, reason_rows, REASON_FIELDS)
    write_report(args.report, summary_rows, reason_rows)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_research_universe_profile",
            file_path=args.out_summary,
            vendor="local",
            schema_version="research_universe_profile_v0_1",
            date_range=f"{start}..{end}",
            notes=f"{len(summary_rows)} rebalance rows; frequency={args.frequency}",
        )
    print(f"Wrote {len(summary_rows)} research universe rows to {args.out_summary}")
    print(f"Wrote {len(reason_rows)} exclusion reason rows to {args.out_reasons}")
    print(f"Wrote research universe report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
