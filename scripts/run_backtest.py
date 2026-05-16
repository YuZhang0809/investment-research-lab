from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_common import append_manifest, month_key, parse_date, parse_float, parse_int, read_csv, write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a one-rebalance execution snapshot backtest.")
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--orders", required=True, type=Path)
    parser.add_argument("--capital-jpy", type=float, default=5_000_000)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/backtest"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")
    orders = read_csv(args.orders)
    trades: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    invested = 0.0
    total_cost = 0.0
    for order in orders:
        shares = parse_int(order.get("order_shares"), default=0) or 0
        price = parse_float(order.get("intended_price"), default=0.0) or 0.0
        cost = parse_float(order.get("estimated_cost_base"), default=0.0) or 0.0
        value = shares * price
        if shares > 0:
            trades.append(
                {
                    "date": args.rebalance_date,
                    "code": order.get("code", ""),
                    "name": order.get("name", ""),
                    "side": "BUY",
                    "shares": shares,
                    "price": price,
                    "value": value,
                    "estimated_cost_base": cost,
                }
            )
            holdings.append(
                {
                    "date": args.rebalance_date,
                    "code": order.get("code", ""),
                    "name": order.get("name", ""),
                    "shares": shares,
                    "price": price,
                    "value": value,
                }
            )
            invested += value
            total_cost += cost
    cash = args.capital_jpy - invested - total_cost
    ending_value = invested + cash
    summary = [
        {
            "rebalance_date": args.rebalance_date,
            "capital_jpy": args.capital_jpy,
            "invested_value": invested,
            "cash": cash,
            "estimated_cost_base": total_cost,
            "gross_return": 0.0,
            "after_cost_pre_tax_return": ending_value / args.capital_jpy - 1.0,
            "after_cost_after_tax_taxable_return": ending_value / args.capital_jpy - 1.0,
            "after_cost_after_tax_nisa_like_return": ending_value / args.capital_jpy - 1.0,
            "note": "single-rebalance execution snapshot; no forward return window yet",
        }
    ]
    suffix = month_key(rebalance_date)
    trades_path = args.out_dir / f"trades_{suffix}.csv"
    holdings_path = args.out_dir / f"holdings_{suffix}.csv"
    summary_path = args.out_dir / f"summary_{suffix}.csv"
    equity_path = args.out_dir / f"equity_curve_{suffix}.csv"
    write_csv(trades_path, trades, ["date", "code", "name", "side", "shares", "price", "value", "estimated_cost_base"])
    write_csv(holdings_path, holdings, ["date", "code", "name", "shares", "price", "value"])
    write_csv(
        summary_path,
        summary,
        [
            "rebalance_date",
            "capital_jpy",
            "invested_value",
            "cash",
            "estimated_cost_base",
            "gross_return",
            "after_cost_pre_tax_return",
            "after_cost_after_tax_taxable_return",
            "after_cost_after_tax_nisa_like_return",
            "note",
        ],
    )
    write_csv(
        equity_path,
        [
            {"date": args.rebalance_date, "equity": args.capital_jpy, "stage": "before_execution"},
            {"date": args.rebalance_date, "equity": ending_value, "stage": "after_execution_cost"},
        ],
        ["date", "equity", "stage"],
    )
    if not args.no_manifest:
        for source, path, schema, row_count in [
            ("derived_backtest_trades", trades_path, "backtest_trades_v0_1", len(trades)),
            ("derived_backtest_holdings", holdings_path, "backtest_holdings_v0_1", len(holdings)),
            ("derived_backtest_summary", summary_path, "backtest_summary_v0_1", len(summary)),
            ("derived_backtest_equity_curve", equity_path, "backtest_equity_curve_v0_1", 2),
        ]:
            append_manifest(
                args.manifest,
                source=source,
                file_path=path,
                vendor="local",
                schema_version=schema,
                date_range=args.rebalance_date,
                notes=f"{row_count} rows",
            )
    print(f"Wrote backtest snapshot to {args.out_dir}")
    print(f"Invested={invested:.0f}; cash={cash:.0f}; estimated_cost_base={total_cost:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
