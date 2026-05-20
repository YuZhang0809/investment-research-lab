from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_qvm_walkforward  # noqa: E402
from run_qvm_walkforward import select_codes, select_codes_detailed, sector_cap_failure_rows  # noqa: E402


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

    def test_sector_cap_disabled_keeps_existing_selection_behavior(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "sector": "Tech"},
            {"code": "1002", "rank": "2", "sector": "Tech"},
            {"code": "1003", "rank": "3", "sector": "Tech"},
        ]
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 3, "target_holdings_max": 3},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "sector_cap": {"enabled": False, "mode": "name_count", "max_names_per_group": 1},
            }
        }

        selected, research = select_codes(scores, holdings={}, config=config)

        self.assertEqual(["1001", "1002", "1003"], selected)
        self.assertEqual(["1001", "1002", "1003"], research)

    def test_name_count_sector_cap_preserves_hold_buffer_and_blocks_new_names(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "sector": "Tech"},
            {"code": "1002", "rank": "2", "sector": "Tech"},
            {"code": "1003", "rank": "3", "sector": "Tech"},
            {"code": "2001", "rank": "4", "sector": "Health"},
            {"code": "2002", "rank": "5", "sector": "Health"},
        ]
        holdings = {"1001": 100.0, "1003": 100.0}
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 4, "target_holdings_max": 4},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "sector_cap": {
                    "enabled": True,
                    "mode": "name_count",
                    "group_field": "sector",
                    "max_names_per_group": 2,
                },
            }
        }

        result = select_codes_detailed(scores, holdings=holdings, config=config)

        self.assertEqual(["1001", "1003", "2001", "2002"], result.selected_codes)
        self.assertEqual(["1002"], [item.code for item in result.blocked_candidates])
        self.assertEqual(0, result.unfilled_slots)
        self.assertEqual(["1001", "1002", "1003", "2001"], result.research_codes)

    def test_name_count_sector_cap_removes_lower_ranked_held_names_first(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "sector": "Tech"},
            {"code": "1002", "rank": "2", "sector": "Tech"},
            {"code": "1003", "rank": "3", "sector": "Tech"},
            {"code": "2001", "rank": "4", "sector": "Health"},
        ]
        holdings = {"1001": 100.0, "1002": 100.0, "1003": 100.0}
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 3, "target_holdings_max": 3},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "sector_cap": {"enabled": True, "mode": "name_count", "max_names_per_group": 2},
            }
        }

        result = select_codes_detailed(scores, holdings=holdings, config=config)

        self.assertEqual(["1001", "1002", "2001"], result.selected_codes)
        self.assertEqual([("1003", "hold")], [(item.code, item.phase) for item in result.blocked_candidates])

    def test_name_count_sector_cap_can_leave_target_unfilled_and_reports_failure(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "sector": "Tech"},
            {"code": "1002", "rank": "2", "sector": "Tech"},
            {"code": "1003", "rank": "3", "sector": "Tech"},
        ]
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 3, "target_holdings_max": 3},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "sector_cap": {"enabled": True, "mode": "name_count", "max_names_per_group": 1},
            }
        }

        result = select_codes_detailed(scores, holdings={}, config=config)
        failure_types = {row["failure_type"] for row in sector_cap_failure_rows(date(2026, 1, 31), result)}

        self.assertEqual(["1001"], result.selected_codes)
        self.assertEqual(2, result.unfilled_slots)
        self.assertEqual(["1001", "1002", "1003"], result.research_codes)
        self.assertIn("sector_cap_blocked_candidate", failure_types)
        self.assertIn("sector_cap_unfilled_target", failure_types)

    def test_name_count_sector_cap_groups_missing_sector_as_unknown(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "sector": ""},
            {"code": "1002", "rank": "2", "sector": ""},
            {"code": "2001", "rank": "3", "sector": "Health"},
        ]
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 3, "target_holdings_max": 3},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "sector_cap": {"enabled": True, "mode": "name_count", "max_names_per_group": 1},
            }
        }

        result = select_codes_detailed(scores, holdings={}, config=config)

        self.assertEqual(["1001", "2001"], result.selected_codes)
        self.assertEqual("UNKNOWN", result.blocked_candidates[0].group)

    def test_name_count_sector_cap_resolves_group_field_from_universe_rows(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "sector": "Same"},
            {"code": "1002", "rank": "2", "sector": "Same"},
        ]
        universe_by_code = {
            "1001": {"code": "1001", "market": "Prime"},
            "1002": {"code": "1002", "market": "Standard"},
        }
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 2, "target_holdings_max": 2},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "sector_cap": {
                    "enabled": True,
                    "mode": "name_count",
                    "group_field": "market",
                    "max_names_per_group": 1,
                },
            }
        }

        result = select_codes_detailed(
            scores,
            holdings={},
            config=config,
            universe_by_code=universe_by_code,
        )

        self.assertEqual(["1001", "1002"], result.selected_codes)
        self.assertEqual({}, {item.code: item.group for item in result.blocked_candidates})
        self.assertEqual({"Prime": 1, "Standard": 1}, result.selected_group_counts)

    def test_affordable_lot_filter_skips_expensive_top_ranked_candidate_and_backfills(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "latest_unadjusted_close": "700"},
            {"code": "1002", "rank": "2", "latest_unadjusted_close": "400"},
            {"code": "1003", "rank": "3", "latest_unadjusted_close": "300"},
        ]
        universe_by_code = {
            code: {"code": code, "lot_size": "100"}
            for code in ["1001", "1002", "1003"]
        }
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 2, "target_holdings_max": 2, "lot_size": 100},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "affordable_lot_filter": {
                    "enabled": True,
                    "max_single_lot_weight": 0.8,
                    "cash_buffer_weight": 0.0,
                },
            }
        }

        result = select_codes_detailed(
            scores,
            holdings={},
            config=config,
            universe_by_code=universe_by_code,
            equity=100_000,
        )

        self.assertEqual(["1002", "1003"], result.selected_codes)
        self.assertEqual(["1001", "1002"], result.research_codes)
        self.assertEqual(["1001"], [item.code for item in result.affordability_excluded])
        self.assertEqual("zero_lot_avoided", result.affordability_excluded[0].reason)

    def test_affordable_lot_filter_applies_single_lot_weight_bounds(self) -> None:
        scores = [
            {"code": "1001", "rank": "1", "latest_unadjusted_close": "900"},
            {"code": "1002", "rank": "2", "latest_unadjusted_close": "50"},
            {"code": "1003", "rank": "3", "latest_unadjusted_close": "300"},
        ]
        universe_by_code = {
            code: {"code": code, "lot_size": "100"}
            for code in ["1001", "1002", "1003"]
        }
        config = {
            "portfolio": {
                "executable_portfolio": {"target_holdings_min": 1, "target_holdings_max": 1, "lot_size": 100},
                "buy_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "hold_rule": {"rank_top_pct": 100, "rank_top_n": 100},
                "affordable_lot_filter": {
                    "enabled": True,
                    "max_single_lot_weight": 0.8,
                    "min_single_lot_weight": 0.1,
                    "cash_buffer_weight": 0.0,
                },
            }
        }

        result = select_codes_detailed(
            scores,
            holdings={},
            config=config,
            universe_by_code=universe_by_code,
            equity=100_000,
        )

        self.assertEqual(["1003"], result.selected_codes)
        self.assertEqual(
            ["above_max_single_lot_weight", "below_min_single_lot_weight"],
            [item.reason for item in result.affordability_excluded],
        )

    def test_walkforward_writes_sector_cap_failures_and_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            config_path = temp / "sector_cap.yml"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_text = config_text.replace("  sector_cap:\n    enabled: false", "  sector_cap:\n    enabled: true", 1)
            config_text = config_text.replace("max_names_per_group: 9", "max_names_per_group: 1", 1)
            config_path.write_text(config_text, encoding="utf-8")
            write_csv(
                prices,
                [
                    {
                        "date": day,
                        "code": code,
                        "unadjusted_open": 100,
                        "unadjusted_close": 100,
                        "adjusted_close": 100,
                        "trading_value": 10_000_000,
                        "price_limit_flag": "false",
                    }
                    for day in ["2026-01-31", "2026-02-01"]
                    for code in ["1001", "1002", "1003"]
                ],
                ["date", "code", "unadjusted_open", "unadjusted_close", "adjusted_close", "trading_value", "price_limit_flag"],
            )
            write_csv(
                listings,
                [
                    {
                        "code": code,
                        "listed_date": "2020-01-01",
                        "delisted_date": "",
                    }
                    for code in ["1001", "1002", "1003"]
                ],
                ["code", "listed_date", "delisted_date"],
            )
            write_csv(fundamentals, [], ["code"])

            original_argv = sys.argv[:]
            original_run_stages = run_qvm_walkforward.run_stages

            def fake_run_stages(args, rebalance_date):
                suffix = rebalance_date.strftime("%Y%m%d")
                stage = temp / "stage"
                universe = stage / f"universe_{suffix}.csv"
                factors = stage / f"factors_{suffix}.csv"
                scores = stage / f"scores_{suffix}.csv"
                write_csv(
                    universe,
                    [
                        {"code": "1001", "sector": "Tech", "lot_size": "100", "median_60d_trading_value": "10000000"},
                        {"code": "1002", "sector": "Tech", "lot_size": "100", "median_60d_trading_value": "10000000"},
                        {"code": "1003", "sector": "Tech", "lot_size": "100", "median_60d_trading_value": "10000000"},
                    ],
                    ["code", "sector", "lot_size", "median_60d_trading_value"],
                )
                write_csv(factors, [{"code": "1001"}, {"code": "1002"}, {"code": "1003"}], ["code"])
                write_csv(
                    scores,
                    [
                        {"code": "1001", "rank": "1", "sector": "Tech", "latest_unadjusted_close": "100"},
                        {"code": "1002", "rank": "2", "sector": "Tech", "latest_unadjusted_close": "100"},
                        {"code": "1003", "rank": "3", "sector": "Tech", "latest_unadjusted_close": "100"},
                    ],
                    ["code", "rank", "sector", "latest_unadjusted_close"],
                )
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
                    "2026-01-31",
                    "--frequency",
                    "monthly",
                    "--execution-price",
                    "next_open",
                    "--capital-jpy",
                    "100000",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                    "--run-label",
                    "sectorcap_test",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_summary_sectorcap_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                summary = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_failure_cases_sectorcap_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                failures = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_sector_exposure_sectorcap_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                exposures = list(csv.DictReader(file))

            self.assertEqual("True", summary[-1]["sector_cap_enabled"])
            self.assertEqual("2026-02-01", summary[-1]["last_execution_date"])
            self.assertEqual("2", summary[-1]["sector_cap_unfilled_slots"])
            self.assertIn("sector_cap_unfilled_target", {row["failure_type"] for row in failures})
            self.assertEqual("Tech", exposures[0]["group"])
            self.assertEqual("1", exposures[0]["selected_count"])

    def test_walkforward_affordable_lot_filter_reports_and_avoids_zero_lot_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            config_path = temp / "affordable_lot.yml"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_text = config_text.replace("  affordable_lot_filter:\n    enabled: false", "  affordable_lot_filter:\n    enabled: true", 1)
            config_text = config_text.replace("max_single_lot_weight: 0.05", "max_single_lot_weight: 0.8", 1)
            config_text = config_text.replace("cash_buffer_weight: 0.02", "cash_buffer_weight: 0.1", 1)
            config_text = config_text.replace("spread_multiplier: 0.5", "spread_multiplier: 0.0", 1)
            config_text += "\nreporting:\n  execution_diagnostics:\n    enabled: true\n    high_cash_threshold: 0.30\n"
            config_path.write_text(config_text, encoding="utf-8")
            write_csv(
                prices,
                [
                    {"date": "2026-01-31", "code": "1001", "unadjusted_open": 460, "unadjusted_close": 460, "adjusted_close": 460, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-01-31", "code": "1002", "unadjusted_open": 400, "unadjusted_close": 400, "adjusted_close": 400, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-01-31", "code": "1003", "unadjusted_open": 300, "unadjusted_close": 300, "adjusted_close": 300, "trading_value": 10_000_000, "price_limit_flag": "false"},
                ],
                ["date", "code", "unadjusted_open", "unadjusted_close", "adjusted_close", "trading_value", "price_limit_flag"],
            )
            write_csv(
                listings,
                [
                    {"code": code, "listed_date": "2020-01-01", "delisted_date": ""}
                    for code in ["1001", "1002", "1003"]
                ],
                ["code", "listed_date", "delisted_date"],
            )
            write_csv(fundamentals, [], ["code"])

            original_argv = sys.argv[:]
            original_run_stages = run_qvm_walkforward.run_stages

            def fake_run_stages(args, rebalance_date):
                suffix = rebalance_date.strftime("%Y%m%d")
                stage = temp / "stage"
                universe = stage / f"universe_{suffix}.csv"
                factors = stage / f"factors_{suffix}.csv"
                scores = stage / f"scores_{suffix}.csv"
                write_csv(
                    universe,
                    [
                        {"code": "1001", "lot_size": "100", "median_60d_trading_value": "10000000"},
                        {"code": "1002", "lot_size": "100", "median_60d_trading_value": "10000000"},
                        {"code": "1003", "lot_size": "100", "median_60d_trading_value": "10000000"},
                    ],
                    ["code", "lot_size", "median_60d_trading_value"],
                )
                write_csv(factors, [{"code": "1001"}, {"code": "1002"}, {"code": "1003"}], ["code"])
                write_csv(
                    scores,
                    [
                        {"code": "1001", "rank": "1", "latest_unadjusted_close": "460"},
                        {"code": "1002", "rank": "2", "latest_unadjusted_close": "400"},
                        {"code": "1003", "rank": "3", "latest_unadjusted_close": "300"},
                    ],
                    ["code", "rank", "latest_unadjusted_close"],
                )
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
                    "2026-01-31",
                    "--frequency",
                    "monthly",
                    "--execution-price",
                    "rebalance_close",
                    "--cost-scenario",
                    "optimistic",
                    "--capital-jpy",
                    "100000",
                    "--target-holdings",
                    "2",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                    "--run-label",
                    "affordable_lot_test",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_summary_affordable_lot_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                summary = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_failure_cases_affordable_lot_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                failures = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_trades_affordable_lot_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                trades = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_execution_diagnostics_affordable_lot_test_202601_202601.csv").open(
                "r", encoding="utf-8", newline=""
            ) as file:
                execution_diagnostics = list(csv.DictReader(file))

            self.assertEqual("True", summary[-1]["affordable_lot_filter_enabled"])
            self.assertEqual("1", summary[-1]["affordability_excluded"])
            self.assertEqual("1", summary[-1]["zero_lot_avoided"])
            self.assertEqual("True", summary[-1]["execution_diagnostics_enabled"])
            self.assertEqual("1", summary[-1]["selected_but_unaffordable_count"])
            self.assertEqual("1", summary[-1]["skipped_due_to_affordable_lot_count"])
            self.assertEqual("True", summary[-1]["small_account_path_dependency_flag"])
            self.assertEqual("0", summary[-1]["zero_lot_targets"])
            self.assertEqual("2", summary[-1]["selected_count"])
            self.assertEqual(1, len(execution_diagnostics))
            self.assertEqual("1", execution_diagnostics[0]["selected_but_unaffordable_count"])
            self.assertEqual("True", execution_diagnostics[0]["small_account_path_dependency_flag"])
            self.assertNotEqual("", execution_diagnostics[0]["average_cash_weight"])
            self.assertIn("buy_turnover", execution_diagnostics[0])
            self.assertEqual({"1002", "1003"}, {row["code"] for row in trades if row["side"] == "BUY"})
            failure_types = {row["failure_type"] for row in failures}
            self.assertIn("affordability_excluded", failure_types)
            self.assertIn("zero_lot_avoided", failure_types)
            self.assertIn("cash_drag", failure_types)

    def test_sector_exposure_actual_violation_uses_signal_date(self) -> None:
        result = run_qvm_walkforward.SelectionResult(
            selected_codes=["1001"],
            research_codes=["1001"],
            target_count=1,
            sector_cap=run_qvm_walkforward.SectorCapConfig(
                enabled=True,
                mode="name_count",
                group_field="sector",
                max_names_per_group=1,
            ),
            affordable_lot_filter=run_qvm_walkforward.AffordableLotFilterConfig(),
            blocked_candidates=[],
            affordability_excluded=[],
            unfilled_slots=0,
            selected_group_counts={"Tech": 1},
        )
        price_index = {
            "1001": [run_qvm_walkforward.PricePoint(date(2026, 1, 31), 100.0, 100.0, 100.0, 1_000_000.0, False)],
            "1002": [run_qvm_walkforward.PricePoint(date(2026, 1, 31), 100.0, 100.0, 100.0, 1_000_000.0, False)],
        }

        _exposures, _selected_weight, _actual_weight, violation_count, violations = run_qvm_walkforward.sector_exposure_rows(
            signal_date=date(2026, 1, 31),
            valuation_date=date(2026, 1, 31),
            result=result,
            targets={"1001": 100},
            holdings={"1001": 100.0, "1002": 100.0},
            universe_by_code={
                "1001": {"code": "1001", "sector": "Tech"},
                "1002": {"code": "1002", "sector": "Tech"},
            },
            price_index=price_index,
            pre_equity=20_000.0,
            after_equity=20_000.0,
        )

        self.assertEqual(1, violation_count)
        self.assertEqual("sector_cap_actual_violation", violations[0]["failure_type"])
        self.assertEqual(date(2026, 1, 31), violations[0]["date"])

    def test_next_open_marks_equity_on_fill_date_without_prefill_return(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            config_path = temp / "next_open.yml"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_text = config_text.replace("spread_multiplier: 0.5", "spread_multiplier: 0.0", 1)
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
                        "date": "2026-02-01",
                        "code": "1001",
                        "unadjusted_open": 200,
                        "unadjusted_close": 200,
                        "adjusted_close": 200,
                        "trading_value": 10_000_000,
                        "price_limit_flag": "false",
                    },
                ],
                ["date", "code", "unadjusted_open", "unadjusted_close", "adjusted_close", "trading_value", "price_limit_flag"],
            )
            write_csv(listings, [{"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""}], ["code", "listed_date", "delisted_date"])
            write_csv(fundamentals, [], ["code"])

            original_argv = sys.argv[:]
            original_run_stages = run_qvm_walkforward.run_stages

            def fake_run_stages(args, rebalance_date):
                suffix = rebalance_date.strftime("%Y%m%d")
                stage = temp / "stage"
                universe = stage / f"universe_{suffix}.csv"
                factors = stage / f"factors_{suffix}.csv"
                scores = stage / f"scores_{suffix}.csv"
                write_csv(universe, [{"code": "1001", "lot_size": "100", "median_60d_trading_value": ""}], ["code", "lot_size", "median_60d_trading_value"])
                write_csv(factors, [{"code": "1001"}], ["code"])
                write_csv(scores, [{"code": "1001", "rank": "1", "latest_unadjusted_close": "100"}], ["code", "rank", "latest_unadjusted_close"])
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
                    "2026-01-31",
                    "--frequency",
                    "monthly",
                    "--execution-price",
                    "next_open",
                    "--cost-scenario",
                    "optimistic",
                    "--capital-jpy",
                    "20000",
                    "--target-holdings",
                    "1",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                    "--run-label",
                    "next_open",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_summary_next_open_202601_202601.csv").open("r", encoding="utf-8", newline="") as file:
                summary = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_trades_next_open_202601_202601.csv").open("r", encoding="utf-8", newline="") as file:
                trades = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_holdings_next_open_202601_202601.csv").open("r", encoding="utf-8", newline="") as file:
                holdings = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_equity_next_open_202601_202601.csv").open("r", encoding="utf-8", newline="") as file:
                equity = list(csv.DictReader(file))

            self.assertEqual("2026-02-01", trades[0]["execution_date"])
            self.assertEqual("200.0", trades[0]["price"])
            self.assertAlmostEqual(20000.0, float(summary[-1]["portfolio_equity_after_cost"]))
            self.assertEqual("2026-02-01", summary[-1]["last_execution_date"])
            self.assertEqual("1", summary[-1]["execution_lag_days"])
            self.assertEqual("2026-02-01", holdings[0]["date"])
            self.assertEqual("2026-02-01", equity[0]["date"])

    def test_next_close_uses_next_day_close_price(self) -> None:
        points = {
            "1001": [
                run_qvm_walkforward.PricePoint(date(2026, 1, 31), 99.0, 100.0, 100.0, 1_000_000.0, False),
                run_qvm_walkforward.PricePoint(date(2026, 2, 1), 150.0, 210.0, 210.0, 1_000_000.0, False),
            ]
        }

        fill = run_qvm_walkforward.execution_point(points, [date(2026, 1, 31), date(2026, 2, 1)], "1001", date(2026, 1, 31), "next_close")

        self.assertIsNotNone(fill)
        self.assertEqual(date(2026, 2, 1), fill.date)
        self.assertEqual(210.0, run_qvm_walkforward.execution_price(fill, "next_close"))

    def run_next_open_single_order_case(
        self,
        price_rows: list[dict[str, object]],
        run_label: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            write_csv(
                prices,
                price_rows,
                [
                    "date",
                    "code",
                    "unadjusted_open",
                    "unadjusted_close",
                    "adjusted_close",
                    "volume",
                    "trading_value",
                    "tradable_flag",
                    "price_limit_flag",
                ],
            )
            write_csv(listings, [{"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""}], ["code", "listed_date", "delisted_date"])
            write_csv(fundamentals, [], ["code"])

            original_argv = sys.argv[:]
            original_run_stages = run_qvm_walkforward.run_stages

            def fake_run_stages(args, rebalance_date):
                suffix = rebalance_date.strftime("%Y%m%d")
                stage = temp / "stage"
                universe = stage / f"universe_{suffix}.csv"
                factors = stage / f"factors_{suffix}.csv"
                scores = stage / f"scores_{suffix}.csv"
                write_csv(universe, [{"code": "1001", "lot_size": "100", "median_60d_trading_value": ""}], ["code", "lot_size", "median_60d_trading_value"])
                write_csv(factors, [{"code": "1001"}], ["code"])
                write_csv(scores, [{"code": "1001", "rank": "1", "latest_unadjusted_close": "100"}], ["code", "rank", "latest_unadjusted_close"])
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
                    "next_open",
                    "--capital-jpy",
                    "20000",
                    "--target-holdings",
                    "1",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                    "--run-label",
                    run_label,
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / f"qvm_walkforward_summary_{run_label}_202601_202601.csv").open("r", encoding="utf-8", newline="") as file:
                summary = list(csv.DictReader(file))
            with (out_dir / f"qvm_walkforward_failure_cases_{run_label}_202601_202601.csv").open("r", encoding="utf-8", newline="") as file:
                failures = list(csv.DictReader(file))
            with (out_dir / f"qvm_walkforward_trades_{run_label}_202601_202601.csv").open("r", encoding="utf-8", newline="") as file:
                trades = list(csv.DictReader(file))
            return summary, failures, trades

    def test_next_open_missing_execution_price_row_skips_order(self) -> None:
        summary, failures, trades = self.run_next_open_single_order_case(
            [
                {"date": "2026-01-31", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 100, "volume": 1000, "trading_value": 10_000_000, "tradable_flag": "true", "price_limit_flag": "false"},
                {"date": "2026-02-01", "code": "9999", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 100, "volume": 1000, "trading_value": 10_000_000, "tradable_flag": "true", "price_limit_flag": "false"},
            ],
            "missing_exec_row",
        )

        self.assertEqual("1", summary[-1]["missing_execution_price_count"])
        self.assertEqual("1", summary[-1]["missing_execution_price_row_count"])
        self.assertEqual("0", summary[-1]["execution_date_not_tradable_count"])
        self.assertEqual("0", summary[-1]["execution_price_unavailable_on_execution_date_count"])
        self.assertEqual("0", summary[-1]["filled_order_count"])
        self.assertIn("missing_execution_price_row", {row["failure_type"] for row in failures})
        self.assertEqual("missing_execution_price_row", trades[0]["constraint_reason"])
        self.assertIn("has_price_row=False", failures[0]["detail"])

    def test_next_open_not_tradable_execution_date_skips_order(self) -> None:
        summary, failures, trades = self.run_next_open_single_order_case(
            [
                {"date": "2026-01-31", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 100, "volume": 1000, "trading_value": 10_000_000, "tradable_flag": "true", "price_limit_flag": "false"},
                {"date": "2026-02-01", "code": "1001", "unadjusted_open": "", "unadjusted_close": "", "adjusted_close": "", "volume": "", "trading_value": "", "tradable_flag": "false", "price_limit_flag": "false"},
            ],
            "not_tradable_exec",
        )

        self.assertEqual("1", summary[-1]["missing_execution_price_count"])
        self.assertEqual("0", summary[-1]["missing_execution_price_row_count"])
        self.assertEqual("1", summary[-1]["execution_date_not_tradable_count"])
        self.assertEqual("0", summary[-1]["execution_price_unavailable_on_execution_date_count"])
        self.assertEqual("0", summary[-1]["filled_order_count"])
        self.assertIn("execution_date_not_tradable", {row["failure_type"] for row in failures})
        self.assertEqual("execution_date_not_tradable", trades[0]["constraint_reason"])
        self.assertIn("has_price_row=True", failures[0]["detail"])
        self.assertIn("tradable_flag=False", failures[0]["detail"])
        self.assertIn("has_open=False", failures[0]["detail"])
        self.assertIn("has_close=False", failures[0]["detail"])
        self.assertIn("has_volume=False", failures[0]["detail"])
        self.assertIn("has_trading_value=False", failures[0]["detail"])

    def test_next_open_missing_open_skips_order_without_using_close(self) -> None:
        summary, failures, trades = self.run_next_open_single_order_case(
            [
                {"date": "2026-01-31", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 100, "volume": 1000, "trading_value": 10_000_000, "tradable_flag": "true", "price_limit_flag": "false"},
                {"date": "2026-02-01", "code": "1001", "unadjusted_open": "", "unadjusted_close": 100, "adjusted_close": 100, "volume": 1000, "trading_value": 10_000_000, "tradable_flag": "true", "price_limit_flag": "false"},
            ],
            "missing_exec_open",
        )

        self.assertEqual("1", summary[-1]["missing_execution_price_count"])
        self.assertEqual("0", summary[-1]["missing_execution_price_row_count"])
        self.assertEqual("0", summary[-1]["execution_date_not_tradable_count"])
        self.assertEqual("1", summary[-1]["execution_price_unavailable_on_execution_date_count"])
        self.assertEqual("0", summary[-1]["filled_order_count"])
        self.assertIn("execution_price_unavailable_on_execution_date", {row["failure_type"] for row in failures})
        self.assertEqual("execution_price_unavailable_on_execution_date", trades[0]["constraint_reason"])
        self.assertIn("has_open=False", failures[0]["detail"])
        self.assertIn("has_close=True", failures[0]["detail"])

    def test_next_close_sell_realized_gain_uses_execution_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            config_path = temp / "next_close_tax.yml"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_text = config_text.replace("spread_multiplier: 0.5", "spread_multiplier: 0.0", 1)
            config_path.write_text(config_text, encoding="utf-8")
            write_csv(
                prices,
                [
                    {"date": "2026-01-31", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 100, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-02-01", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 100, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-02-28", "code": "1001", "unadjusted_open": 120, "unadjusted_close": 120, "adjusted_close": 120, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-03-01", "code": "1001", "unadjusted_open": 150, "unadjusted_close": 150, "adjusted_close": 150, "trading_value": 10_000_000, "price_limit_flag": "false"},
                ],
                ["date", "code", "unadjusted_open", "unadjusted_close", "adjusted_close", "trading_value", "price_limit_flag"],
            )
            write_csv(listings, [{"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""}], ["code", "listed_date", "delisted_date"])
            write_csv(fundamentals, [], ["code"])

            original_argv = sys.argv[:]
            original_run_stages = run_qvm_walkforward.run_stages

            def fake_run_stages(args, rebalance_date):
                suffix = rebalance_date.strftime("%Y%m%d")
                stage = temp / "stage"
                universe = stage / f"universe_{suffix}.csv"
                factors = stage / f"factors_{suffix}.csv"
                scores = stage / f"scores_{suffix}.csv"
                write_csv(universe, [{"code": "1001", "lot_size": "100", "median_60d_trading_value": ""}], ["code", "lot_size", "median_60d_trading_value"])
                write_csv(factors, [{"code": "1001"}], ["code"])
                rows = [{"code": "1001", "rank": "1", "latest_unadjusted_close": "100"}] if rebalance_date == date(2026, 1, 31) else []
                write_csv(scores, rows, ["code", "rank", "latest_unadjusted_close"])
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
                    "next_close",
                    "--cost-scenario",
                    "optimistic",
                    "--capital-jpy",
                    "10000",
                    "--target-holdings",
                    "1",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                    "--run-label",
                    "next_close_tax",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_trades_next_close_tax_202601_202602.csv").open("r", encoding="utf-8", newline="") as file:
                trades = list(csv.DictReader(file))
            sell = [row for row in trades if row["side"] == "SELL"][0]

            self.assertEqual("2026-03-01", sell["execution_date"])
            self.assertGreater(float(sell["realized_gain"]), 0)
            self.assertGreater(float(sell["estimated_tax"]), 0)

    def test_next_close_liquidation_uses_fill_date_share_count_after_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            listings = temp / "listings.csv"
            fundamentals = temp / "fundamentals.csv"
            config_path = temp / "next_close_split_sell.yml"
            out_dir = temp / "out"
            report_dir = temp / "reports"

            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_text = config_text.replace("spread_multiplier: 0.5", "spread_multiplier: 0.0", 1)
            config_path.write_text(config_text, encoding="utf-8")
            write_csv(
                prices,
                [
                    {"date": "2026-01-31", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 50, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-02-01", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 50, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-02-28", "code": "1001", "unadjusted_open": 100, "unadjusted_close": 100, "adjusted_close": 50, "trading_value": 10_000_000, "price_limit_flag": "false"},
                    {"date": "2026-03-01", "code": "1001", "unadjusted_open": 50, "unadjusted_close": 50, "adjusted_close": 50, "trading_value": 10_000_000, "price_limit_flag": "false"},
                ],
                ["date", "code", "unadjusted_open", "unadjusted_close", "adjusted_close", "trading_value", "price_limit_flag"],
            )
            write_csv(listings, [{"code": "1001", "listed_date": "2020-01-01", "delisted_date": ""}], ["code", "listed_date", "delisted_date"])
            write_csv(fundamentals, [], ["code"])

            original_argv = sys.argv[:]
            original_run_stages = run_qvm_walkforward.run_stages

            def fake_run_stages(args, rebalance_date):
                suffix = rebalance_date.strftime("%Y%m%d")
                stage = temp / "stage"
                universe = stage / f"universe_{suffix}.csv"
                factors = stage / f"factors_{suffix}.csv"
                scores = stage / f"scores_{suffix}.csv"
                write_csv(universe, [{"code": "1001", "lot_size": "100", "median_60d_trading_value": ""}], ["code", "lot_size", "median_60d_trading_value"])
                write_csv(factors, [{"code": "1001"}], ["code"])
                rows = [{"code": "1001", "rank": "1", "latest_unadjusted_close": "100"}] if rebalance_date == date(2026, 1, 31) else []
                write_csv(scores, rows, ["code", "rank", "latest_unadjusted_close"])
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
                    "next_close",
                    "--cost-scenario",
                    "optimistic",
                    "--capital-jpy",
                    "10000",
                    "--target-holdings",
                    "1",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                    "--run-label",
                    "next_close_split_sell",
                ]

                self.assertEqual(0, run_qvm_walkforward.main())
            finally:
                run_qvm_walkforward.run_stages = original_run_stages
                sys.argv = original_argv

            with (out_dir / "qvm_walkforward_summary_next_close_split_sell_202601_202602.csv").open("r", encoding="utf-8", newline="") as file:
                summary = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_trades_next_close_split_sell_202601_202602.csv").open("r", encoding="utf-8", newline="") as file:
                trades = list(csv.DictReader(file))
            with (out_dir / "qvm_walkforward_holdings_next_close_split_sell_202601_202602.csv").open("r", encoding="utf-8", newline="") as file:
                holdings = list(csv.DictReader(file))

            sell = [row for row in trades if row["side"] == "SELL"][0]

            self.assertEqual("2026-03-01", sell["execution_date"])
            self.assertEqual("-200", sell["filled_shares"])
            self.assertEqual("0", summary[-1]["holdings_count"])
            self.assertAlmostEqual(10000.0, float(summary[-1]["cash"]))
            self.assertEqual([], [row for row in holdings if row["date"] == "2026-03-01"])

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

    def test_build_targets_does_not_execute_stale_research_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            scores = temp / "scores.csv"
            universe = temp / "universe.csv"
            out_dir = temp / "targets"
            write_csv(
                scores,
                [
                    {
                        "rebalance_date": "2026-03-31",
                        "rank": "1",
                        "code": "1001",
                        "name": "Stale",
                        "sector": "Tech",
                        "latest_unadjusted_close": "1000",
                        "qvm_score": "1.0",
                    }
                ],
                [
                    "rebalance_date",
                    "rank",
                    "code",
                    "name",
                    "sector",
                    "latest_unadjusted_close",
                    "qvm_score",
                ],
            )
            write_csv(
                universe,
                [
                    {
                        "code": "1001",
                        "lot_size": "100",
                        "latest_unadjusted_close": "1000",
                        "rebalance_price_available": "false",
                        "median_60d_trading_value": "10000000",
                    }
                ],
                [
                    "code",
                    "lot_size",
                    "latest_unadjusted_close",
                    "rebalance_price_available",
                    "median_60d_trading_value",
                ],
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_targets.py"),
                    "--config",
                    str(ROOT / "configs" / "qvm_v0_1.example.yml"),
                    "--rebalance-date",
                    "2026-03-31",
                    "--scores",
                    str(scores),
                    "--universe",
                    str(universe),
                    "--target-count",
                    "1",
                    "--out-dir",
                    str(out_dir),
                    "--no-manifest",
                ],
                cwd=ROOT,
                check=True,
            )

            with (out_dir / "targets_202603.csv").open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual("0", rows[0]["target_shares"])
            self.assertEqual("no_rebalance_price", rows[0]["target_constraint_reason"])

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
