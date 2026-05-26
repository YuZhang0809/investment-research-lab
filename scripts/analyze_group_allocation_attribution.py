from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from group_beta_common import fmt, normalize_text, parse_optional_date
from research_common import append_manifest, date_range_from_rows, parse_float, read_table, write_table


FIELDNAMES = [
    "date",
    "allocation_date",
    "group_type",
    "group_id",
    "group_name",
    "target_weight",
    "benchmark_weight",
    "active_weight",
    "group_return",
    "portfolio_contribution",
    "benchmark_contribution",
    "active_contribution",
    "missing_flags",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze basic group-allocation return attribution.")
    parser.add_argument("--group-allocation", required=True, type=Path)
    parser.add_argument("--basket-returns", required=True, type=Path)
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-label", default="group_allocation_attribution")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def parse_row_date(row: dict[str, Any], field_name: str) -> date | None:
    return parse_optional_date(row.get("rebalance_date") or row.get("date"), field_name)


def group_key(row: dict[str, Any]) -> tuple[str, str]:
    return normalize_text(row.get("group_type")), normalize_text(row.get("group_id"))


def load_allocation_rows(path: Path, input_format: str) -> dict[date, dict[tuple[str, str], dict[str, Any]]]:
    grouped: dict[date, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        row_date = parse_row_date(row, "group_allocation.rebalance_date")
        key = group_key(row)
        if row_date is None or not key[0] or not key[1]:
            continue
        if key in grouped[row_date]:
            raise ValueError(f"Duplicate group allocation row for date={row_date};group_type={key[0]};group_id={key[1]}.")
        grouped[row_date][key] = dict(row)
    if not grouped:
        raise ValueError("Group allocation panel has no valid rows.")
    return dict(grouped)


def load_basket_rows(path: Path, input_format: str) -> dict[date, dict[tuple[str, str], dict[str, Any]]]:
    grouped: dict[date, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        row_date = parse_row_date(row, "basket_returns.date")
        key = group_key(row)
        if row_date is None or not key[0] or not key[1]:
            continue
        if key in grouped[row_date]:
            raise ValueError(f"Duplicate group basket row for date={row_date};group_type={key[0]};group_id={key[1]}.")
        grouped[row_date][key] = dict(row)
    if not grouped:
        raise ValueError("Group basket-return panel has no valid rows.")
    return dict(grouped)


def latest_allocation_date(allocation_dates: list[date], return_date: date) -> date | None:
    eligible = [value for value in allocation_dates if value < return_date]
    return max(eligible) if eligible else None


def build_panel(
    allocation_rows_by_date: dict[date, dict[tuple[str, str], dict[str, Any]]],
    basket_rows_by_date: dict[date, dict[tuple[str, str], dict[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    allocation_dates = sorted(allocation_rows_by_date)
    for return_date in sorted(basket_rows_by_date):
        allocation_date = latest_allocation_date(allocation_dates, return_date)
        if allocation_date is None:
            continue
        allocations = allocation_rows_by_date[allocation_date]
        baskets = basket_rows_by_date[return_date]
        for key in sorted(set(allocations) | set(baskets)):
            allocation_row = allocations.get(key, {})
            basket_row = baskets.get(key, {})
            group_return = parse_float(basket_row.get("basket_return"))
            target_weight = parse_float(allocation_row.get("target_weight")) or 0.0
            benchmark_weight = parse_float(allocation_row.get("benchmark_weight")) or 0.0
            active_weight = parse_float(allocation_row.get("active_weight"))
            if active_weight is None:
                active_weight = target_weight - benchmark_weight
            missing_flags: list[str] = []
            if group_return is None:
                missing_flags.append("basket_return")
            row = {
                "date": return_date,
                "allocation_date": allocation_date,
                "group_type": key[0],
                "group_id": key[1],
                "group_name": normalize_text(allocation_row.get("group_name") or basket_row.get("group_name")),
                "target_weight": target_weight,
                "benchmark_weight": benchmark_weight,
                "active_weight": active_weight,
                "group_return": group_return,
                "portfolio_contribution": target_weight * group_return if group_return is not None else None,
                "benchmark_contribution": benchmark_weight * group_return if group_return is not None else None,
                "active_contribution": active_weight * group_return if group_return is not None else None,
                "missing_flags": ";".join(missing_flags),
            }
            output.append(row)
    return output


def main() -> int:
    args = build_parser().parse_args()
    rows = build_panel(
        load_allocation_rows(args.group_allocation, args.input_format),
        load_basket_rows(args.basket_returns, args.input_format),
    )
    serializable = [{key: fmt(value) for key, value in row.items()} for row in rows]
    write_table(serializable, args.out, format=args.output_format, fieldnames=FIELDNAMES)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_group_allocation_attribution",
            file_path=args.out,
            vendor="internal",
            schema_version="group_allocation_attribution_v0_1",
            date_range=date_range_from_rows(serializable, "date"),
            notes=f"run_label={args.run_label};rows={len(rows)}",
        )
    print(f"Wrote {len(rows)} group allocation attribution rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
