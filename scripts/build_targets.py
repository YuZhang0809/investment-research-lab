from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from research_common import (
    append_manifest,
    load_yaml,
    month_key,
    parse_bool,
    parse_date,
    parse_float,
    parse_int,
    read_csv,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build research and executable target holdings.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--capital-jpy", type=float, default=5_000_000)
    parser.add_argument("--target-count", type=int)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/portfolio"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def floor_lot(value: float, price: float, lot: int) -> int:
    if price <= 0 or lot <= 0:
        return 0
    return int(value // (price * lot)) * lot


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")
    scores = [row for row in read_csv(args.scores) if parse_float(row.get("qvm_score")) is not None]
    universe_by_code = {row["code"]: row for row in read_csv(args.universe)}
    ranked = sorted(scores, key=lambda row: parse_int(row.get("rank"), default=999999) or 999999)

    if args.target_count:
        target_count = args.target_count
    else:
        min_holdings = int(config["portfolio"]["research_portfolio"].get("target_holdings_min", 20))
        max_holdings = int(config["portfolio"]["research_portfolio"].get("target_holdings_max", 50))
        target_count = min(max_holdings, max(min_holdings, len(ranked)))
    selected = ranked[: min(target_count, len(ranked))]
    research_weight = 1 / len(selected) if selected else 0
    target_value = args.capital_jpy * research_weight if selected else 0

    target_rows: list[dict[str, Any]] = []
    total_executable_value = 0.0
    for row in selected:
        universe = universe_by_code.get(row["code"], {})
        price = parse_float(row.get("latest_unadjusted_close")) or parse_float(universe.get("latest_unadjusted_close")) or 0.0
        lot = parse_int(universe.get("lot_size"), default=100) or 100
        rebalance_price_available = parse_bool(
            universe.get("rebalance_price_available"),
            default=True,
        )
        if rebalance_price_available is False:
            target_shares = 0
            target_constraint_reason = "no_rebalance_price"
        else:
            target_shares = floor_lot(target_value, price, lot)
            target_constraint_reason = "below_lot_size" if target_shares == 0 and target_value > 0 else ""
        executable_value = target_shares * price
        total_executable_value += executable_value
        target_rows.append(
            {
                "rebalance_date": args.rebalance_date,
                "rank": row.get("rank", ""),
                "code": row.get("code", ""),
                "name": row.get("name", ""),
                "sector": row.get("sector", ""),
                "qvm_score": row.get("qvm_score", ""),
                "research_weight": research_weight,
                "research_target_value": target_value,
                "latest_unadjusted_close": price,
                "lot_size": lot,
                "target_shares": target_shares,
                "target_value": executable_value,
                "cash_drag_from_lot": target_value - executable_value,
                "is_executable_target": target_shares > 0,
                "target_constraint_reason": target_constraint_reason,
                "median_60d_trading_value": universe.get("median_60d_trading_value", ""),
            }
        )
    cash = args.capital_jpy - total_executable_value
    for row in target_rows:
        row["executable_weight"] = parse_float(row["target_value"], default=0.0) / args.capital_jpy
        row["portfolio_cash_after_targets"] = cash

    output_path = args.out_dir / f"targets_{month_key(rebalance_date)}.csv"
    fieldnames = [
        "rebalance_date",
        "rank",
        "code",
        "name",
        "sector",
        "qvm_score",
        "research_weight",
        "research_target_value",
        "latest_unadjusted_close",
        "lot_size",
        "target_shares",
        "target_value",
        "cash_drag_from_lot",
        "is_executable_target",
        "target_constraint_reason",
        "executable_weight",
        "portfolio_cash_after_targets",
        "median_60d_trading_value",
    ]
    write_csv(output_path, target_rows, fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_targets",
            file_path=output_path,
            vendor="local",
            schema_version="targets_v0_1",
            date_range=args.rebalance_date,
            notes=f"{len(target_rows)} rows; capital_jpy={args.capital_jpy}",
        )
    print(f"Wrote {len(target_rows)} target rows to {output_path}")
    print(f"Executable target value: {total_executable_value:.0f}; cash after targets: {cash:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
