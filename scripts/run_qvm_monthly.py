from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local CSV QVM pipeline for one rebalance date.")
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--capital-jpy", type=float, default=5_000_000)
    return parser


def run(command: list[str]) -> None:
    print(" ".join(command))
    subprocess.run(command, check=True)


def month_suffix(rebalance_date: str) -> str:
    return rebalance_date[:7].replace("-", "")


def main() -> int:
    args = build_parser().parse_args()
    suffix = month_suffix(args.rebalance_date)
    py = sys.executable
    run(
        [
            py,
            "scripts/build_factors.py",
            "--rebalance-date",
            args.rebalance_date,
            "--universe",
            str(args.universe),
            "--prices",
            str(args.prices),
            "--fundamentals",
            str(args.fundamentals),
        ]
    )
    factors = f"data/processed/factors/factors_{suffix}.csv"
    run([py, "scripts/build_scores.py", "--rebalance-date", args.rebalance_date, "--factors", factors])
    scores = f"data/processed/scores/scores_{suffix}.csv"
    run(
        [
            py,
            "scripts/build_targets.py",
            "--rebalance-date",
            args.rebalance_date,
            "--scores",
            scores,
            "--universe",
            str(args.universe),
            "--capital-jpy",
            str(args.capital_jpy),
        ]
    )
    targets = f"data/processed/portfolio/targets_{suffix}.csv"
    run([py, "scripts/build_orders.py", "--rebalance-date", args.rebalance_date, "--targets", targets])
    orders = f"data/processed/execution/orders_{suffix}.csv"
    run(
        [
            py,
            "scripts/run_backtest.py",
            "--rebalance-date",
            args.rebalance_date,
            "--orders",
            orders,
            "--capital-jpy",
            str(args.capital_jpy),
        ]
    )
    run(
        [
            py,
            "scripts/generate_qvm_report.py",
            "--rebalance-date",
            args.rebalance_date,
            "--factors",
            factors,
            "--scores",
            scores,
            "--targets",
            targets,
            "--orders",
            orders,
            "--backtest-summary",
            f"data/processed/backtest/summary_{suffix}.csv",
        ]
    )
    print("QVM monthly CSV pipeline complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
