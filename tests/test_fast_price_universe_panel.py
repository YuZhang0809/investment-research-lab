from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_rebalance_price_universe_panel import build_panel, build_panel_frame  # noqa: E402
from compare_fast_panel_to_legacy import DEFAULT_COMPARE_FIELDS, compare_fast_to_legacy, compare_rows  # noqa: E402
from research_common import read_csv, write_csv  # noqa: E402


def synthetic_config() -> dict:
    return {
        "scope": {"instruments": {"include": ["common_stock"], "exclude": ["etf", "reit"]}},
        "universe": {
            "min_ipo_age_trading_days": 20,
            "liquidity_lookback_days": 20,
            "min_median_trading_value_jpy": 1_000,
            "require_tradable_on_rebalance_date": True,
            "strict_rebalance_price_filter": True,
            "require_fundamentals": False,
        },
    }


def minimal_config(*, require_fundamentals: bool = False) -> dict:
    return {
        "scope": {"instruments": {"include": ["common_stock"], "exclude": []}},
        "universe": {
            "min_ipo_age_trading_days": 0,
            "liquidity_lookback_days": 1,
            "require_tradable_on_rebalance_date": True,
            "strict_rebalance_price_filter": False,
            "require_fundamentals": require_fundamentals,
        },
    }


def write_synthetic_fixture(temp: Path) -> tuple[Path, Path, Path, date, date]:
    listings = temp / "listings.csv"
    prices = temp / "prices.csv"
    fundamentals = temp / "fundamentals.csv"
    start = date(2025, 1, 1)
    end = start + timedelta(days=279)
    rows = [
        {
            "code": "1001",
            "name": "Active Split",
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
        },
        {
            "code": "1002",
            "name": "Missing Rebalance Price",
            "market": "Prime",
            "sector": "Retail",
            "listed_date": "2020-01-01",
            "delisted_date": "",
            "last_trading_date": "",
            "security_type": "common_stock",
            "is_common_stock": "true",
            "is_etf_reit_infra": "false",
            "tradable_flag": "true",
            "lot_size": "100",
        },
        {
            "code": "1003",
            "name": "New Listing",
            "market": "Prime",
            "sector": "Services",
            "listed_date": (end - timedelta(days=8)).isoformat(),
            "delisted_date": "",
            "last_trading_date": "",
            "security_type": "common_stock",
            "is_common_stock": "true",
            "is_etf_reit_infra": "false",
            "tradable_flag": "true",
            "lot_size": "100",
        },
        {
            "code": "1004",
            "name": "Delisted Earlier",
            "market": "Prime",
            "sector": "Industrial",
            "listed_date": "2020-01-01",
            "delisted_date": (end + timedelta(days=20)).isoformat(),
            "last_trading_date": (end - timedelta(days=3)).isoformat(),
            "security_type": "common_stock",
            "is_common_stock": "true",
            "is_etf_reit_infra": "false",
            "tradable_flag": "true",
            "lot_size": "100",
        },
        {
            "code": "1005",
            "name": "Non Tradable",
            "market": "Prime",
            "sector": "Materials",
            "listed_date": "2020-01-01",
            "delisted_date": "",
            "last_trading_date": "",
            "security_type": "common_stock",
            "is_common_stock": "true",
            "is_etf_reit_infra": "false",
            "tradable_flag": "true",
            "lot_size": "100",
        },
    ]
    write_csv(
        listings,
        rows,
        [
            "code",
            "name",
            "market",
            "sector",
            "listed_date",
            "delisted_date",
            "last_trading_date",
            "security_type",
            "is_common_stock",
            "is_etf_reit_infra",
            "tradable_flag",
            "lot_size",
        ],
    )

    price_rows: list[dict[str, object]] = []
    for index in range(280):
        day = start + timedelta(days=index)
        for code_index, code in enumerate(["1001", "1002", "1004", "1005"], start=1):
            if code == "1002" and day == end:
                continue
            if code == "1004" and day > end - timedelta(days=3):
                continue
            close = 100 + index + code_index
            adjustment_factor = 2 if code == "1001" and index == 150 else 1
            unadjusted_close = close * 2 if code == "1001" and index >= 150 else close
            price_rows.append(
                {
                    "date": day.isoformat(),
                    "code": code,
                    "unadjusted_close": unadjusted_close,
                    "adjusted_close": "" if code == "1001" else close,
                    "adjustment_factor": adjustment_factor,
                    "trading_value": 10_000 + index + code_index,
                    "tradable_flag": "false" if code == "1005" and day == end else "true",
                    "price_limit_flag": "false",
                }
            )
    for index in range(9):
        day = end - timedelta(days=8 - index)
        price_rows.append(
            {
                "date": day.isoformat(),
                "code": "1003",
                "unadjusted_close": 100 + index,
                "adjusted_close": 100 + index,
                "adjustment_factor": 1,
                "trading_value": 10_000 + index,
                "tradable_flag": "true",
                "price_limit_flag": "false",
            }
        )
    write_csv(
        prices,
        sorted(price_rows, key=lambda row: (str(row["date"]), str(row["code"]))),
        [
            "date",
            "code",
            "unadjusted_close",
            "adjusted_close",
            "adjustment_factor",
            "trading_value",
            "tradable_flag",
            "price_limit_flag",
        ],
    )
    write_csv(fundamentals, [], ["code", "available_date"])
    return listings, prices, fundamentals, start, end


