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
from build_group_allocation_panel import build_panel as build_allocation_panel  # noqa: E402
from build_group_signal_panel import (  # noqa: E402
    aggregate_values,
    benchmark_returns,
    build_panel as build_signal_panel,
    external_rows_by_group,
    latest_factor_rows,
    load_basket_rows,
    parse_aggregation,
)
from analyze_group_allocation_attribution import build_panel as build_group_attribution_panel  # noqa: E402
from expand_group_allocation_to_security_targets import build_panel as build_lookthrough_panel, load_prices as load_lookthrough_prices  # noqa: E402
from group_beta_common import fmt, load_group_membership_panel, memberships_for_date  # noqa: E402
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
                {"date": "2026-01-01", "code": "1001", "adjusted_close": "10", "trading_value": "100", "volume": "100", "market_cap": "100"},
                {"date": "2026-01-01", "code": "1002", "adjusted_close": "20", "trading_value": "300", "volume": "300", "market_cap": "300"},
                {"date": "2026-01-01", "code": "1003", "adjusted_close": "30", "trading_value": "100", "volume": "100", "market_cap": "100"},
                {"date": "2026-01-02", "code": "1001", "adjusted_close": "11", "trading_value": "100", "volume": "100", "market_cap": "100"},
                {"date": "2026-01-02", "code": "1002", "adjusted_close": "22", "trading_value": "300", "volume": "300", "market_cap": "300"},
                {"date": "2026-01-02", "code": "1003", "adjusted_close": "33", "trading_value": "100", "volume": "100", "market_cap": "100"},
                {"date": "2026-01-03", "code": "1001", "adjusted_close": "12.1", "trading_value": "100", "volume": "100", "market_cap": "100"},
                {"date": "2026-01-03", "code": "1002", "adjusted_close": "22", "trading_value": "300", "volume": "300", "market_cap": "300"},
                {"date": "2026-01-03", "code": "1003", "adjusted_close": "36.3", "trading_value": "100", "volume": "100", "market_cap": "100"},
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
            volume_rows = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1)],
                input_format="csv",
                weighting_mode="volume_weight",
            )
            equal_by_date = {row["date"].isoformat(): row for row in equal_rows if row["group_id"] == "tech"}
            liq_by_date = {row["date"].isoformat(): row for row in liquidity_rows if row["group_id"] == "tech"}

            self.assertAlmostEqual(0.1, float(equal_by_date["2026-01-02"]["basket_return"]))
            self.assertAlmostEqual(0.1, float(liq_by_date["2026-01-02"]["basket_return"]))
            self.assertAlmostEqual(0.75, float(liq_by_date["2026-01-01"]["top_constituent_weight"]))
            self.assertAlmostEqual(0.75, float(market_cap_rows[0]["top_constituent_weight"]))
            self.assertAlmostEqual(0.75, float(custom_rows[0]["top_constituent_weight"]))
            self.assertAlmostEqual(0.75, float(volume_rows[0]["top_constituent_weight"]))
            self.assertEqual(2, equal_by_date["2026-01-03"]["constituent_count"])
            self.assertGreater(float(equal_by_date["2026-01-03"]["turnover"]), 0)

    def test_group_basket_treats_stale_current_price_as_missing_return(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            membership = temp / "membership.csv"
            write_rows(
                membership,
                [
                    {"rebalance_date": "2026-01-01", "code": "1001", "group_type": "sector", "group_id": "tech", "membership_weight": "1"},
                ],
            )
            prices = [
                {"date": "2026-01-01", "code": "1001", "adjusted_close": "10", "trading_value": "100"},
            ]

            rows = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1), date(2026, 1, 2)],
                input_format="csv",
                weighting_mode="equal_weight",
            )
            second = rows[1]

            self.assertIsNone(second["basket_return"])
            self.assertEqual(0.0, second["coverage"])
            self.assertEqual(1, second["missing_return_count"])

    def test_liquidity_weight_does_not_mix_trading_value_with_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            membership = temp / "membership.csv"
            write_rows(
                membership,
                [
                    {"rebalance_date": "2026-01-01", "code": "1001", "group_type": "sector", "group_id": "tech", "membership_weight": "1"},
                    {"rebalance_date": "2026-01-01", "code": "1002", "group_type": "sector", "group_id": "tech", "membership_weight": "1"},
                ],
            )
            prices = [
                {"date": "2026-01-01", "code": "1001", "adjusted_close": "10", "trading_value": "100", "volume": "1"},
                {"date": "2026-01-01", "code": "1002", "adjusted_close": "20", "trading_value": "", "volume": "300"},
            ]

            liquidity = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1)],
                input_format="csv",
                weighting_mode="liquidity_weight",
            )
            volume = build_basket_panel(
                prices,
                membership,
                dates=[date(2026, 1, 1)],
                input_format="csv",
                weighting_mode="volume_weight",
            )

            self.assertEqual(1, liquidity[0]["constituent_count"])
            self.assertAlmostEqual(1.0, liquidity[0]["top_constituent_weight"])
            self.assertAlmostEqual(300 / 301, volume[0]["top_constituent_weight"])

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

    def test_group_signal_preserves_zero_benchmark_return_from_parquet(self) -> None:
        pd = __import__("pandas")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "benchmark.parquet"
            pd.DataFrame({"date": ["2026-01-02", "2026-01-03"], "return": [0.0, 0.05]}).to_parquet(path, index=False)

            returns = benchmark_returns(path, "auto")

            self.assertIn(date(2026, 1, 2), returns)
            self.assertEqual(0.0, returns[date(2026, 1, 2)])
            self.assertEqual(0.05, returns[date(2026, 1, 3)])

    def test_group_signal_validates_percentile_bounds_and_formats_nan_blank(self) -> None:
        with self.assertRaisesRegex(ValueError, "Percentile aggregation"):
            parse_aggregation("book_to_market:p101")

        self.assertAlmostEqual(15.0, float(aggregate_values([(10.0, 1), (20.0, 1)], "p50") or 0))
        self.assertEqual("", fmt(float("nan")))

    def test_group_allocation_score_tilt_caps_and_missing_score(self) -> None:
        rebalance = date(2026, 2, 28)
        rows = build_allocation_panel(
            {
                rebalance: {
                    ("theme", "theme_a"): {"group_name": "Theme A", "score": "2.0"},
                    ("theme", "theme_b"): {"group_name": "Theme B", "score": "-1.0"},
                    ("theme", "theme_c"): {"group_name": "Theme C", "score": ""},
                }
            },
            benchmark_weights_by_date={
                rebalance: {
                    ("theme", "theme_a"): 0.30,
                    ("theme", "theme_b"): 0.30,
                    ("theme", "theme_c"): 0.40,
                }
            },
            score_field="score",
            active_budget=0.20,
            max_active_weight=0.05,
            max_group_weight=0.34,
        )
        by_group = {row["group_id"]: row for row in rows}

        self.assertLessEqual(float(by_group["theme_a"]["active_weight"]), 0.05)
        self.assertAlmostEqual(0.34, float(by_group["theme_a"]["target_weight"]))
        self.assertIn("max_active_weight", by_group["theme_a"]["constraint_reasons"])
        self.assertIn("max_group_weight", by_group["theme_a"]["constraint_reasons"])
        self.assertIn("missing_score", by_group["theme_c"]["constraint_reasons"])
        self.assertIn("excluded_missing_score", by_group["theme_c"]["constraint_reasons"])

    def test_group_allocation_group_type_and_turnover_caps(self) -> None:
        rebalance = date(2026, 2, 28)
        rows = build_allocation_panel(
            {
                rebalance: {
                    ("theme", "theme_a"): {"group_name": "Theme A", "score": "10"},
                    ("theme", "theme_b"): {"group_name": "Theme B", "score": "8"},
                    ("sector", "sector_a"): {"group_name": "Sector A", "score": "1"},
                }
            },
            mode="top_n_equal",
            top_n=3,
            current_weights_by_date={rebalance: {("theme", "theme_a"): 0.0, ("theme", "theme_b"): 0.0, ("sector", "sector_a"): 1.0}},
            group_type_caps={"theme": 0.50},
            max_turnover=0.10,
        )
        by_group = {(row["group_type"], row["group_id"]): row for row in rows}
        turnover = 0.5 * sum(abs(float(row["target_weight"]) - float(row["current_weight"])) for row in rows)

        self.assertLessEqual(turnover, 0.100000001)
        self.assertIn("group_type_cap", by_group[("theme", "theme_a")]["constraint_reasons"])
        self.assertIn("max_turnover", by_group[("theme", "theme_a")]["constraint_reasons"])

    def test_group_allocation_reports_infeasible_final_cap_violations(self) -> None:
        rebalance = date(2026, 2, 28)
        turnover_rows = build_allocation_panel(
            {
                rebalance: {
                    ("theme", "theme_a"): {"group_name": "Theme A", "score": "1"},
                    ("theme", "theme_b"): {"group_name": "Theme B", "score": "0"},
                }
            },
            mode="top_n_equal",
            top_n=1,
            current_weights_by_date={rebalance: {("theme", "theme_a"): 1.0, ("theme", "theme_b"): 0.0}},
            max_group_weight=0.20,
            max_turnover=0.10,
        )
        turnover_by_group = {row["group_id"]: row for row in turnover_rows}

        self.assertGreater(float(turnover_by_group["theme_a"]["target_weight"]), 0.20)
        self.assertEqual("violation", turnover_by_group["theme_a"]["constraint_status"])
        self.assertIn("final_max_group_weight_violation", turnover_by_group["theme_a"]["constraint_reasons"])

        active_rows = build_allocation_panel(
            {
                rebalance: {
                    ("theme", "theme_a"): {"group_name": "Theme A", "score": "1"},
                    ("theme", "theme_b"): {"group_name": "Theme B", "score": ""},
                }
            },
            benchmark_weights_by_date={rebalance: {("theme", "theme_a"): 0.90, ("theme", "theme_b"): 0.10}},
            mode="score_tilt",
            max_active_weight=0.05,
            max_group_weight=0.50,
        )
        active_row = {row["group_id"]: row for row in active_rows}["theme_a"]

        self.assertAlmostEqual(-0.40, float(active_row["active_weight"]))
        self.assertEqual("violation", active_row["constraint_status"])
        self.assertIn("final_max_active_weight_violation", active_row["constraint_reasons"])

    def test_group_allocation_scale_caps_only_tag_changed_rows(self) -> None:
        rebalance = date(2026, 2, 28)
        rows = build_allocation_panel(
            {
                rebalance: {
                    ("theme", "theme_a"): {"group_name": "Theme A", "score": "2"},
                    ("theme", "theme_b"): {"group_name": "Theme B", "score": "1"},
                    ("sector", "sector_a"): {"group_name": "Sector A", "score": "0"},
                }
            },
            benchmark_weights_by_date={rebalance: {("theme", "theme_a"): 0.20, ("theme", "theme_b"): 0.20, ("sector", "sector_a"): 0.60}},
            active_budget=0.60,
            max_total_active_weight=0.20,
            group_type_caps={"theme": 0.60},
        )
        by_group = {(row["group_type"], row["group_id"]): row for row in rows}

        self.assertIn("max_total_active_weight", by_group[("theme", "theme_a")]["constraint_reasons"])
        self.assertNotIn("max_total_active_weight", by_group[("theme", "theme_b")]["constraint_reasons"])
        self.assertNotIn("group_type_cap", by_group[("sector", "sector_a")]["constraint_reasons"])

    def test_group_lookthrough_aggregates_overlapping_memberships_and_caps_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            membership = temp / "membership.csv"
            write_rows(
                membership,
                [
                    {"rebalance_date": "2026-02-28", "code": "1001", "group_type": "theme", "group_id": "theme_a", "group_name": "Theme A", "membership_weight": "1"},
                    {"rebalance_date": "2026-02-28", "code": "1002", "group_type": "theme", "group_id": "theme_a", "group_name": "Theme A", "membership_weight": "1"},
                    {"rebalance_date": "2026-02-28", "code": "1001", "group_type": "theme", "group_id": "theme_b", "group_name": "Theme B", "membership_weight": "1"},
                ],
            )
            allocation = {
                date(2026, 2, 28): {
                    ("theme", "theme_a"): {"target_weight": "0.6"},
                    ("theme", "theme_b"): {"target_weight": "0.4"},
                }
            }

            rows = build_lookthrough_panel(
                allocation,
                membership,
                rebalance_dates=[date(2026, 2, 28)],
                input_format="csv",
                weighting_mode="equal_weight",
                single_name_cap=0.50,
            )
            by_code = {row["code"]: row for row in rows}

            self.assertAlmostEqual(0.50, float(by_code["1001"]["target_weight"]))
            self.assertEqual(2, by_code["1001"]["source_group_count"])
            self.assertIn("single_name_cap", by_code["1001"]["lookthrough_constraint_reasons"])
            self.assertAlmostEqual(0.30, float(by_code["1002"]["target_weight"]))

    def test_group_lookthrough_custom_weight_does_not_require_prices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            membership = temp / "membership.csv"
            write_rows(
                membership,
                [
                    {"rebalance_date": "2026-02-28", "code": "1001", "group_type": "theme", "group_id": "theme_a", "membership_weight": "1", "custom_weight": "3"},
                    {"rebalance_date": "2026-02-28", "code": "1002", "group_type": "theme", "group_id": "theme_a", "membership_weight": "1", "custom_weight": "1"},
                ],
            )

            rows = build_lookthrough_panel(
                {date(2026, 2, 28): {("theme", "theme_a"): {"target_weight": "0.8"}}},
                membership,
                rebalance_dates=[date(2026, 2, 28)],
                input_format="csv",
                weighting_mode="custom_weight",
                custom_weight_field="custom_weight",
                price_index=load_lookthrough_prices(None, "csv", "custom_weight"),
            )
            by_code = {row["code"]: row for row in rows}

            self.assertAlmostEqual(0.6, float(by_code["1001"]["target_weight"]))
            self.assertAlmostEqual(0.2, float(by_code["1002"]["target_weight"]))

    def test_group_allocation_attribution_uses_prior_allocation(self) -> None:
        rows = build_group_attribution_panel(
            {
                date(2026, 1, 31): {
                    ("theme", "theme_a"): {"group_name": "Theme A", "target_weight": "0.6", "benchmark_weight": "0.5", "active_weight": "0.1"},
                    ("theme", "theme_b"): {"group_name": "Theme B", "target_weight": "0.4", "benchmark_weight": "0.5", "active_weight": "-0.1"},
                }
            },
            {
                date(2026, 2, 28): {
                    ("theme", "theme_a"): {"group_name": "Theme A", "basket_return": "0.10"},
                    ("theme", "theme_b"): {"group_name": "Theme B", "basket_return": "-0.20"},
                }
            },
        )
        by_group = {row["group_id"]: row for row in rows}

        self.assertEqual(date(2026, 1, 31), by_group["theme_a"]["allocation_date"])
        self.assertAlmostEqual(0.06, float(by_group["theme_a"]["portfolio_contribution"]))
        self.assertAlmostEqual(0.05, float(by_group["theme_a"]["benchmark_contribution"]))
        self.assertAlmostEqual(0.01, float(by_group["theme_a"]["active_contribution"]))
        self.assertAlmostEqual(-0.08, float(by_group["theme_b"]["portfolio_contribution"]))
        self.assertAlmostEqual(0.02, float(by_group["theme_b"]["active_contribution"]))


if __name__ == "__main__":
    unittest.main()
