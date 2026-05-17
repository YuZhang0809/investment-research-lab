from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class CandidateReviewTest(unittest.TestCase):
    def test_generates_selected_executable_review_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            scores = temp / "scores.csv"
            factors = temp / "factors.csv"
            targets = temp / "targets.csv"
            orders = temp / "orders.csv"
            out_dir = temp / "out"

            write_csv(
                scores,
                [
                    {
                        "rebalance_date": "2026-05-15",
                        "rank": "1",
                        "code": "1001",
                        "name": "Sample",
                        "sector": "Tech",
                        "latest_unadjusted_close": "1000",
                        "quality_score": "1",
                        "value_score": "0.5",
                        "momentum_score": "0.25",
                        "qvm_score": "0.75",
                        "missing_score_components": "",
                    }
                ],
                [
                    "rebalance_date",
                    "rank",
                    "code",
                    "name",
                    "sector",
                    "latest_unadjusted_close",
                    "quality_score",
                    "value_score",
                    "momentum_score",
                    "qvm_score",
                    "missing_score_components",
                ],
            )
            write_csv(
                factors,
                [
                    {
                        "rebalance_date": "2026-05-15",
                        "code": "1001",
                        "missing_flags": "",
                        "operating_profit_to_total_assets": "0.1",
                        "equity_to_assets": "0.5",
                        "earnings_yield": "0.08",
                        "book_to_market": "0.7",
                        "return_12_1": "0.2",
                        "return_6_1": "0.1",
                    }
                ],
                [
                    "rebalance_date",
                    "code",
                    "missing_flags",
                    "operating_profit_to_total_assets",
                    "equity_to_assets",
                    "earnings_yield",
                    "book_to_market",
                    "return_12_1",
                    "return_6_1",
                ],
            )
            write_csv(
                targets,
                [
                    {
                        "rebalance_date": "2026-05-15",
                        "rank": "1",
                        "code": "1001",
                        "name": "Sample",
                        "sector": "Tech",
                        "research_weight": "1",
                        "research_target_value": "1000000",
                        "target_shares": "1000",
                        "target_value": "1000000",
                        "cash_drag_from_lot": "0",
                        "target_constraint_reason": "",
                        "median_60d_trading_value": "10000000",
                    }
                ],
                [
                    "rebalance_date",
                    "rank",
                    "code",
                    "name",
                    "sector",
                    "research_weight",
                    "research_target_value",
                    "target_shares",
                    "target_value",
                    "cash_drag_from_lot",
                    "target_constraint_reason",
                    "median_60d_trading_value",
                ],
            )
            write_csv(
                orders,
                [
                    {
                        "rebalance_date": "2026-05-15",
                        "code": "1001",
                        "name": "Sample",
                        "side": "BUY",
                        "order_shares": "1000",
                        "order_value": "1000000",
                        "estimated_cost_base": "100",
                        "constraint_reason": "",
                    }
                ],
                [
                    "rebalance_date",
                    "code",
                    "name",
                    "side",
                    "order_shares",
                    "order_value",
                    "estimated_cost_base",
                    "constraint_reason",
                ],
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_candidate_review.py"),
                    "--rebalance-date",
                    "2026-05-15",
                    "--scores",
                    str(scores),
                    "--factors",
                    str(factors),
                    "--targets",
                    str(targets),
                    "--orders",
                    str(orders),
                    "--out-dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
            )

            with (out_dir / "candidate_review_202605.csv").open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(1, len(rows))
            self.assertEqual("selected_executable", rows[0]["review_status"])
            self.assertEqual("true", rows[0]["executable_flag"])


if __name__ == "__main__":
    unittest.main()
