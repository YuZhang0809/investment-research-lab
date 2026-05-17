from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_qvm_walkforward  # noqa: E402
from run_qvm_walkforward import select_codes  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class P2CorrectnessTest(unittest.TestCase):
    def test_buy_rule_limits_new_buys_instead_of_always_filling_target_count(self) -> None:
        scores = [{"code": f"100{index}", "rank": str(index)} for index in range(1, 6)]
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 3, "target_holdings_max": 3},
                "buy_rule": {"rank_top_pct": 0, "rank_top_n": 1},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
            }
        }

        selected, research = select_codes(scores, holdings={}, config=config)

        self.assertEqual(["1001"], selected)
        self.assertEqual(["1001", "1002", "1003"], research)

    def test_score_ties_rank_by_code_not_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            factors = temp / "factors.csv"
            out_dir = temp / "scores"
            write_csv(
                factors,
                [
                    factor_row("2002", 2),
                    factor_row("1001", 2),
                    factor_row("3003", 1),
                ],
                [
                    "rebalance_date",
                    "code",
                    "name",
                    "sector",
                    "latest_unadjusted_close",
                    "operating_profit_to_total_assets",
                    "equity_to_assets",
                    "earnings_yield",
                    "book_to_market",
                    "return_12_1",
                    "return_6_1",
                ],
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_scores.py"),
                    "--config",
                    str(ROOT / "configs" / "qvm_v0_1.example.yml"),
                    "--rebalance-date",
                    "2026-01-31",
                    "--factors",
                    str(factors),
                    "--out-dir",
                    str(out_dir),
                    "--no-manifest",
                ],
                cwd=ROOT,
                check=True,
            )

            with (out_dir / "scores_202601.csv").open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            rank_by_code = {row["code"]: row["rank"] for row in rows}
            self.assertEqual("1", rank_by_code["1001"])
            self.assertEqual("2", rank_by_code["2002"])

    def test_price_limit_fill_is_marked_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            write_csv(
                prices,
                [
                    {
                        "date": "2026-01-31",
                        "code": "1001",
                        "unadjusted_open": 100,
                        "unadjusted_close": 100,
                        "adjusted_close": 100,
                        "trading_value": 10_000_000,
                        "price_limit_flag": "true",
                    }
                ],
                [
                    "date",
                    "code",
                    "unadjusted_open",
                    "unadjusted_close",
                    "adjusted_close",
                    "trading_value",
                    "price_limit_flag",
                ],
            )
            write_csv(listings, [], ["code"])
            write_csv(fundamentals, [], ["code"])

            original_argv = sys.argv[:]
            original_run_stages = run_qvm_walkforward.run_stages

            def fake_run_stages(args, rebalance_date):
                suffix = rebalance_date.strftime("%Y%m%d")
                universe = temp / "stage" / f"universe_{suffix}.csv"
                factors = temp / "stage" / f"factors_{suffix}.csv"
                scores = temp / "stage" / f"scores_{suffix}.csv"
                write_csv(
                    universe,
                    [{"code": "1001", "lot_size": "100", "median_60d_trading_value": "10000000"}],
                    ["code", "lot_size", "median_60d_trading_value"],
                )
                write_csv(factors, [{"code": "1001"}], ["code"])
                write_csv(scores, [{"code": "1001", "rank": "1"}], ["code", "rank"])
                return universe, factors, scores

            try:
                run_qvm_walkforward.run_stages = fake_run_stages
                sys.argv = [
                    "run_qvm_walkforward.py",
                    "--config",
                    str(ROOT / "configs" / "qvm_v0_1.example.yml"),
                    "--listings",
                    str(listings),
                    "--prices",
                    str(prices),
                    "--fundamentals",
                    str(fundamentals),
                    "--start-date",
                    "2026-01-31",
                    "--end-date",
                    "2026-01-31",
                    "--frequency",
                    "monthly",
                    "--execution-price",
                    "rebalance_close",
                    "--capital-jpy",
                    "10100",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                    "--run-label",
                    "price_limit_test",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_failure_cases_price_limit_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                failure_rows = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_trades_price_limit_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                trade_rows = list(csv.DictReader(file))

            self.assertIn("price_limit_uncertain_fill", {row["failure_type"] for row in failure_rows})
            self.assertEqual("price_limit_uncertain_fill", trade_rows[0]["constraint_reason"])

    def test_compare_walkforward_runs_separates_drawdown_sampling_and_infers_capital(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            monthly = temp / "monthly.csv"
            quarterly = temp / "quarterly.csv"
            out = temp / "comparison.md"
            fields = [
                "rebalance_date",
                "frequency",
                "portfolio_equity_pre",
                "portfolio_equity_after_cost",
                "benchmark_equity",
                "research_equity",
                "cash_pct",
                "turnover",
                "holdings_count",
                "zero_lot_targets",
                "skipped_orders",
                "estimated_cost_base",
            ]
            write_csv(
                monthly,
                [
                    summary_row("2026-01-31", "monthly", 1000, 1000),
                    summary_row("2026-02-28", "monthly", 1000, 900),
                ],
                fields,
            )
            write_csv(
                quarterly,
                [
                    summary_row("2026-03-31", "quarterly", 2000, 2000),
                    summary_row("2026-06-30", "quarterly", 2000, 1900),
                ],
                fields,
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "compare_walkforward_runs.py"),
                    "--summary",
                    str(monthly),
                    "--summary",
                    str(quarterly),
                    "--label",
                    "monthly",
                    "--label",
                    "quarterly",
                    "--out",
                    str(out),
                    "--no-manifest",
                ],
                cwd=ROOT,
                check=True,
            )

            text = out.read_text(encoding="utf-8")
            first_table = text.split("## Drawdown Sampling", 1)[0]
            self.assertIn("JPY 1,000", first_table)
            self.assertIn("JPY 2,000", first_table)
            self.assertNotIn("max DD", first_table)
            self.assertIn("## Drawdown Sampling", text)
            self.assertIn("recorded max DD", text)


def factor_row(code: str, value: float) -> dict[str, object]:
    return {
        "rebalance_date": "2026-01-31",
        "code": code,
        "name": code,
        "sector": "Tech",
        "latest_unadjusted_close": "100",
        "operating_profit_to_total_assets": value,
        "equity_to_assets": value,
        "earnings_yield": value,
        "book_to_market": value,
        "return_12_1": value,
        "return_6_1": value,
    }


def summary_row(date_value: str, frequency: str, initial: float, equity: float) -> dict[str, object]:
    return {
        "rebalance_date": date_value,
        "frequency": frequency,
        "portfolio_equity_pre": initial,
        "portfolio_equity_after_cost": equity,
        "benchmark_equity": initial,
        "research_equity": initial,
        "cash_pct": 0,
        "turnover": 0,
        "holdings_count": 1,
        "zero_lot_targets": 0,
        "skipped_orders": 0,
        "estimated_cost_base": 0,
    }


if __name__ == "__main__":
    unittest.main()
