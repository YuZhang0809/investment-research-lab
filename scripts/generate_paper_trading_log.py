from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from research_common import read_csv, write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a paper-trading log template from simulated trades.")
    parser.add_argument("--trades", required=True, type=Path)
    parser.add_argument("--failures", required=True, type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("experiments/qvm_v0_1/paper_trading_log_template.csv"),
    )
    parser.add_argument(
        "--runbook",
        type=Path,
        default=Path("reports/paper_trading/qvm_paper_trading_runbook.md"),
    )
    return parser


def latest_signal_date(rows: list[dict[str, str]]) -> str:
    dates = sorted({row.get("signal_date", "") for row in rows if row.get("signal_date")})
    return dates[-1] if dates else ""


def write_runbook(path: Path, latest_date: str, trade_count: int, failure_counts: Counter[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# QVM Paper-Trading Runbook",
        "",
        f"- source rebalance date: {latest_date or 'none'}",
        f"- template orders: {trade_count}",
        "- mode: manual observation only; no auto-trading",
        "",
        "## Before Placing A Paper Order",
        "",
        "1. Confirm the signal date and execution date are not stale.",
        "2. Check current bid/ask and whether the issue is halted or price-limit affected.",
        "3. Confirm the order remains inside the configured ADV cap.",
        "4. Record intended price before observing the fill.",
        "5. Record actual paper fill, unfilled reason, and slippage versus model.",
        "",
        "## Failure Counts From Source Run",
        "",
        "| failure_type | count |",
        "|---|---:|",
    ]
    if failure_counts:
        for name, count in failure_counts.most_common():
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Gate",
            "",
            "At least one full rebalance cycle must be logged before any live-trading discussion. The log should show fill rate, real spread, unfilled reasons, and whether model slippage is understated.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    trades = read_csv(args.trades)
    failures = read_csv(args.failures)
    latest_date = latest_signal_date(trades)
    latest_trades = [row for row in trades if row.get("signal_date") == latest_date and row.get("side") in {"BUY", "SELL"}]
    rows = []
    for row in latest_trades:
        rows.append(
            {
                "signal_date": row.get("signal_date", ""),
                "execution_date": row.get("execution_date", ""),
                "code": row.get("code", ""),
                "side": row.get("side", ""),
                "requested_shares": row.get("requested_shares", ""),
                "simulated_filled_shares": row.get("filled_shares", ""),
                "intended_price": row.get("price", ""),
                "simulated_value": row.get("value", ""),
                "broker_order_type": "",
                "paper_filled_price": "",
                "paper_filled_shares": "",
                "paper_commission": "",
                "slippage_vs_model": "",
                "unfilled_reason": row.get("constraint_reason", ""),
                "notes": "",
            }
        )
    write_csv(
        args.out,
        rows,
        [
            "signal_date",
            "execution_date",
            "code",
            "side",
            "requested_shares",
            "simulated_filled_shares",
            "intended_price",
            "simulated_value",
            "broker_order_type",
            "paper_filled_price",
            "paper_filled_shares",
            "paper_commission",
            "slippage_vs_model",
            "unfilled_reason",
            "notes",
        ],
    )
    write_runbook(args.runbook, latest_date, len(rows), Counter(row.get("failure_type", "") for row in failures if row.get("failure_type")))
    print(f"Wrote paper-trading log template to {args.out}")
    print(f"Wrote paper-trading runbook to {args.runbook}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
