from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from download_jquants_bulk import convert_endpoint_rows, filter_codes  # noqa: E402
from download_jquants_listings_panel import resolve_snapshot_dates  # noqa: E402
from profile_data_coverage import profile_coverage  # noqa: E402
from profile_research_universe import profile_research_universe  # noqa: E402
from research_common import read_csv, write_csv  # noqa: E402


class GenericResearchPipelineTest(unittest.TestCase):
    def test_listings_panel_uses_last_trading_day_per_quarter_month(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calendar_path = Path(temp_dir) / "calendar.csv"
            write_csv(
                calendar_path,
                [
                    {"date": "2026-03-30"},
                    {"date": "2026-03-31"},
                    {"date": "2026-06-29"},
                ],
                ["date"],
            )
            args = argparse.Namespace(
                dates_file=None,
                dates=None,
                from_date="2026-01-01",
                to_date="2026-06-30",
                frequency="quarterly",
                calendar=calendar_path,
            )

            self.assertEqual(["2026-03-31", "2026-06-29"], resolve_snapshot_dates(args))

    def test_bulk_converter_supports_prices_without_api_calls(self) -> None:
        raw_rows = [
            {"Date": "2026-01-02", "Code": "2002", "C": "50", "AdjC": "50", "Vo": "10", "Va": "500"},
            {"Date": "2026-01-01", "Code": "1001", "C": "100", "AdjC": "100", "Vo": "10", "Va": "1000"},
        ]

        selected = filter_codes(raw_rows, {"1001"})
        converted = convert_endpoint_rows("/equities/bars/daily", selected)

        self.assertEqual(["1001"], [row["code"] for row in converted])
        self.assertEqual("2026-01-01", converted[0]["date"])
        self.assertEqual("true", converted[0]["tradable_flag"])

    def test_data_coverage_profiles_common_stock_overlap(self) -> None:
        listings = listing_rows()
        prices = price_rows()
        fundamentals = fundamental_rows()

        rows = profile_coverage(
            listings=listings,
            prices=prices,
            fundamentals=fundamentals,
            rebalance_dates=[date(2026, 6, 30)],
        )

        self.assertEqual(2, rows[0]["common_stock_codes"])
        self.assertEqual(2, rows[0]["common_with_price_on_or_before"])
        self.assertEqual(1, rows[0]["common_with_price_on_date"])
        self.assertEqual(1, rows[0]["common_with_fundamentals"])
        self.assertEqual(1, rows[0]["common_with_price_and_fundamentals"])

    def test_research_universe_profile_reports_constraint_exclusions(self) -> None:
        config = {
            "scope": {"instruments": {"include": ["common_stock"], "exclude": ["etf", "reit"]}},
            "universe": {
                "min_ipo_age_trading_days": 0,
                "liquidity_lookback_days": 1,
                "require_tradable_on_rebalance_date": True,
                "strict_rebalance_price_filter": False,
                "require_fundamentals": True,
            },
        }

        summary, reasons = profile_research_universe(
            config=config,
            listings=listing_rows(),
            prices=price_rows(),
            fundamentals=fundamental_rows(),
            rebalance_dates=[date(2026, 6, 30)],
        )

        self.assertEqual(1, summary[0]["included_count"])
        self.assertEqual(1, summary[0]["excluded_count"])
        self.assertEqual("missing_point_in_time_fundamentals", reasons[0]["reason"])
        self.assertEqual(1, reasons[0]["count"])

    def test_select_research_codes_writes_public_safe_code_list(self) -> None:
        import select_research_codes

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings_path = temp / "listings.csv"
            out_path = temp / "codes.csv"
            write_csv(
                listings_path,
                listing_rows()
                + [
                    {
                        "source_date": "2026-06-30",
                        "code": "1301",
                        "name": "ETF",
                        "security_type": "etf",
                        "is_common_stock": "false",
                        "is_etf_reit_infra": "true",
                    }
                ],
                [
                    "source_date",
                    "code",
                    "name",
                    "market",
                    "sector",
                    "security_type",
                    "is_common_stock",
                    "is_etf_reit_infra",
                    "tradable_flag",
                    "lot_size",
                ],
            )
            original_argv = sys.argv[:]
            try:
                sys.argv = [
                    "select_research_codes.py",
                    "--listings",
                    str(listings_path),
                    "--out",
                    str(out_path),
                    "--as-of",
                    "2026-06-30",
                    "--min-snapshots",
                    "1",
                ]
                self.assertEqual(0, select_research_codes.main())
            finally:
                sys.argv = original_argv

            self.assertEqual(["1001", "1002"], [row["code"] for row in read_csv(out_path)])


def listing_rows() -> list[dict[str, str]]:
    return [
        {
            "source_date": "2026-03-31",
            "code": "1001",
            "name": "A",
            "market": "Prime",
            "sector": "Tech",
            "security_type": "common_stock",
            "is_common_stock": "true",
            "is_etf_reit_infra": "false",
            "tradable_flag": "true",
            "lot_size": "100",
        },
        {
            "source_date": "2026-06-30",
            "code": "1001",
            "name": "A",
            "market": "Prime",
            "sector": "Tech",
            "security_type": "common_stock",
            "is_common_stock": "true",
            "is_etf_reit_infra": "false",
            "tradable_flag": "true",
            "lot_size": "100",
        },
        {
            "source_date": "2026-06-30",
            "code": "1002",
            "name": "B",
            "market": "Standard",
            "sector": "Retail",
            "security_type": "common_stock",
            "is_common_stock": "true",
            "is_etf_reit_infra": "false",
            "tradable_flag": "true",
            "lot_size": "100",
        },
    ]


def price_rows() -> list[dict[str, str]]:
    return [
        {
            "date": "2026-06-30",
            "code": "1001",
            "unadjusted_close": "100",
            "adjusted_close": "100",
            "trading_value": "1000000",
            "tradable_flag": "true",
            "price_limit_flag": "false",
        },
        {
            "date": "2026-06-29",
            "code": "1002",
            "unadjusted_close": "100",
            "adjusted_close": "100",
            "trading_value": "1000000",
            "tradable_flag": "true",
            "price_limit_flag": "false",
        },
    ]


def fundamental_rows() -> list[dict[str, str]]:
    return [
        {
            "available_date": "2026-01-31",
            "code": "1001",
            "operating_profit": "10",
            "equity": "100",
            "total_assets": "200",
            "shares_outstanding": "1000",
        }
    ]


if __name__ == "__main__":
    unittest.main()
