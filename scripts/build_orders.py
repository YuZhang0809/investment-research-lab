from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_common import append_manifest, load_yaml, month_key, parse_date, parse_float, parse_int, read_csv, write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply execution constraints to target holdings.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/execution"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def tick_size(price: float) -> float:
    if price < 1000:
        return 0.1
    if price < 3000:
        return 0.5
    if price < 30000:
        return 1.0
    return 5.0


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")
    max_order_to_adv = float(config["execution"].get("max_order_to_median_trading_value", 0.005))
    rows = read_csv(args.targets)
    order_rows: list[dict[str, Any]] = []

    for row in rows:
        price = parse_float(row.get("latest_unadjusted_close"), default=0.0) or 0.0
        lot = parse_int(row.get("lot_size"), default=100) or 100
        target_shares = parse_int(row.get("target_shares"), default=0) or 0
        current_shares = 0
        desired_shares = target_shares - current_shares
        median_adv = parse_float(row.get("median_60d_trading_value"), default=0.0) or 0.0
        adv_cap_value = median_adv * max_order_to_adv
        desired_value = abs(desired_shares * price)
        adjusted_shares = desired_shares
        reason = row.get("target_constraint_reason", "")
        if target_shares == 0:
            adjusted_shares = 0
            reason = reason or "zero_target_shares"
        elif desired_value > adv_cap_value and adv_cap_value > 0:
            adjusted_shares = int(adv_cap_value // (price * lot)) * lot
            reason = "reduced_by_adv_cap"
        if adjusted_shares == 0 and desired_shares != 0:
            reason = reason or "below_lot_or_adv_cap"

        order_value = adjusted_shares * price
        spread_cost = abs(order_value) * (tick_size(price) / price if price else 0)
        impact_cost = abs(order_value) * min(abs(order_value) / median_adv, 0.02) if median_adv else 0
        estimated_cost_base = spread_cost + impact_cost
        order_rows.append(
            {
                "rebalance_date": args.rebalance_date,
                "code": row.get("code", ""),
                "name": row.get("name", ""),
                "side": "BUY" if adjusted_shares > 0 else "SKIP",
                "target_shares": target_shares,
                "current_shares": current_shares,
                "desired_shares": desired_shares,
                "order_shares": adjusted_shares,
                "intended_price": price,
                "order_value": order_value,
                "median_60d_trading_value": median_adv,
                "adv_cap_value": adv_cap_value,
                "estimated_spread_cost": spread_cost,
                "estimated_impact_cost": impact_cost,
                "estimated_cost_base": estimated_cost_base,
                "constraint_reason": reason,
            }
        )

    output_path = args.out_dir / f"orders_{month_key(rebalance_date)}.csv"
    fieldnames = [
        "rebalance_date",
        "code",
        "name",
        "side",
        "target_shares",
        "current_shares",
        "desired_shares",
        "order_shares",
        "intended_price",
        "order_value",
        "median_60d_trading_value",
        "adv_cap_value",
        "estimated_spread_cost",
        "estimated_impact_cost",
        "estimated_cost_base",
        "constraint_reason",
    ]
    write_csv(output_path, order_rows, fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_orders",
            file_path=output_path,
            vendor="local",
            schema_version="orders_v0_1",
            date_range=args.rebalance_date,
            notes=f"{len(order_rows)} rows",
        )
    print(f"Wrote {len(order_rows)} order rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
