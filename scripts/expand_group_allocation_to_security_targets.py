from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from build_group_basket_return_panel import WEIGHTING_MODES, group_weights
from group_beta_common import (
    build_price_index,
    fmt,
    load_dates,
    load_group_membership_panel,
    memberships_for_date,
    normalize_text,
    parse_optional_date,
)
from research_common import append_manifest, date_range_from_rows, parse_float, read_table, write_table


FIELDNAMES = [
    "rebalance_date",
    "code",
    "target_weight",
    "source_group_count",
    "source_groups",
    "lookthrough_constraint_status",
    "lookthrough_constraint_reasons",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expand generic group allocations to security-level look-through targets.")
    parser.add_argument("--group-allocation", required=True, type=Path)
    parser.add_argument("--membership-panel", required=True, type=Path)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--rebalance-dates", type=Path)
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values")
    parser.add_argument("--membership-date-field", default="auto")
    parser.add_argument("--membership-duplicate-policy", choices=["fail", "aggregate"], default="fail")
    parser.add_argument("--weighting-mode", choices=sorted(WEIGHTING_MODES), default="equal_weight")
    parser.add_argument("--custom-weight-field")
    parser.add_argument("--single-name-cap", type=float)
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-label", default="group_lookthrough_targets")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def allocation_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(row.get("rebalance_date") or row.get("date"), "group_allocation.rebalance_date")


def group_key(row: dict[str, Any]) -> tuple[str, str]:
    return normalize_text(row.get("group_type")), normalize_text(row.get("group_id"))


def load_allocation_rows(path: Path, input_format: str) -> dict[date, dict[tuple[str, str], dict[str, Any]]]:
    grouped: dict[date, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        row_date = allocation_date(row)
        key = group_key(row)
        if row_date is None or not key[0] or not key[1]:
            continue
        if key in grouped[row_date]:
            raise ValueError(f"Duplicate group allocation row for date={row_date};group_type={key[0]};group_id={key[1]}.")
        grouped[row_date][key] = dict(row)
    if not grouped:
        raise ValueError("Group allocation panel has no valid rows.")
    return dict(grouped)


def load_prices(path: Path | None, input_format: str, weighting_mode: str) -> dict[str, list[Any]]:
    if weighting_mode == "equal_weight" and path is None:
        return {}
    if path is None:
        raise ValueError("--prices is required for non-equal group look-through weighting modes.")
    return build_price_index(read_table(path, format=input_format).to_dict(orient="records"))


def build_panel(
    allocation_rows_by_date: dict[date, dict[tuple[str, str], dict[str, Any]]],
    membership_panel_path: Path,
    *,
    price_index: dict[str, list[Any]] | None = None,
    rebalance_dates: list[date] | None = None,
    input_format: str = "auto",
    membership_date_field: str = "auto",
    membership_duplicate_policy: str = "fail",
    weighting_mode: str = "equal_weight",
    custom_weight_field: str | None = None,
    single_name_cap: float | None = None,
) -> list[dict[str, Any]]:
    if weighting_mode not in WEIGHTING_MODES:
        raise ValueError(f"Unsupported weighting mode: {weighting_mode}")
    if single_name_cap is not None and single_name_cap < 0:
        raise ValueError("--single-name-cap must be non-negative.")
    membership_panel = load_group_membership_panel(
        membership_panel_path,
        input_format=input_format,
        date_field=membership_date_field,
        duplicate_policy=membership_duplicate_policy,
    )
    price_index = price_index or {}
    output: list[dict[str, Any]] = []
    for rebalance_date in sorted(rebalance_dates or allocation_rows_by_date.keys()):
        allocations = allocation_rows_by_date.get(rebalance_date, {})
        memberships = memberships_for_date(membership_panel, rebalance_date)
        security_weights_by_group, _names = group_weights(
            memberships,
            price_index,
            rebalance_date,
            mode=weighting_mode,
            custom_weight_field=custom_weight_field,
        )
        target_by_code: dict[str, float] = defaultdict(float)
        source_by_code: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for key, allocation_row in sorted(allocations.items()):
            group_target = parse_float(allocation_row.get("target_weight")) or 0.0
            if group_target <= 0:
                continue
            for code, group_security_weight in security_weights_by_group.get(key, {}).items():
                contribution = group_target * group_security_weight
                if contribution <= 0:
                    continue
                target_by_code[code] += contribution
                source_by_code[code].append((key[0], key[1], contribution))
        for code in sorted(target_by_code):
            target = target_by_code[code]
            reasons: list[str] = []
            if single_name_cap is not None and target > single_name_cap:
                target = single_name_cap
                reasons.append("single_name_cap")
            sources = [
                f"{group_type}:{group_id}={fmt(weight)}"
                for group_type, group_id, weight in sorted(source_by_code.get(code, []))
            ]
            output.append(
                {
                    "rebalance_date": rebalance_date,
                    "code": code,
                    "target_weight": target,
                    "source_group_count": len(source_by_code.get(code, [])),
                    "source_groups": ";".join(sources),
                    "lookthrough_constraint_status": "clipped" if reasons else "ok",
                    "lookthrough_constraint_reasons": ";".join(reasons),
                }
            )
    return output


def main() -> int:
    args = build_parser().parse_args()
    rows = build_panel(
        load_allocation_rows(args.group_allocation, args.input_format),
        args.membership_panel,
        price_index=load_prices(args.prices, args.input_format, args.weighting_mode),
        rebalance_dates=load_dates(args.rebalance_dates, args.rebalance_date_values, field_name="rebalance_date"),
        input_format=args.input_format,
        membership_date_field=args.membership_date_field,
        membership_duplicate_policy=args.membership_duplicate_policy,
        weighting_mode=args.weighting_mode,
        custom_weight_field=args.custom_weight_field,
        single_name_cap=args.single_name_cap,
    )
    serializable = [{key: fmt(value) for key, value in row.items()} for row in rows]
    write_table(serializable, args.out, format=args.output_format, fieldnames=FIELDNAMES)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_group_lookthrough_targets",
            file_path=args.out,
            vendor="internal",
            schema_version="group_lookthrough_targets_v0_1",
            date_range=date_range_from_rows(serializable, "rebalance_date"),
            notes=f"run_label={args.run_label};rows={len(rows)};weighting_mode={args.weighting_mode}",
        )
    print(f"Wrote {len(rows)} group look-through target rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
