from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_common import (
    append_manifest,
    load_yaml,
    median_or_none,
    month_key,
    parse_bool,
    parse_date,
    parse_float,
    parse_int,
    read_csv,
    write_csv,
)


@dataclass
class PricePoint:
    date: Any
    trading_value: float | None
    unadjusted_close: float | None
    tradable_flag: bool | None
    price_limit_flag: bool | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build point-in-time universe for QVM research.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--rebalance-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--listings", required=True, type=Path, help="Listings/master CSV.")
    parser.add_argument("--prices", required=True, type=Path, help="Daily prices CSV.")
    parser.add_argument("--fundamentals", required=True, type=Path, help="Fundamentals CSV.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/processed/universe"),
        help="Output directory for universe and exclusions.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest/data_manifest.csv"),
        help="Manifest CSV to append derived outputs.",
    )
    parser.add_argument("--no-manifest", action="store_true", help="Do not register outputs.")
    return parser


def normalize_kind(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def group_prices(rows: list[dict[str, str]]) -> dict[str, list[PricePoint]]:
    grouped: dict[str, list[PricePoint]] = defaultdict(list)
    for row in rows:
        code = (row.get("code") or "").strip()
        if not code:
            continue
        row_date = parse_date(row.get("date"), field_name="prices.date")
        if row_date is None:
            continue
        grouped[code].append(
            PricePoint(
                date=row_date,
                trading_value=parse_float(row.get("trading_value")),
                unadjusted_close=parse_float(
                    row.get("unadjusted_close") or row.get("close") or row.get("price")
                ),
                tradable_flag=parse_bool(row.get("tradable_flag"), default=True),
                price_limit_flag=parse_bool(row.get("price_limit_flag"), default=False),
            )
        )
    for values in grouped.values():
        values.sort(key=lambda item: item.date)
    return grouped


def price_on_date(points: list[PricePoint], target: Any) -> PricePoint | None:
    for point in points:
        if point.date == target:
            return point
        if point.date > target:
            return None
    return None


def group_fundamentals(rows: list[dict[str, str]], rebalance_date: Any) -> dict[str, bool]:
    available: dict[str, bool] = {}
    for row in rows:
        code = (row.get("code") or "").strip()
        if not code:
            continue
        available_date = parse_date(
            row.get("available_date") or row.get("disclosure_date"),
            field_name="fundamentals.available_date",
        )
        if available_date and available_date <= rebalance_date:
            available[code] = True
    return available


def listing_source_date(row: dict[str, str]) -> Any | None:
    return parse_date(row.get("source_date") or row.get("snapshot_date"), field_name="listings.source_date")


def listings_as_of_snapshot(rows: list[dict[str, str]], rebalance_date: Any) -> list[dict[str, str]]:
    source_dates = [listing_source_date(row) for row in rows if row.get("source_date") or row.get("snapshot_date")]
    clean_dates = [value for value in source_dates if value is not None and value <= rebalance_date]
    if not clean_dates:
        return rows
    selected_date = max(clean_dates)
    selected: list[dict[str, str]] = []
    for row in rows:
        source_date = listing_source_date(row)
        if source_date != selected_date:
            continue
        copied = dict(row)
        if not copied.get("listed_date"):
            copied["listing_lifecycle_status"] = "pit_snapshot_panel_missing_lifecycle_dates"
        selected.append(copied)
    return selected


def active_as_of(row: dict[str, str], rebalance_date: Any) -> tuple[bool, str]:
    listed_date = parse_date(row.get("listed_date"), field_name="listings.listed_date")
    delisted_date = parse_date(row.get("delisted_date"), field_name="listings.delisted_date")
    if listed_date and listed_date > rebalance_date:
        return False, f"listed_after_rebalance:{listed_date}"
    if delisted_date and delisted_date <= rebalance_date:
        return False, f"delisted_before_or_on_rebalance:{delisted_date}"
    return True, ""


def security_allowed(row: dict[str, str], config: dict[str, Any]) -> tuple[bool, str]:
    security_type = normalize_kind(row.get("security_type"))
    excluded = {normalize_kind(value) for value in config["scope"]["instruments"].get("exclude", [])}
    included = {normalize_kind(value) for value in config["scope"]["instruments"].get("include", [])}

    is_common = parse_bool(row.get("is_common_stock"), default=None)
    is_etf_reit_infra = parse_bool(row.get("is_etf_reit_infra"), default=False)

    if is_etf_reit_infra:
        return False, "excluded_instrument_flag"
    if security_type in excluded:
        return False, f"excluded_security_type:{security_type}"
    if is_common is False:
        return False, "not_common_stock"
    if included and security_type and security_type not in included and is_common is not True:
        return False, f"not_in_included_security_types:{security_type}"
    return True, ""


def evaluate_row(
    row: dict[str, str],
    *,
    config: dict[str, Any],
    rebalance_date: Any,
    prices_by_code: dict[str, list[PricePoint]],
    market_calendar: list[Any],
    fundamentals_by_code: dict[str, bool],
) -> tuple[dict[str, Any], list[str]]:
    code = (row.get("code") or "").strip()
    reasons: list[str] = []

    active, reason = active_as_of(row, rebalance_date)
    if not active:
        reasons.append(reason)

    allowed, reason = security_allowed(row, config)
    if not allowed:
        reasons.append(reason)

    listing_tradable = parse_bool(row.get("tradable_flag"), default=None)
    if config["universe"].get("require_tradable_on_rebalance_date", True) and listing_tradable is False:
        reasons.append("listing_not_tradable")

    all_price_points = [point for point in prices_by_code.get(code, []) if point.date <= rebalance_date]
    min_ipo_days = int(config["universe"].get("min_ipo_age_trading_days") or 0)
    if len(all_price_points) < min_ipo_days:
        reasons.append(f"insufficient_ipo_age_trading_days:{len(all_price_points)}<{min_ipo_days}")

    lookback_days = int(config["universe"].get("liquidity_lookback_days") or 60)
    lookback = all_price_points[-lookback_days:]
    if len(lookback) < lookback_days:
        reasons.append(f"insufficient_liquidity_lookback:{len(lookback)}<{lookback_days}")

    median_trading_value = median_or_none(
        [point.trading_value for point in lookback if point.trading_value is not None]
    )
    min_trading_value = config["universe"].get("min_median_trading_value_jpy")
    if min_trading_value is not None:
        min_trading_value_float = float(min_trading_value)
        if median_trading_value is None or median_trading_value < min_trading_value_float:
            reasons.append(f"below_min_median_trading_value:{median_trading_value}")

    rebalance_price = price_on_date(prices_by_code.get(code, []), rebalance_date)
    latest = rebalance_price or (all_price_points[-1] if all_price_points else None)
    rebalance_price_available = rebalance_price is not None
    latest_price_stale = latest is not None and not rebalance_price_available
    price_staleness_trading_days = (
        sum(1 for value in market_calendar if latest and latest.date < value <= rebalance_date)
        if latest_price_stale
        else 0 if latest else None
    )
    if latest is None:
        reasons.append("no_price_on_or_before_rebalance")
    elif config["universe"].get("strict_rebalance_price_filter", False):
        if not rebalance_price_available:
            reasons.append("no_price_on_rebalance_date")
        elif rebalance_price.tradable_flag is False:
            reasons.append("price_not_tradable_on_rebalance_date")

    has_fundamentals = fundamentals_by_code.get(code, False)
    if config["universe"].get("require_fundamentals", True) and not has_fundamentals:
        reasons.append("missing_point_in_time_fundamentals")

    output_row = {
        "rebalance_date": rebalance_date,
        "code": code,
        "name": row.get("name", ""),
        "market": row.get("market", ""),
        "sector": row.get("sector", ""),
        "source_date": row.get("source_date", ""),
        "listing_lifecycle_status": row.get("listing_lifecycle_status", ""),
        "listed_date": row.get("listed_date", ""),
        "delisted_date": row.get("delisted_date", ""),
        "security_type": row.get("security_type", ""),
        "lot_size": parse_int(row.get("lot_size"), default=100),
        "ipo_age_trading_days": len(all_price_points),
        "median_60d_trading_value": median_trading_value,
        "latest_price_date": latest.date if latest else None,
        "latest_unadjusted_close": latest.unadjusted_close if latest else None,
        "rebalance_price_available": rebalance_price_available,
        "latest_price_stale": latest_price_stale,
        "price_staleness_trading_days": price_staleness_trading_days,
        "has_fundamentals": has_fundamentals,
        "tradable_flag": (listing_tradable is not False) and (rebalance_price.tradable_flag if rebalance_price else False),
        "price_limit_flag": latest.price_limit_flag if latest else False,
    }
    return output_row, reasons


def build_universe(
    *,
    config: dict[str, Any],
    rebalance_date: Any,
    listings_path: Path,
    prices_path: Path,
    fundamentals_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return build_universe_from_rows(
        config=config,
        rebalance_date=rebalance_date,
        listing_rows=read_csv(listings_path),
        price_rows=read_csv(prices_path),
        fundamental_rows=read_csv(fundamentals_path),
    )


def build_universe_from_rows(
    *,
    config: dict[str, Any],
    rebalance_date: Any,
    listing_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prices_by_code = group_prices(price_rows)
    market_calendar = sorted({point.date for points in prices_by_code.values() for point in points if point.date <= rebalance_date})
    fundamentals_by_code = group_fundamentals(fundamental_rows, rebalance_date)
    listing_rows = listings_as_of_snapshot(listing_rows, rebalance_date)
    universe_rows: list[dict[str, Any]] = []
    exclusion_rows: list[dict[str, Any]] = []
    for row in listing_rows:
        output_row, reasons = evaluate_row(
            row,
            config=config,
            rebalance_date=rebalance_date,
            prices_by_code=prices_by_code,
            market_calendar=market_calendar,
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
    return universe_rows, exclusion_rows


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")

    universe_rows, exclusion_rows = build_universe(
        config=config,
        rebalance_date=rebalance_date,
        listings_path=args.listings,
        prices_path=args.prices,
        fundamentals_path=args.fundamentals,
    )

    suffix = month_key(rebalance_date)
    universe_path = args.out_dir / f"universe_{suffix}.csv"
    exclusions_path = args.out_dir / f"excluded_{suffix}.csv"

    universe_fields = [
        "rebalance_date",
        "code",
        "name",
        "market",
        "sector",
        "source_date",
        "listing_lifecycle_status",
        "listed_date",
        "delisted_date",
        "security_type",
        "lot_size",
        "ipo_age_trading_days",
        "median_60d_trading_value",
        "latest_price_date",
        "latest_unadjusted_close",
        "rebalance_price_available",
        "latest_price_stale",
        "price_staleness_trading_days",
        "has_fundamentals",
        "tradable_flag",
        "price_limit_flag",
    ]
    exclusion_fields = ["rebalance_date", "code", "name", "reason", "detail"]

    write_csv(universe_path, universe_rows, universe_fields)
    write_csv(exclusions_path, exclusion_rows, exclusion_fields)

    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_universe",
            file_path=universe_path,
            vendor="local",
            schema_version="universe_v0_1",
            date_range=args.rebalance_date,
            notes=f"{len(universe_rows)} included; {len(exclusion_rows)} excluded",
        )
        append_manifest(
            args.manifest,
            source="derived_universe_exclusions",
            file_path=exclusions_path,
            vendor="local",
            schema_version="universe_exclusions_v0_1",
            date_range=args.rebalance_date,
            notes=f"{len(exclusion_rows)} excluded",
        )

    print(f"Wrote {len(universe_rows)} included rows to {universe_path}")
    print(f"Wrote {len(exclusion_rows)} excluded rows to {exclusions_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
