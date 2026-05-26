from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from group_beta_common import (
    Membership,
    PricePoint,
    build_price_index,
    fmt,
    latest_price_point,
    load_dates,
    load_group_membership_panel,
    memberships_for_date,
)
from research_common import append_manifest, date_range_from_rows, parse_float, read_table, write_table


WEIGHTING_MODES = {"equal_weight", "liquidity_weight", "market_cap_weight", "custom_weight"}
FIELDNAMES = [
    "date",
    "group_type",
    "group_id",
    "group_name",
    "constituent_count",
    "weighting_mode",
    "basket_return",
    "basket_value",
    "turnover",
    "coverage",
    "missing_return_count",
    "top_constituent_weight",
    "weight_concentration",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build generic group basket return panels.")
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--membership-panel", required=True, type=Path)
    parser.add_argument("--dates", type=Path, help="CSV/Parquet with date or rebalance_date column.")
    parser.add_argument("--date", action="append", dest="date_values", help="YYYY-MM-DD; can be repeated.")
    parser.add_argument("--membership-date-field", default="auto")
    parser.add_argument("--membership-duplicate-policy", choices=["fail", "aggregate"], default="fail")
    parser.add_argument("--weighting-mode", choices=sorted(WEIGHTING_MODES), default="equal_weight")
    parser.add_argument("--custom-weight-field", help="Membership-panel field used when --weighting-mode custom_weight.")
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-label", default="group_basket_returns")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    total = sum(value for value in raw.values() if value > 0)
    if total <= 0:
        return {}
    return {code: value / total for code, value in raw.items() if value > 0}


def raw_weight(
    membership: Membership,
    point: PricePoint | None,
    *,
    mode: str,
    custom_weight_field: str | None,
) -> float:
    base = membership.membership_weight
    if mode == "equal_weight":
        return base
    if mode == "liquidity_weight":
        if point is None:
            return 0.0
        value = point.trading_value if point.trading_value is not None else point.volume
        return base * value if value is not None and value > 0 else 0.0
    if mode == "market_cap_weight":
        return base * point.market_cap if point is not None and point.market_cap is not None and point.market_cap > 0 else 0.0
    if mode == "custom_weight":
        if not custom_weight_field:
            raise ValueError("--custom-weight-field is required for custom_weight mode.")
        value = parse_float(membership.raw.get(custom_weight_field))
        return base * value if value is not None and value > 0 else 0.0
    raise ValueError(f"Unsupported weighting mode: {mode}")


def group_weights(
    memberships: list[Membership],
    price_index: dict[str, list[PricePoint]],
    weight_date: date,
    *,
    mode: str,
    custom_weight_field: str | None,
) -> tuple[dict[tuple[str, str], dict[str, float]], dict[tuple[str, str], str]]:
    raw_by_group: dict[tuple[str, str], dict[str, float]] = {}
    group_names: dict[tuple[str, str], str] = {}
    for membership in memberships:
        key = (membership.group_type, membership.group_id)
        point = latest_price_point(price_index.get(membership.code, []), weight_date)
        raw_by_group.setdefault(key, {})[membership.code] = raw_by_group.setdefault(key, {}).get(membership.code, 0.0) + raw_weight(
            membership,
            point,
            mode=mode,
            custom_weight_field=custom_weight_field,
        )
        group_names.setdefault(key, membership.group_name)
    return {key: normalize_weights(values) for key, values in raw_by_group.items()}, group_names


def basket_return_for_group(
    weights: dict[str, float],
    price_index: dict[str, list[PricePoint]],
    previous_date: date,
    current_date: date,
) -> tuple[float | None, float, int]:
    total_return = 0.0
    covered_weight = 0.0
    missing = 0
    for code, weight in weights.items():
        previous = latest_price_point(price_index.get(code, []), previous_date)
        current = latest_price_point(price_index.get(code, []), current_date)
        if (
            previous is None
            or current is None
            or previous.adjusted_close is None
            or current.adjusted_close is None
            or previous.adjusted_close <= 0
        ):
            missing += 1
            continue
        total_return += weight * (current.adjusted_close / previous.adjusted_close - 1.0)
        covered_weight += weight
    if covered_weight <= 0:
        return None, 0.0, missing
    return total_return / covered_weight, covered_weight, missing


def basket_value_for_group(weights: dict[str, float], price_index: dict[str, list[PricePoint]], value_date: date) -> float | None:
    total = 0.0
    covered = 0.0
    for code, weight in weights.items():
        point = latest_price_point(price_index.get(code, []), value_date)
        if point is None or point.adjusted_close is None:
            continue
        total += weight * point.adjusted_close
        covered += weight
    return total / covered if covered > 0 else None


def turnover(current: dict[str, float], previous: dict[str, float] | None) -> float:
    if previous is None:
        return 0.0
    codes = set(current) | set(previous)
    return 0.5 * sum(abs(current.get(code, 0.0) - previous.get(code, 0.0)) for code in codes)


def build_panel(
    price_rows: list[dict[str, Any]],
    membership_panel_path: Path,
    *,
    dates: list[date],
    input_format: str = "auto",
    membership_date_field: str = "auto",
    membership_duplicate_policy: str = "fail",
    weighting_mode: str = "equal_weight",
    custom_weight_field: str | None = None,
) -> list[dict[str, Any]]:
    if weighting_mode not in WEIGHTING_MODES:
        raise ValueError(f"Unsupported weighting mode: {weighting_mode}")
    membership_panel = load_group_membership_panel(
        membership_panel_path,
        input_format=input_format,
        date_field=membership_date_field,
        duplicate_policy=membership_duplicate_policy,
    )
    price_index = build_price_index(price_rows)
    if not dates:
        dates = sorted({point.date for points in price_index.values() for point in points})
    output: list[dict[str, Any]] = []
    previous_current_weights: dict[tuple[str, str], dict[str, float]] | None = None
    previous_date: date | None = None
    for current_date in sorted(dates):
        current_memberships = memberships_for_date(membership_panel, current_date)
        current_weights, current_names = group_weights(
            current_memberships,
            price_index,
            current_date,
            mode=weighting_mode,
            custom_weight_field=custom_weight_field,
        )
        return_weights: dict[tuple[str, str], dict[str, float]] = {}
        if previous_date is not None:
            return_memberships = memberships_for_date(membership_panel, previous_date)
            return_weights, _return_names = group_weights(
                return_memberships,
                price_index,
                previous_date,
                mode=weighting_mode,
                custom_weight_field=custom_weight_field,
            )
        group_keys = sorted(set(current_weights) | set(return_weights))
        for key in group_keys:
            weights = current_weights.get(key, {})
            return_value: float | None = None
            coverage = 0.0
            missing_count = len(return_weights.get(key, weights))
            if previous_date is not None:
                return_value, coverage, missing_count = basket_return_for_group(
                    return_weights.get(key, {}),
                    price_index,
                    previous_date,
                    current_date,
                )
            top_weight = max(weights.values(), default=0.0)
            concentration = sum(value * value for value in weights.values())
            output.append(
                {
                    "date": current_date,
                    "group_type": key[0],
                    "group_id": key[1],
                    "group_name": current_names.get(key, ""),
                    "constituent_count": len(weights),
                    "weighting_mode": weighting_mode,
                    "basket_return": return_value,
                    "basket_value": basket_value_for_group(weights, price_index, current_date),
                    "turnover": turnover(weights, None if previous_current_weights is None else previous_current_weights.get(key)),
                    "coverage": coverage,
                    "missing_return_count": missing_count,
                    "top_constituent_weight": top_weight,
                    "weight_concentration": concentration,
                }
            )
        previous_current_weights = current_weights
        previous_date = current_date
    return output


def main() -> int:
    args = build_parser().parse_args()
    dates = load_dates(args.dates, args.date_values)
    price_rows = read_table(args.prices, format=args.input_format).to_dict(orient="records")
    rows = build_panel(
        price_rows,
        args.membership_panel,
        dates=dates,
        input_format=args.input_format,
        membership_date_field=args.membership_date_field,
        membership_duplicate_policy=args.membership_duplicate_policy,
        weighting_mode=args.weighting_mode,
        custom_weight_field=args.custom_weight_field,
    )
    serializable = [{key: fmt(value) for key, value in row.items()} for row in rows]
    write_table(serializable, args.out, format=args.output_format, fieldnames=FIELDNAMES)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_group_basket_returns",
            file_path=args.out,
            vendor="internal",
            schema_version="group_basket_returns_v0_1",
            date_range=date_range_from_rows(serializable, "date"),
            notes=f"run_label={args.run_label};rows={len(rows)};weighting_mode={args.weighting_mode}",
        )
    print(f"Wrote {len(rows)} group basket return rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
