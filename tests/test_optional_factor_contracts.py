from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_crowding_factor_panel import build_panel as build_crowding_panel  # noqa: E402
from validate_optional_factor_contract import validate_panel  # noqa: E402


class OptionalFactorContractsTest(unittest.TestCase):
    def test_validates_dividend_balance_and_crowding_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            dividend = temp / "dividend.csv"
            balance = temp / "balance.csv"
            crowding = temp / "crowding.csv"
            with dividend.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "available_date", "forecast_dividend_per_share"])
                writer.writeheader()
                writer.writerow({"code": "1001", "available_date": "2026-01-31", "forecast_dividend_per_share": "10"})
            with balance.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "available_date", "cash_and_equivalents", "market_cap"])
                writer.writeheader()
                writer.writerow({"code": "1001", "available_date": "2026-01-31", "cash_and_equivalents": "50", "market_cap": "100"})
            with crowding.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "available_date", "long_margin_balance"])
                writer.writeheader()
                writer.writerow({"code": "1001", "available_date": "2026-01-31", "long_margin_balance": "20"})

            self.assertEqual(1, validate_panel(dividend, "dividend", ["forecast_dividend_per_share"]))
            self.assertEqual(1, validate_panel(balance, "balance_sheet", ["cash_and_equivalents"]))
            self.assertEqual(1, validate_panel(crowding, "crowding", ["long_margin_balance"]))

    def test_validator_rejects_invalid_present_numeric_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = Path(temp_dir) / "dividend.csv"
            with panel.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["code", "available_date", "forecast_dividend_per_share"])
                writer.writeheader()
                writer.writerow({"code": "1001", "available_date": "2026-01-31", "forecast_dividend_per_share": "not-a-number"})

            with self.assertRaisesRegex(ValueError, "invalid numeric field"):
                validate_panel(panel, "dividend")

    def test_validator_rejects_sector_only_crowding_panel_for_issuer_builder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = Path(temp_dir) / "crowding.csv"
            with panel.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["Sector33Code", "available_date", "long_margin_balance"])
                writer.writeheader()
                writer.writerow({"Sector33Code": "6050", "available_date": "2026-01-31", "long_margin_balance": "20"})

            with self.assertRaisesRegex(ValueError, "requires one key field"):
                validate_panel(panel, "crowding")

    def test_required_numeric_accepts_supported_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = Path(temp_dir) / "crowding.csv"
            with panel.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["LocalCode", "available_date", "LongMarginTradeVolume"])
                writer.writeheader()
                writer.writerow({"LocalCode": "1001", "available_date": "2026-01-31", "LongMarginTradeVolume": "20"})

            self.assertEqual(1, validate_panel(panel, "crowding", ["long_margin_balance"]))

    def test_crowding_panel_computes_ratios_zscore_and_change(self) -> None:
        rows = [
            {
                "code": "1001",
                "available_date": "2026-01-31",
                "long_margin_balance": "100",
                "short_margin_balance": "50",
                "short_interest": "25",
                "volume": "1000",
            },
            {
                "code": "1002",
                "available_date": "2026-01-31",
                "long_margin_balance": "300",
                "short_margin_balance": "150",
                "short_interest": "75",
                "volume": "1000",
            },
            {
                "code": "1001",
                "available_date": "2026-02-28",
                "long_margin_balance": "120",
                "short_margin_balance": "60",
                "short_interest": "30",
                "volume": "1000",
            },
        ]

        panel = build_crowding_panel(rows, rebalance_dates=[date(2026, 1, 31), date(2026, 2, 28)])
        jan = {row["code"]: row for row in panel if row["rebalance_date"] == "2026-01-31"}
        feb = {row["code"]: row for row in panel if row["rebalance_date"] == "2026-02-28"}

        self.assertAlmostEqual(0.1, float(jan["1001"]["margin_buy_balance_to_volume"]))
        self.assertGreater(float(jan["1002"]["crowding_zscore"]), float(jan["1001"]["crowding_zscore"]))
        self.assertAlmostEqual(0.01166666667, float(feb["1001"]["crowding_change"]))

    def test_crowding_change_resets_after_missing_raw_gap(self) -> None:
        rows = [
            {
                "code": "1001",
                "available_date": "2026-01-31",
                "long_margin_balance": "100",
                "volume": "1000",
            },
            {
                "code": "1001",
                "available_date": "2026-02-28",
                "long_margin_balance": "120",
                "volume": "",
            },
            {
                "code": "1001",
                "available_date": "2026-03-31",
                "long_margin_balance": "140",
                "volume": "1000",
            },
        ]

        panel = build_crowding_panel(
            rows,
            rebalance_dates=[date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
            max_lag_days=40,
        )
        by_date = {row["rebalance_date"]: row for row in panel}

        self.assertEqual("", by_date["2026-02-28"]["crowding_raw"])
        self.assertEqual("", by_date["2026-03-31"]["crowding_change"])


if __name__ == "__main__":
    unittest.main()
