from __future__ import annotations

import argparse
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from build_factors import build_factors
from build_rebalance_price_universe_panel import PANEL_FIELDS
from build_universe import evaluate_row, group_fundamentals, group_prices, listings_as_of_snapshot
from research_common import load_yaml, parse_date, parse_float, read_csv, read_table, trading_calendar_from_rows, write_csv
from run_qvm_walkforward import rebalance_dates


DEFAULT_COMPARE_FIELDS = [
    "included_flag",
    "exclusion_reason",
    "listing_lifecycle_status",
    "listed_date",
    "delisted_date",
    "last_trading_date",
    "lifecycle_exit_date",
    "latest_price_date",
    "latest_unadjusted_close",
    "rebalance_price_available",
    "latest_price_stale",
    "price_staleness_trading_days",
    "ipo_age_trading_days",
    "median_60d_trading_value",
    "return_12_1",
    "return_6_1",
]
DIFF_FIELDS = ["field", "rebalance_date", "code", "legacy_value", "fast_value", "difference_type"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare a fast price/universe panel against legacy universe/factor helpers.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", type=Path)
    parser.add_argument("--fast-panel", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="monthly")
    parser.add_argument("--field", action="append", dest="fields", help="Field to compare. Can be repeated.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tolerance", type=float, default=1e-9)
    return parser


def text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except TypeError:
        pass
    if isinstance(value, datetime):
        if value.time() == time.min:
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if str(value) in {"NaT", "nan", "None"}:
        return ""
    return str(value)


def bool_text(value: Any) -> str:
    normalized = text(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return "true"
    if normalized in {"0", "false", "f", "no", "n"}:
        return "false"
    return normalized


def number_text(value: Any, tolerance: float) -> str | None:
    parsed = parse_float(value)
    if parsed is None:
        return None
    rounded = round(parsed / tolerance) * tolerance if tolerance > 0 else parsed
    return f"{rounded:.12g}"


def normalized_value(value: Any, *, field: str, tolerance: float) -> str:
    if field.endswith("_flag") or field in {"included_flag", "rebalance_price_available", "latest_price_stale"}:
        return bool_text(value)
    numeric = number_text(value, tolerance)
    if numeric is not None:
        return numeric
    return text(value).strip()


def difference_type(legacy: str, fast: str) -> str:
    if legacy == "" and fast != "":
        return "missing_in_legacy"
    if legacy != "" and fast == "":
        return "missing_in_fast"
    return "value_mismatch"


def sorted_reason(value: Any) -> str:
    parts = [part for part in text(value).split(";") if part]
    return ";".join(sorted(parts))


def legacy_panel_rows(
    *,
    config: dict[str, Any],
    listing_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
    dates: list[date],
) -> list[dict[str, Any]]:
    prices_by_code = group_prices(price_rows)
    market_calendar = sorted({point.date for points in prices_by_code.values() for point in points})
    rows: list[dict[str, Any]] = []
    for rebalance_date in dates:
        fundamentals_by_code = group_fundamentals(fundamental_rows, rebalance_date)
        snapshot = listings_as_of_snapshot(listing_rows, rebalance_date)
        factors_by_code = {
            row["code"]: row
            for row in build_factors(
                rebalance_date=rebalance_date,
                universe_rows=[
                    output
                    for output, reasons in (
                        evaluate_row(
                            row,
                            config=config,
                            rebalance_date=rebalance_date,
                            prices_by_code=prices_by_code,
                            market_calendar=market_calendar,
                            fundamentals_by_code=fundamentals_by_code,
                        )
                        for row in snapshot
                    )
                    if not reasons
                ],
                price_rows=price_rows,
                fundamental_rows=fundamental_rows,
                config=config,
            )
        }
        for listing in snapshot:
            output, reasons = evaluate_row(
                listing,
                config=config,
                rebalance_date=rebalance_date,
                prices_by_code=prices_by_code,
                market_calendar=market_calendar,
                fundamentals_by_code=fundamentals_by_code,
            )
            factor = factors_by_code.get(output["code"], {})
            row = {field: "" for field in PANEL_FIELDS}
            row.update(output)
            row["included_flag"] = not reasons
            row["exclusion_reason"] = ";".join(reasons)
            row["lifecycle_exit_date"] = output.get("last_trading_date") or output.get("delisted_date") or ""
            row["return_12_1"] = factor.get("return_12_1", "")
            row["return_6_1"] = factor.get("return_6_1", "")
            rows.append(row)
    return rows


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    return (text(row.get("rebalance_date")), text(row.get("code")))


def compare_rows(
    *,
    legacy_rows: list[dict[str, Any]],
    fast_rows: list[dict[str, Any]],
    fields: list[str],
    tolerance: float,
) -> list[dict[str, str]]:
    legacy_by_key = {row_key(row): row for row in legacy_rows}
    fast_by_key = {row_key(row): row for row in fast_rows}
    diffs: list[dict[str, str]] = []
    for key in sorted(set(legacy_by_key) | set(fast_by_key)):
        legacy = legacy_by_key.get(key, {})
        fast = fast_by_key.get(key, {})
        for field in fields:
            legacy_value = sorted_reason(legacy.get(field, "")) if field == "exclusion_reason" else legacy.get(field, "")
            fast_value = sorted_reason(fast.get(field, "")) if field == "exclusion_reason" else fast.get(field, "")
            left = normalized_value(legacy_value, field=field, tolerance=tolerance)
            right = normalized_value(fast_value, field=field, tolerance=tolerance)
            if left == right:
                continue
            diffs.append(
                {
                    "field": field,
                    "rebalance_date": key[0],
                    "code": key[1],
                    "legacy_value": text(legacy_value),
                    "fast_value": text(fast_value),
                    "difference_type": difference_type(left, right),
                }
            )
    return diffs


def fast_panel_rows(path: Path) -> list[dict[str, Any]]:
    frame = read_table(path)
    return frame.to_dict(orient="records")


def compare_fast_to_legacy(
    *,
    config: dict[str, Any],
    listings_path: Path,
    prices_path: Path,
    fundamentals_path: Path | None,
    fast_panel_path: Path,
    start_date: date,
    end_date: date,
    frequency: str,
    fields: list[str],
    tolerance: float,
) -> list[dict[str, str]]:
    price_rows = read_csv(prices_path)
    dates = rebalance_dates(trading_calendar_from_rows(price_rows), start_date, end_date, frequency)
    legacy_rows = legacy_panel_rows(
        config=config,
        listing_rows=read_csv(listings_path),
        price_rows=price_rows,
        fundamental_rows=read_csv(fundamentals_path) if fundamentals_path else [],
        dates=dates,
    )
    return compare_rows(
        legacy_rows=legacy_rows,
        fast_rows=fast_panel_rows(fast_panel_path),
        fields=fields,
        tolerance=tolerance,
    )


def main() -> int:
    args = build_parser().parse_args()
    start_date = parse_date(args.start_date, field_name="start_date")
    end_date = parse_date(args.end_date, field_name="end_date")
    if start_date is None or end_date is None:
        raise ValueError("start-date and end-date are required")
    fields = args.fields or DEFAULT_COMPARE_FIELDS
    diffs = compare_fast_to_legacy(
        config=load_yaml(args.config),
        listings_path=args.listings,
        prices_path=args.prices,
        fundamentals_path=args.fundamentals,
        fast_panel_path=args.fast_panel,
        start_date=start_date,
        end_date=end_date,
        frequency=args.frequency,
        fields=fields,
        tolerance=args.tolerance,
    )
    write_csv(args.out, diffs, DIFF_FIELDS)
    print(f"Wrote {len(diffs)} fast-vs-legacy panel differences to {args.out}")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