class FastPriceUniversePanelTest(unittest.TestCase):
    def test_compare_rows_normalizes_midnight_datetime_keys(self) -> None:
        diffs = compare_rows(
            legacy_rows=[{"rebalance_date": date(2026, 3, 31), "code": "1001", "included_flag": True}],
            fast_rows=[{"rebalance_date": datetime(2026, 3, 31), "code": "1001", "included_flag": True}],
            fields=["included_flag"],
            tolerance=1e-9,
        )

        self.assertEqual([], diffs)

    def test_quarterly_frequency_filters_monthly_rebalances_after_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals, start, end = write_synthetic_fixture(temp)

            frame = build_panel_frame(
                config=synthetic_config(),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="quarterly",
                input_format="csv",
            )

            dates = sorted({str(value)[:10] for value in frame["rebalance_date"]})
            self.assertEqual(["2025-03-31", "2025-06-30", "2025-09-30"], dates)

    def test_snapshot_date_alias_selects_one_pit_snapshot_per_rebalance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings = temp / "listings.csv"
            prices = temp / "prices.csv"
            write_csv(
                listings,
                [
                    {
                        "snapshot_date": "2026-01-31",
                        "code": "1001",
                        "name": "January Snapshot",
                        "listed_date": "2020-01-01",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "true",
                    },
                    {
                        "snapshot_date": "2026-02-28",
                        "code": "1002",
                        "name": "February Snapshot",
                        "listed_date": "2020-01-01",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "true",
                    },
                ],
                ["snapshot_date", "code", "name", "listed_date", "security_type", "is_common_stock", "is_etf_reit_infra", "tradable_flag"],
            )
            write_csv(
                prices,
                [
                    {"date": "2026-01-31", "code": "1001", "unadjusted_close": "100", "adjusted_close": "100", "trading_value": "1000", "tradable_flag": "true"},
                    {"date": "2026-02-28", "code": "1002", "unadjusted_close": "200", "adjusted_close": "200", "trading_value": "1000", "tradable_flag": "true"},
                ],
                ["date", "code", "unadjusted_close", "adjusted_close", "trading_value", "tradable_flag"],
            )

            frame = build_panel_frame(
                config=minimal_config(),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=None,
                start_date="2026-01-31",
                end_date="2026-02-28",
                frequency="monthly",
                input_format="csv",
            )

            keys = sorted((str(row.rebalance_date)[:10], row.code) for row in frame.itertuples())
            self.assertEqual([("2026-01-31", "1001"), ("2026-02-28", "1002")], keys)

    def test_price_and_fundamental_aliases_fall_back_per_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings = temp / "listings.csv"
            prices = temp / "prices.csv"
            fundamentals = temp / "fundamentals.csv"
            write_csv(
                listings,
                [
                    {
                        "code": "1001",
                        "name": "Alias Row",
                        "listed_date": "2020-01-01",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "true",
                    }
                ],
                ["code", "name", "listed_date", "security_type", "is_common_stock", "is_etf_reit_infra", "tradable_flag"],
            )
            write_csv(
                prices,
                [
                    {
                        "date": "2026-01-31",
                        "code": "1001",
                        "unadjusted_close": "",
                        "close": "123",
                        "adjusted_close": "123",
                        "trading_value": "1000",
                        "tradable_flag": "true",
                    }
                ],
                ["date", "code", "unadjusted_close", "close", "adjusted_close", "trading_value", "tradable_flag"],
            )
            write_csv(
                fundamentals,
                [{"code": "1001", "disclosure_date": "2026-01-30"}],
                ["code", "disclosure_date"],
            )

            frame = build_panel_frame(
                config=minimal_config(require_fundamentals=True),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date="2026-01-31",
                end_date="2026-01-31",
                frequency="monthly",
                input_format="csv",
            )

            row = frame.iloc[0]
            self.assertEqual(123, row["latest_unadjusted_close"])
            self.assertTrue(row["has_fundamentals"])
            self.assertTrue(row["included_flag"])

    def test_rejects_duplicate_price_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings = temp / "listings.csv"
            prices = temp / "prices.csv"
            write_csv(
                listings,
                [
                    {
                        "code": "1001",
                        "listed_date": "2020-01-01",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "true",
                    }
                ],
                ["code", "listed_date", "security_type", "is_common_stock", "is_etf_reit_infra", "tradable_flag"],
            )
            write_csv(
                prices,
                [
                    {"date": "2026-01-31", "code": "1001", "unadjusted_close": "100", "adjusted_close": "100", "trading_value": "1000"},
                    {"date": "2026-01-31", "code": "1001", "unadjusted_close": "101", "adjusted_close": "101", "trading_value": "1000"},
                ],
                ["date", "code", "unadjusted_close", "adjusted_close", "trading_value"],
            )

            with self.assertRaisesRegex(ValueError, "Duplicate price rows"):
                build_panel_frame(
                    config=minimal_config(),
                    listings_path=listings,
                    prices_path=prices,
                    fundamentals_path=None,
                    start_date="2026-01-31",
                    end_date="2026-01-31",
                    frequency="monthly",
                    input_format="csv",
                )

    def test_rejects_duplicate_listing_snapshot_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings = temp / "listings.csv"
            prices = temp / "prices.csv"
            write_csv(
                listings,
                [
                    {
                        "source_date": "2026-01-31",
                        "code": "1001",
                        "listed_date": "2020-01-01",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "true",
                    },
                    {
                        "source_date": "2026-01-31",
                        "code": "1001",
                        "listed_date": "2020-01-01",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "false",
                    },
                ],
                ["source_date", "code", "listed_date", "security_type", "is_common_stock", "is_etf_reit_infra", "tradable_flag"],
            )
            write_csv(
                prices,
                [{"date": "2026-01-31", "code": "1001", "unadjusted_close": "100", "adjusted_close": "100", "trading_value": "1000"}],
                ["date", "code", "unadjusted_close", "adjusted_close", "trading_value"],
            )

            with self.assertRaisesRegex(ValueError, "Duplicate listing snapshot rows"):
                build_panel_frame(
                    config=minimal_config(),
                    listings_path=listings,
                    prices_path=prices,
                    fundamentals_path=None,
                    start_date="2026-01-31",
                    end_date="2026-01-31",
                    frequency="monthly",
                    input_format="csv",
                )

    def test_missing_adjusted_close_requires_positive_adjustment_factor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings = temp / "listings.csv"
            prices = temp / "prices.csv"
            write_csv(
                listings,
                [
                    {
                        "code": "1001",
                        "listed_date": "2020-01-01",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "true",
                    }
                ],
                ["code", "listed_date", "security_type", "is_common_stock", "is_etf_reit_infra", "tradable_flag"],
            )
            write_csv(
                prices,
                [{"date": "2026-01-31", "code": "1001", "unadjusted_close": "100", "adjusted_close": "", "adjustment_factor": "0", "trading_value": "1000"}],
                ["date", "code", "unadjusted_close", "adjusted_close", "adjustment_factor", "trading_value"],
            )

            with self.assertRaisesRegex(ValueError, "Missing adjusted_close requires positive adjustment_factor"):
                build_panel_frame(
                    config=minimal_config(),
                    listings_path=listings,
                    prices_path=prices,
                    fundamentals_path=None,
                    start_date="2026-01-31",
                    end_date="2026-01-31",
                    frequency="monthly",
                    input_format="csv",
                )

    def test_duckdb_price_universe_panel_matches_legacy_core_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals, start, end = write_synthetic_fixture(temp)
            out = temp / "fast_panel.csv"

            row_count = build_panel(
                config=synthetic_config(),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="monthly",
                input_format="csv",
                out_path=out,
                output_format="csv",
            )

            self.assertGreater(row_count, 0)
            rows = read_csv(out)
            final_rows = {row["code"]: row for row in rows if row["rebalance_date"] == end.isoformat()}
            self.assertEqual("True", final_rows["1001"]["included_flag"])
            self.assertEqual("False", final_rows["1002"]["included_flag"])
            self.assertIn("no_price_on_rebalance_date", final_rows["1002"]["exclusion_reason"])
            self.assertIn("last_trading_before_rebalance", final_rows["1004"]["exclusion_reason"])
            self.assertIn("price_not_tradable_on_rebalance_date", final_rows["1005"]["exclusion_reason"])

            diffs = compare_fast_to_legacy(
                config=synthetic_config(),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                fast_panel_path=out,
                start_date=start,
                end_date=end,
                frequency="monthly",
                fields=DEFAULT_COMPARE_FIELDS,
                tolerance=1e-9,
            )

            self.assertEqual([], diffs)

    def test_duckdb_panel_builds_medium_synthetic_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings = temp / "listings.csv"
            prices = temp / "prices.csv"
            out = temp / "fast_panel.parquet"
            start = date(2026, 1, 1)
            codes = [f"{1000 + index}" for index in range(30)]
            write_csv(
                listings,
                [
                    {
                        "code": code,
                        "name": f"Synthetic {code}",
                        "market": "Prime",
                        "sector": "Synthetic",
                        "listed_date": "2020-01-01",
                        "delisted_date": "",
                        "last_trading_date": "",
                        "security_type": "common_stock",
                        "is_common_stock": "true",
                        "is_etf_reit_infra": "false",
                        "tradable_flag": "true",
                        "lot_size": "100",
                    }
                    for code in codes
                ],
                [
                    "code",
                    "name",
                    "market",
                    "sector",
                    "listed_date",
                    "delisted_date",
                    "last_trading_date",
                    "security_type",
                    "is_common_stock",
                    "is_etf_reit_infra",
                    "tradable_flag",
                    "lot_size",
                ],
            )
            price_rows = []
            for offset in range(90):
                day = start + timedelta(days=offset)
                for code_index, code in enumerate(codes):
                    price_rows.append(
                        {
                            "date": day.isoformat(),
                            "code": code,
                            "unadjusted_close": 100 + offset + code_index,
                            "adjusted_close": 100 + offset + code_index,
                            "adjustment_factor": 1,
                            "trading_value": 1_000_000 + code_index,
                            "tradable_flag": "true",
                            "price_limit_flag": "false",
                        }
                    )
            write_csv(
                prices,
                price_rows,
                [
                    "date",
                    "code",
                    "unadjusted_close",
                    "adjusted_close",
                    "adjustment_factor",
                    "trading_value",
                    "tradable_flag",
                    "price_limit_flag",
                ],
            )

            frame = build_panel_frame(
                config=synthetic_config(),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=None,
                start_date=start.isoformat(),
                end_date=(start + timedelta(days=89)).isoformat(),
                frequency="monthly",
                input_format="csv",
            )
            self.assertGreater(len(frame), 0)
            build_panel(
                config=synthetic_config(),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=None,
                start_date=start.isoformat(),
                end_date=(start + timedelta(days=89)).isoformat(),
                frequency="monthly",
                input_format="csv",
                out_path=out,
                output_format="parquet",
            )
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
