from __future__ import annotations

import csv
import unittest
from datetime import date
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_qvm_walkforward  # noqa: E402
from build_factors import adjusted_close as factor_adjusted_close  # noqa: E402
from build_factors import rows_until_rebalance  # noqa: E402
from run_qvm_walkforward import (  # noqa: E402
    PricePoint,
    actual_shares_from_adjusted,
    adjusted_shares_for_trade,
    build_price_index,
    consume_lots,
    position_value,
)


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class WalkForwardAdjustedValuationTest(unittest.TestCase):
    def test_split_does_not_mechanically_cut_position_value(self) -> None:
        pre_split = PricePoint(
            date=date(2026, 1, 31),
            unadjusted_open=100.0,
            unadjusted_close=100.0,
            adjusted_close=50.0,
            trading_value=1_000_000.0,
            price_limit_flag=False,
        )
        post_split = PricePoint(
            date=date(2026, 2, 28),
            unadjusted_open=50.0,
            unadjusted_close=50.0,
            adjusted_close=50.0,
            trading_value=1_000_000.0,
            price_limit_flag=False,
        )

        adjusted_shares = adjusted_shares_for_trade(100, pre_split)

        self.assertEqual(200, adjusted_shares)
        self.assertEqual(10_000, position_value(adjusted_shares, pre_split))
        self.assertEqual(10_000, position_value(adjusted_shares, post_split))
        self.assertEqual(200, actual_shares_from_adjusted(adjusted_shares, post_split))

    def test_missing_adjusted_close_uses_adjustment_factor_for_reverse_split(self) -> None:
        price_index = build_price_index(
            [
                {
                    "date": "2026-01-31",
                    "code": "1001",
                    "unadjusted_open": "100",
                    "unadjusted_close": "100",
                    "adjusted_close": "",
                    "trading_value": "1000000",
                    "adjustment_factor": "1",
                    "price_limit_flag": "false",
                },
                {
                    "date": "2026-02-28",
                    "code": "1001",
                    "unadjusted_open": "1000",
                    "unadjusted_close": "1000",
                    "adjusted_close": "",
                    "trading_value": "1000000",
                    "adjustment_factor": "10",
                    "price_limit_flag": "false",
                },
            ]
        )

        pre_split, post_split = price_index["1001"]
        adjusted_shares = adjusted_shares_for_trade(100, pre_split)

        self.assertEqual(100, adjusted_shares)
        self.assertEqual(10_000, position_value(adjusted_shares, pre_split))
        self.assertEqual(10_000, position_value(adjusted_shares, post_split))
        self.assertEqual(10, actual_shares_from_adjusted(adjusted_shares, post_split))

    def test_missing_factor_adjusted_close_uses_adjustment_factor(self) -> None:
        rows = rows_until_rebalance(
            [
                {
                    "date": "2026-01-31",
                    "code": "1001",
                    "unadjusted_close": "100",
                    "adjusted_close": "",
                    "adjustment_factor": "1",
                },
                {
                    "date": "2026-02-28",
                    "code": "1001",
                    "unadjusted_close": "1000",
                    "adjusted_close": "",
                    "adjustment_factor": "10",
                },
            ],
            date(2026, 2, 28),
        )

        self.assertEqual(2, len(rows))
        self.assertEqual(100, factor_adjusted_close(rows[0]))
        self.assertEqual(100, factor_adjusted_close(rows[1]))

    def test_adjusted_tax_lot_basis_survives_split(self) -> None:
        pre_split = PricePoint(
            date=date(2026, 1, 31),
            unadjusted_open=100.0,
            unadjusted_close=100.0,
            adjusted_close=50.0,
            trading_value=1_000_000.0,
            price_limit_flag=False,
        )
        post_split = PricePoint(
            date=date(2026, 2, 28),
            unadjusted_open=50.0,
            unadjusted_close=50.0,
            adjusted_close=50.0,
            trading_value=1_000_000.0,
            price_limit_flag=False,
        )

        adjusted_shares = adjusted_shares_for_trade(100, pre_split)
        lots = [
            {
                "adjusted_shares": adjusted_shares,
                "basis_per_adjusted_share": 10_000 / adjusted_shares,
            }
        ]
        adjusted_sold = adjusted_shares_for_trade(200, post_split)

        self.assertEqual(10_000, consume_lots(lots, adjusted_sold))
        self.assertEqual([], lots)

    def test_walkforward_equity_uses_adjusted_valuation_through_split(self) -> None:
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
                        "adjusted_close": 50,
                        "trading_value": 1_000_000,
                        "price_limit_flag": "false",
                    },
                    {
                        "date": "2026-02-28",
                        "code": "1001",
                        "unadjusted_open": 50,
                        "unadjusted_close": 50,
                        "adjusted_close": 50,
                        "trading_value": 1_000_000,
                        "price_limit_flag": "false",
                    },
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
                    "2026-02-28",
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
                    "split_test",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_summary_split_test_202601_202602.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                rows = list(csv.DictReader(file))

            self.assertEqual(2, len(rows))
            self.assertAlmostEqual(
                float(rows[0]["portfolio_equity_after_cost"]),
                float(rows[-1]["portfolio_equity_after_cost"]),
            )


if __name__ == "__main__":
    unittest.main()
