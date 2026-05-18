from __future__ import annotations

import unittest

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_contracts import validate_contracts  # noqa: E402


class ValidateContractsTest(unittest.TestCase):
    def test_valid_minimal_contracts_have_no_errors(self) -> None:
        config = {"universe": {"min_ipo_age_trading_days": 0, "liquidity_lookback_days": 0}}
        listings = [
            {
                "code": "1001",
                "name": "Sample",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2020-01-01",
                "delisted_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            }
        ]
        prices = []
        for index in range(280):
            prices.append(
                {
                    "date": f"2025-{index // 28 + 1:02d}-{index % 28 + 1:02d}",
                    "code": "1001",
                    "unadjusted_close": "1000",
                    "adjusted_close": "1000",
                    "trading_value": "1000000",
                    "tradable_flag": "true",
                    "price_limit_flag": "false",
                }
            )
        fundamentals = [
            {
                "code": "1001",
                "available_date": "2025-12-15",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": "100",
                "net_profit": "80",
                "equity": "1000",
                "total_assets": "2000",
                "shares_outstanding": "100",
            }
        ]

        issues, summary = validate_contracts(
            config=config,
            listing_rows=listings,
            price_rows=prices,
            fundamental_rows=fundamentals,
        )

        self.assertEqual([], [row for row in issues if row["severity"] == "error"])
        self.assertEqual(1, summary["unique_codes"]["prices"])

    def test_bad_date_number_and_duplicate_price_are_errors(self) -> None:
        config = {"universe": {}}
        listings = [
            {
                "code": "1001",
                "name": "Sample",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "bad-date",
                "delisted_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "not-a-number",
            }
        ]
        price_row = {
            "date": "2025-01-01",
            "code": "1001",
            "unadjusted_close": "1000",
            "adjusted_close": "1000",
            "trading_value": "1000000",
            "tradable_flag": "true",
            "price_limit_flag": "false",
        }
        fundamentals = [
            {
                "code": "1001",
                "available_date": "2025-01-02",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": "100",
                "net_profit": "80",
                "equity": "1000",
                "total_assets": "2000",
                "shares_outstanding": "100",
            }
        ]

        issues, _summary = validate_contracts(
            config=config,
            listing_rows=listings,
            price_rows=[dict(price_row), dict(price_row)],
            fundamental_rows=fundamentals,
        )

        error_checks = {row["check"] for row in issues if row["severity"] == "error"}
        self.assertIn("date_parse", error_checks)
        self.assertIn("number_parse", error_checks)
        self.assertIn("duplicate_key", error_checks)

    def test_missing_or_nan_price_values_are_errors(self) -> None:
        config = {"universe": {}}
        listings = [
            {
                "code": "1001",
                "name": "Sample",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2020-01-01",
                "delisted_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            }
        ]
        fundamentals = [
            {
                "code": "1001",
                "available_date": "2025-01-02",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": "100",
                "net_profit": "80",
                "equity": "1000",
                "total_assets": "2000",
                "shares_outstanding": "100",
            }
        ]

        issues, _summary = validate_contracts(
            config=config,
            listing_rows=listings,
            price_rows=[
                {
                    "date": "2025-01-01",
                    "code": "1001",
                    "unadjusted_close": "",
                    "adjusted_close": "nan",
                    "trading_value": "1000000",
                    "tradable_flag": "true",
                    "price_limit_flag": "false",
                }
            ],
            fundamental_rows=fundamentals,
        )

        error_checks = {row["check"] for row in issues if row["severity"] == "error"}
        self.assertIn("missing_price", error_checks)
        self.assertIn("number_parse", error_checks)
        self.assertIn("missing_adjusted_price_basis", error_checks)

    def test_missing_adjusted_close_is_valid_when_adjustment_factor_is_available(self) -> None:
        config = {"universe": {"min_ipo_age_trading_days": 0, "liquidity_lookback_days": 0}}
        listings = [
            {
                "code": "1001",
                "name": "Sample",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2020-01-01",
                "delisted_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            }
        ]
        prices = []
        for index in range(280):
            prices.append(
                {
                    "date": f"2025-{index // 28 + 1:02d}-{index % 28 + 1:02d}",
                    "code": "1001",
                    "unadjusted_close": "1000",
                    "adjusted_close": "",
                    "adjustment_factor": "1",
                    "trading_value": "1000000",
                    "tradable_flag": "true",
                    "price_limit_flag": "false",
                }
            )
        fundamentals = [
            {
                "code": "1001",
                "available_date": "2025-12-15",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": "100",
                "net_profit": "80",
                "equity": "1000",
                "total_assets": "2000",
                "shares_outstanding": "100",
            }
        ]

        issues, _summary = validate_contracts(
            config=config,
            listing_rows=listings,
            price_rows=prices,
            fundamental_rows=fundamentals,
        )

        self.assertEqual([], [row for row in issues if row["severity"] == "error"])

    def test_all_empty_delisted_dates_are_warned(self) -> None:
        config = {"universe": {"min_ipo_age_trading_days": 0, "liquidity_lookback_days": 0}}
        listings = [
            {
                "code": "1001",
                "name": "Sample",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2020-01-01",
                "delisted_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            }
        ]
        prices = [
            {
                "date": "2025-01-01",
                "code": "1001",
                "unadjusted_close": "1000",
                "adjusted_close": "1000",
                "trading_value": "1000000",
                "tradable_flag": "true",
                "price_limit_flag": "false",
            }
        ]
        fundamentals = [
            {
                "code": "1001",
                "available_date": "2025-01-02",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": "100",
                "net_profit": "80",
                "equity": "1000",
                "total_assets": "2000",
                "shares_outstanding": "100",
            }
        ]

        issues, _summary = validate_contracts(
            config=config,
            listing_rows=listings,
            price_rows=prices,
            fundamental_rows=fundamentals,
        )

        warning_checks = {row["check"] for row in issues if row["severity"] == "warning"}
        self.assertIn("delisting_lifecycle_coverage", warning_checks)

    def test_last_trading_date_counts_as_lifecycle_exit_coverage(self) -> None:
        config = {"universe": {"min_ipo_age_trading_days": 0, "liquidity_lookback_days": 0}}
        listings = [
            {
                "code": "1001",
                "name": "Sample",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2020-01-01",
                "delisted_date": "",
                "last_trading_date": "2026-01-31",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            }
        ]
        prices = [
            {
                "date": "2025-01-01",
                "code": "1001",
                "unadjusted_close": "1000",
                "adjusted_close": "1000",
                "trading_value": "1000000",
                "tradable_flag": "true",
                "price_limit_flag": "false",
            }
        ]
        fundamentals = [
            {
                "code": "1001",
                "available_date": "2025-01-02",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": "100",
                "net_profit": "80",
                "equity": "1000",
                "total_assets": "2000",
                "shares_outstanding": "100",
            }
        ]

        issues, _summary = validate_contracts(
            config=config,
            listing_rows=listings,
            price_rows=prices,
            fundamental_rows=fundamentals,
        )

        warning_checks = {row["check"] for row in issues if row["severity"] == "warning"}
        self.assertNotIn("delisting_lifecycle_coverage", warning_checks)

    def test_lifecycle_status_and_date_order_are_validated(self) -> None:
        config = {"universe": {"min_ipo_age_trading_days": 0, "liquidity_lookback_days": 0}}
        listings = [
            {
                "code": "1001",
                "name": "Bad Status",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2026-01-01",
                "delisted_date": "",
                "last_trading_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
                "listing_lifecycle_status": "halted_forever",
            },
            {
                "code": "1002",
                "name": "Bad Order",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2026-03-01",
                "delisted_date": "2026-02-01",
                "last_trading_date": "2026-02-02",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
                "listing_lifecycle_status": "delisted",
            },
            {
                "code": "1003",
                "name": "Missing Exit",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "2020-01-01",
                "delisted_date": "",
                "last_trading_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
                "listing_lifecycle_status": "merged",
            },
        ]
        prices = [
            {
                "date": "2026-01-01",
                "code": "1001",
                "unadjusted_close": "1000",
                "adjusted_close": "1000",
                "trading_value": "1000000",
                "tradable_flag": "true",
                "price_limit_flag": "false",
            }
        ]
        fundamentals = [
            {
                "code": "1001",
                "available_date": "2026-01-02",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": "100",
                "net_profit": "80",
                "equity": "1000",
                "total_assets": "2000",
                "shares_outstanding": "100",
            }
        ]

        issues, _summary = validate_contracts(
            config=config,
            listing_rows=listings,
            price_rows=prices,
            fundamental_rows=fundamentals,
        )

        error_checks = {row["check"] for row in issues if row["severity"] == "error"}
        warning_checks = {row["check"] for row in issues if row["severity"] == "warning"}
        self.assertIn("listing_lifecycle_status_value", error_checks)
        self.assertIn("lifecycle_date_order", error_checks)
        self.assertIn("terminal_lifecycle_missing_exit_date", warning_checks)


if __name__ == "__main__":
    unittest.main()
