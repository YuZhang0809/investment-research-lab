from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_qvm_walkforward  # noqa: E402
from analyze_event_drift import PricePoint as DriftPricePoint  # noqa: E402
from analyze_event_drift import drift_return  # noqa: E402
from analyze_factor_forward_returns import PricePoint as FactorPricePoint  # noqa: E402
from analyze_factor_forward_returns import future_return as factor_future_return  # noqa: E402
from build_ml_ranker_dataset import PricePoint as MlPricePoint  # noqa: E402
from build_ml_ranker_dataset import future_return as ml_future_return  # noqa: E402
from build_universe import build_universe_from_rows  # noqa: E402
from build_factors import return_with_skip  # noqa: E402
from download_jquants import convert_master, has_trade  # noqa: E402
from validate_contracts import validate_contracts  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class P0P1CorrectnessTest(unittest.TestCase):
    def test_jquants_conversion_does_not_overstate_tradability(self) -> None:
        self.assertFalse(has_trade({"C": "100", "Vo": "0", "Va": "0"}))
        self.assertTrue(has_trade({"C": "100", "Vo": "100", "Va": "10000"}))

        converted = convert_master([{"Code": "1001", "CoName": "Sample"}], "2026-05-15")

        self.assertEqual("", converted[0]["tradable_flag"])
        self.assertEqual("snapshot_only_missing_lifecycle_dates", converted[0]["listing_lifecycle_status"])

    def test_snapshot_only_listings_fail_contract_validation(self) -> None:
        config = {"universe": {"min_ipo_age_trading_days": 0, "liquidity_lookback_days": 0}}
        listings = [
            {
                "code": "1001",
                "name": "Snapshot",
                "market": "Prime",
                "sector": "Tech",
                "listed_date": "",
                "delisted_date": "",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "",
                "lot_size": "100",
                "listing_lifecycle_status": "snapshot_only_missing_lifecycle_dates",
            }
        ]
        prices = [
            {
                "date": "2026-01-31",
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
                "available_date": "2026-01-15",
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
        self.assertIn("listing_lifecycle_coverage", error_checks)

    def test_lifecycle_status_requires_more_than_one_delisting_marker(self) -> None:
        self.assertEqual("unknown", run_qvm_walkforward.lifecycle_data_status([]))
        self.assertEqual(
            "pit_snapshot_panel",
            run_qvm_walkforward.lifecycle_data_status(
                [{"code": "1001", "listed_date": "", "listing_lifecycle_status": "pit_snapshot_panel_missing_lifecycle_dates"}]
            ),
        )
        self.assertEqual(
            "snapshot_only",
            run_qvm_walkforward.lifecycle_data_status(
                [{"code": "1001", "listed_date": "", "delisted_date": ""}]
            ),
        )
        self.assertEqual(
            "partial_lifecycle",
            run_qvm_walkforward.lifecycle_data_status(
                [
                    {"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""},
                    {"code": "1002", "listed_date": "", "delisted_date": ""},
                ]
            ),
        )
        self.assertEqual(
            "pit_no_delistings_observed",
            run_qvm_walkforward.lifecycle_data_status(
                [{"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""}]
            ),
        )
        self.assertEqual(
            "pit_with_delistings",
            run_qvm_walkforward.lifecycle_data_status(
                [
                    {"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""},
                    {"code": "1002", "listed_date": "2020-01-01", "delisted_date": "2026-01-31"},
                ]
            ),
        )
        self.assertFalse(run_qvm_walkforward.performance_conclusion_allowed("pit_no_delistings_observed"))
        self.assertTrue(run_qvm_walkforward.performance_conclusion_allowed("pit_with_delistings"))

    def test_build_universe_uses_latest_listing_snapshot_as_of_rebalance(self) -> None:
        config = {
            "scope": {
                "instruments": {
                    "include": ["common_stock"],
                    "exclude": ["etf", "reit"],
                }
            },
            "universe": {
                "min_ipo_age_trading_days": 0,
                "liquidity_lookback_days": 1,
                "require_tradable_on_rebalance_date": True,
                "strict_rebalance_price_filter": False,
                "require_fundamentals": False,
            },
        }
        listing_rows = [
            {
                "code": "1001",
                "name": "Still Listed",
                "source_date": "2026-01-31",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            },
            {
                "code": "1002",
                "name": "Gone By Feb",
                "source_date": "2026-01-31",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            },
            {
                "code": "1001",
                "name": "Still Listed",
                "source_date": "2026-02-28",
                "security_type": "common_stock",
                "is_common_stock": "true",
                "is_etf_reit_infra": "false",
                "tradable_flag": "true",
                "lot_size": "100",
            },
        ]
        price_rows = [
            {
                "date": "2026-03-31",
                "code": "1001",
                "unadjusted_close": "100",
                "trading_value": "1000000",
                "tradable_flag": "true",
                "price_limit_flag": "false",
            },
            {
                "date": "2026-03-31",
                "code": "1002",
                "unadjusted_close": "100",
                "trading_value": "1000000",
                "tradable_flag": "true",
                "price_limit_flag": "false",
            },
        ]

        included, excluded = build_universe_from_rows(
            config=config,
            rebalance_date=date(2026, 3, 31),
            listing_rows=listing_rows,
            price_rows=price_rows,
            fundamental_rows=[],
        )

        self.assertEqual(["1001"], [row["code"] for row in included])
        self.assertEqual("2026-02-28", included[0]["source_date"])
        self.assertEqual("pit_snapshot_panel_missing_lifecycle_dates", included[0]["listing_lifecycle_status"])
        self.assertNotIn("1002", [row["code"] for row in included])

    def test_factor_forward_return_excludes_price_tail_gap_without_lifecycle_data(self) -> None:
        calendar = [date(2026, 1, 1) + timedelta(days=index) for index in range(5)]
        points = [
            FactorPricePoint(date=date(2026, 1, 1), adjusted_close=100.0),
            FactorPricePoint(date=date(2026, 1, 2), adjusted_close=90.0),
        ]

        result = factor_future_return(points, calendar, date(2026, 1, 1), 3)

        self.assertEqual("price_tail_gap", result.status)
        self.assertIsNone(result.value)

    def test_event_and_ml_diagnostics_exclude_price_tail_gap_without_lifecycle_data(self) -> None:
        calendar = [date(2026, 1, 1) + timedelta(days=index) for index in range(5)]
        drift_points = [
            DriftPricePoint(date=date(2026, 1, 1), adjusted_close=100.0),
            DriftPricePoint(date=date(2026, 1, 2), adjusted_close=90.0),
        ]
        ml_points = [
            MlPricePoint(date=date(2026, 1, 1), adjusted_close=100.0),
            MlPricePoint(date=date(2026, 1, 2), adjusted_close=90.0),
        ]

        drift_value, drift_status = drift_return(drift_points, calendar, date(2026, 1, 1), 3)
        ml_value, ml_status = ml_future_return(ml_points, calendar, date(2026, 1, 1), 3)

        self.assertEqual("price_tail_gap", drift_status)
        self.assertIsNone(drift_value)
        self.assertEqual("price_tail_gap", ml_status)
        self.assertIsNone(ml_value)

    def test_factor_momentum_uses_market_calendar_not_per_code_row_count(self) -> None:
        calendar = [date(2026, 1, 1) + timedelta(days=index) for index in range(10)]
        rows = [
            {"date": "2026-01-01", "adjusted_close": "100"},
            {"date": "2026-01-02", "adjusted_close": "200"},
            {"date": "2026-01-09", "adjusted_close": "110"},
            {"date": "2026-01-10", "adjusted_close": "120"},
        ]

        value = return_with_skip(rows, calendar, date(2026, 1, 10), lookback_days=4, skip_days=1)

        self.assertAlmostEqual(-0.45, value or 0)

    def test_universe_keeps_stale_price_rows_for_research_layer(self) -> None:
        config = {
            "scope": {
                "instruments": {"include": ["common_stock"], "exclude": []},
            },
            "universe": {
                "min_ipo_age_trading_days": 0,
                "liquidity_lookback_days": 1,
                "require_fundamentals": False,
                "require_tradable_on_rebalance_date": True,
            },
        }
        listings = [
            {
                "code": "1001",
                "name": "Stale Price",
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
                "date": "2026-03-30",
                "code": "1001",
                "unadjusted_close": "1000",
                "adjusted_close": "1000",
                "trading_value": "1000000",
                "tradable_flag": "true",
                "price_limit_flag": "false",
            },
            {
                "date": "2026-03-31",
                "code": "9999",
                "unadjusted_close": "1000",
                "adjusted_close": "1000",
                "trading_value": "1000000",
                "tradable_flag": "true",
                "price_limit_flag": "false",
            },
        ]

        universe_rows, exclusion_rows = build_universe_from_rows(
            config=config,
            rebalance_date=date(2026, 3, 31),
            listing_rows=listings,
            price_rows=prices,
            fundamental_rows=[],
        )

        self.assertEqual([], exclusion_rows)
        self.assertEqual("1001", universe_rows[0]["code"])
        self.assertFalse(universe_rows[0]["rebalance_price_available"])
        self.assertTrue(universe_rows[0]["latest_price_stale"])
        self.assertEqual(1, universe_rows[0]["price_staleness_trading_days"])

    def test_walkforward_closes_terminal_holding_at_zero(self) -> None:
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
                        "price_limit_flag": "false",
                    },
                    {
                        "date": "2026-02-28",
                        "code": "2002",
                        "unadjusted_open": 100,
                        "unadjusted_close": 100,
                        "adjusted_close": 100,
                        "trading_value": 10_000_000,
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
            write_csv(
                listings,
                [
                    {
                        "code": "1001",
                        "listed_date": "2020-01-01",
                        "delisted_date": "2026-02-01",
                    }
                ],
                ["code", "listed_date", "delisted_date"],
            )
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
                    "delist_test",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_summary_delist_test_202601_202602.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                summary_rows = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_failure_cases_delist_test_202601_202602.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                failure_rows = list(csv.DictReader(file))

            self.assertLess(float(summary_rows[-1]["portfolio_equity_after_cost"]), 200)
            self.assertIn("assumed_delisting_loss", {row["failure_type"] for row in failure_rows})

    def test_walkforward_tail_gap_policy_can_close_stale_holding_at_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            config_path = temp / "qvm_tail_gap.yml"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_text = config_text.replace("mode: warn_only", "mode: assume_zero_after_n_trading_days")
            config_text = config_text.replace("max_stale_trading_days: 5", "max_stale_trading_days: 1")
            config_path.write_text(config_text, encoding="utf-8")

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
                        "price_limit_flag": "false",
                    },
                    {
                        "date": "2026-02-28",
                        "code": "2002",
                        "unadjusted_open": 100,
                        "unadjusted_close": 100,
                        "adjusted_close": 100,
                        "trading_value": 10_000_000,
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
            write_csv(
                listings,
                [{"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""}],
                ["code", "listed_date", "delisted_date"],
            )
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
                    str(config_path),
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
                    "tail_gap_test",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_summary_tail_gap_test_202601_202602.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                summary_rows = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_failure_cases_tail_gap_test_202601_202602.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                failure_rows = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_trades_tail_gap_test_202601_202602.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                trade_rows = list(csv.DictReader(file))

            self.assertLess(float(summary_rows[-1]["portfolio_equity_after_cost"]), 200)
            self.assertEqual("assume_zero_after_n_trading_days", summary_rows[-1]["missing_price_tail_policy"])
            self.assertIn("price_tail_gap", {row["failure_type"] for row in failure_rows})
            self.assertIn("assumed_tail_gap_zero", {row["failure_type"] for row in failure_rows})
            self.assertIn("TAIL_GAP_ZERO", {row["side"] for row in trade_rows})


if __name__ == "__main__":
    unittest.main()
