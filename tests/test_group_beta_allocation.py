from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_group_basket_return_panel import build_panel as build_basket_panel  # noqa: E402
from build_group_signal_panel import (  # noqa: E402
    build_panel as build_signal_panel,
    external_rows_by_group,
    latest_factor_rows,
    load_basket_rows,
    parse_aggregation,
)
from group_beta_common import load_group_membership_panel, memberships_for_date  # noqa: E402
from validate_group_membership_panel import validate_panel  # noqa: E402


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class GroupBetaAllocationTest(unittest.TestCase):
    def test_membership_validator_rejects_duplicates_missing_group_and_bad_weight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            valid = temp / "membership.csv"
            write_rows(
                valid,
                [
                    {
                        "rebalance_date": "2026-01-31",
                        "code": "1001",
                        "group_type": "sector",
                        "group_id": "tech",
                        "group_name": "Tech",
                        "membership_weight": "",
                    }
                ],
            )
            self.assertEqual(1, validate_panel(valid))

            duplicate = temp / "duplicate.csv"
            write_rows(
                duplicate,
                [
                    {
                        "rebalance_date": "2026-01-31",
                        "code": "1001",
                        "group_type": "sector",
                        "group_id": "tech",
                        "membership_weight": "1",
                    },
                    {
                        "rebalance_date": "2026-01-31",
                        "code": "1001",
                        "group_type": "sector",
                        "group_id": "tech",
                        "membership_weight": "1",
                    },
                ],
            )
            with self.assertRaisesRegex(ValueError, "Duplicate group membership"):
                validate_panel(duplicate)

            missing_group = temp / "missing_group.csv"
            write_rows(
                missing_group,
                [
                    {
                        "rebalance_date": "2026-01-31",
                        "code": "1001",
                        "group_type": "sector",
                        "group_id": "",
                        "membership_weight": "1",
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "missing group membership"):
                validate_panel(missing_group)

            bad_weight = temp / "bad_weight.csv"
            write_rows(
                bad_weight,
                [
                    {
                        "rebalance_date": "2026-01-31",
                        "code": "1001",
                        "group_type": "sector",
                        "group_id": "tech",
                        "membership_weight": "0",
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "Invalid membership_weight"):
                validate_panel(bad_weight)

    def test_asof_membership_updates_and_multi_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel_path = Path(temp_dir) / "membership.csv"
            write_rows(
                panel_path,
                [
                    {
                        "available_date": "2026-01-01",
                        "code": "1001",
                        "group_type": "theme",
                        "group_id": "theme_a",
                        "group_name": "Theme A",
                        "membership_weight": "0.6",
                    },
                    {
                        "available_date": "2026-01-01",
                        "code": "1001",
                        "group_type": "theme",
                        "group_id": "theme_b",
                        "group_name": "Theme B",
                        "membership_weight": "0.4",
                    },
                    {
                        "available_date": "2026-02-01",
                        "code": "1001",
                        "group_type": "theme",
                        "group_id": "theme_a",
                        "group_name": "Theme A",
                        "membership_weight": "0.8",
                    },
                ],
            )
            panel = load_group_membership_panel(panel_path)
            memberships = memberships_for_date(panel, date(2026, 2, 15))
            weights = {(row.group_id, row.code): row.membership_weight for row in memberships}

            self.assertEqual("asof", panel.mode)
            self.assertAlmostEqual(0.8, weights[("theme_a", "1001")])
            self.assertAlmostEqual(0.4, weights[("theme_b", "1001")])

    def test_group_basket_returns_equal_liquidity_and_membership_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            membership = temp / "membership.csv"
            write_rows(
                membership,
                [
                    {"rebalance_date": "2026-01-01", "code": "1001", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "membership_weight": "1", "custom_weight": "100"},
                    {"rebalance_date": "2026-01-01", "code": "1002", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "membership_weight": "1", "custom_weight": "300"},
                    {"rebalance_date": "2026-01-03", "code": "1001", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "membership_weight": "1", "custom_weight": "100"},
                    {"rebalance_date": "2026-01-03", "code": "1003", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "membership_weight": "1", "custom_weight": "100"},
                ],
            )
            prices = [
                {"date": "2026-01-01", "code": "1001", "adjusted_close": "10", "trading_value": "100", "market_cap": "100"},
                {"date": "2026-01-01", "code": "1002", "adjusted_close": "20", "trading_value": "300", "market_cap": "300"},
                {"date": "2026-01-01", "code": "1003", "adjusted_close": "30", "trading_value": "100", "market_cap": "100"},
                {"date": "2026-01-02", "code": "1001", "adjusted_close": "11", "trading_value": "100", "market_cap": "100"},
                {"date": "2026-01-02", "code": "1002", "adjusted_close": "22", "trading_value": "300", "market_cap": "300"},
                {"date": "2026-01-02", "code": "1003", "adjusted_close": "33", "trading_value": "100", "market_cap": "100"},
                {"date": "2026-01-03", "code": "1001", "adjusted_close": "12.1", "trading_value": "100", "market_cap": "100"},
                {"date": "2026-01-03", "code": "1002", "adjusted_close": "22", "trading_value": "300", "market_cap": "300"},
                {"date": "2026-01-03", "code": "1003", "adjusted_close": "36.3", "trading_value": "100", "market_cap": "100"},
            ]

            equal_rows = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)],
                input_format="csv",
                weighting_mode="equal_weight",
            )
            liquidity_rows = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1), date(2026, 1, 2)],
                input_format="csv",
                weighting_mode="liquidity_weight",
            )
            market_cap_rows = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1)],
                input_format="csv",
                weighting_mode="market_cap_weight",
            )
            custom_rows = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1)],
                input_format="csv",
                weighting_mode="custom_weight",
                custom_weight_field="custom_weight",
            )
            equal_by_date = {row["date"].isoformat(): row for row in equal_rows if row["group_id"] == "tech"}
            liq_by_date = {row["date"].isoformat(): row for row in liquidity_rows if row["group_id"] == "tech"}

            self.assertAlmostEqual(0.1, float(equal_by_date["2026-01-02"]["basket_return"]))
            self.assertAlmostEqual(0.1, float(liq_by_date["2026-01-02"]["basket_return"]))
            self.assertAlmostEqual(0.75, float(liq_by_date["2026-01-01"]["top_constituent_weight"]))
            self.assertAlmostEqual(0.75, float(market_cap_rows[0]["top_constituent_weight"]))
            self.assertAlmostEqual(0.75, float(custom_rows[0]["top_constituent_weight"]))
            self.assertEqual(2, equal_by_date["2026-01-03"]["constituent_count"])
            self.assertGreater(float(equal_by_date["2026-01-03"]["turnover"]), 0)

    def test_group_signal_panel_builds_returns_beta_factor_aggregates_and_external_asof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            membership = temp / "membership.csv"
            basket = temp / "basket.csv"
            factors = temp / "factors.csv"
            external = temp / "external.csv"
            benchmark = {"2026-01-02": 0.05, "2026-01-03": 0.02, "2026-01-04": -0.01}
            write_rows(
                membership,
                [
                    {"rebalance_date": "2026-01-01", "code": "1001", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "membership_weight": "1"},
                    {"rebalance_date": "2026-01-01", "code": "1002", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "membership_weight": "3"},
                ],
            )
            write_rows(
                basket,
                [
                    {"date": "2026-01-01", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "constituent_count": "2", "coverage": "0", "basket_return": ""},
                    {"date": "2026-01-02", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "constituent_count": "2", "coverage": "1", "basket_return": "0.10"},
                    {"date": "2026-01-03", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "constituent_count": "2", "coverage": "1", "basket_return": "0.20"},
                    {"date": "2026-01-04", "group_type": "sector", "group_id": "tech", "group_name": "Tech", "constituent_count": "2", "coverage": "1", "basket_return": "-0.10"},
                ],
            )
            write_rows(
                factors,
                [
                    {"rebalance_date": "2026-01-04", "code": "1001", "book_to_market": "10"},
                    {"rebalance_date": "2026-01-04", "code": "1002", "book_to_market": "20"},
                ],
            )
            write_rows(
                external,
                [
                    {"available_date": "2026-01-02", "group_type": "sector", "group_id": "tech", "risk_state": "calm"},
                    {"available_date": "2026-01-04", "group_type": "sector", "group_id": "tech", "risk_state": "stress"},
                ],
            )
            # The builder accepts benchmark returns as an in-memory mapping; CLI users can pass a file.
            rows, fields = build_signal_panel(
                load_basket_rows(basket, "csv"),
                membership,
                rebalance_dates=[date(2026, 1, 4)],
                input_format="csv",
                factor_rows=latest_factor_rows(factors, "csv"),
                aggregation_specs=[parse_aggregation("book_to_market:weighted_mean")],
                external_rows=external_rows_by_group(external, "csv", ["risk_state"], "available_date"),
                external_fields=["risk_state"],
                external_asof=True,
                benchmark_by_date={date.fromisoformat(key): value for key, value in benchmark.items()},
                momentum_windows=[3],
                risk_windows=[3],
                beta_window=3,
            )
            row = rows[0]

            self.assertIn("book_to_market_weighted_mean", fields)
            self.assertAlmostEqual((1.1 * 1.2 * 0.9) - 1, float(row["group_return_3p"]))
            self.assertAlmostEqual(17.5, float(row["book_to_market_weighted_mean"]))
            self.assertEqual("stress", row["risk_state"])
            self.assertNotEqual("", row["group_beta_to_benchmark"])


if __name__ == "__main__":
    unittest.main()
