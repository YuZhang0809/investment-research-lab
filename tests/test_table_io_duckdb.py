from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from duckdb_query import parquet_scan, query  # noqa: E402
from analyze_factor_forward_returns import factor_files  # noqa: E402
from build_factors import build_factors  # noqa: E402
from build_scores import STRATEGY_VERSION_CHOICES, build_scores  # noqa: E402
from research_common import read_csv, read_table, write_table  # noqa: E402


class TableIODuckDBTest(unittest.TestCase):
    def test_csv_and_parquet_round_trip_through_unified_io(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            csv_path = temp / "fixture.csv"
            parquet_path = temp / "fixture.parquet"
            dataset_path = temp / "fixture_dataset"

            write_table(
                [
                    {"date": "2026-03-31", "code": "0001", "value": "10.5"},
                    {"date": "2026-03-31", "code": "0002", "value": "20.0"},
                ],
                csv_path,
                format="csv",
                fieldnames=["date", "code", "value"],
            )
            csv_frame = read_table(csv_path)
            self.assertEqual(["0001", "0002"], list(csv_frame["code"]))

            rows = read_csv(csv_path)
            write_table(rows, parquet_path, format="parquet")
            self.assertEqual(rows, read_csv(parquet_path))

            write_table(rows, dataset_path, format="parquet")
            self.assertEqual(rows, read_csv(dataset_path))

    def test_duckdb_scans_parquet_without_importing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            parquet_path = temp / "scores.parquet"
            write_table(
                [
                    {"code": "0001", "qvm_score": "1.25"},
                    {"code": "0002", "qvm_score": "-0.50"},
                ],
                parquet_path,
                format="parquet",
            )

            frame = query(
                f"""
                select code
                from {parquet_scan(parquet_path)}
                where cast(qvm_score as double) > 0
                """
            )

            self.assertEqual(["0001"], list(frame["code"]))

    def test_factor_file_discovery_includes_csv_and_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            (temp / "factors_202603.csv").write_text("code\n0001\n", encoding="utf-8")
            write_table([{"code": "0002"}], temp / "factors_202603.parquet", format="parquet")
            (temp / "factors_202604.csv").write_text("code\n0003\n", encoding="utf-8")

            files = factor_files(temp, date(2026, 3, 1), date(2026, 4, 30))

            self.assertEqual(
                ["factors_202603.parquet", "factors_202604.csv"],
                [path.name for path in files],
            )

    def test_supported_strategy_versions_produce_rankable_scores(self) -> None:
        config = {
            "strategy": {
                "scoring": {
                    "mode": "weighted_groups",
                    "weights": {"quality": 0.4, "value": 0.4, "momentum": 0.2},
                },
                "filters": [],
            },
            "factors": {
                "winsorize": {"lower_pct": 0, "upper_pct": 100},
                "quality": {
                    "weight": 0.4,
                    "variables": ["operating_profit_to_total_assets", "equity_to_assets"],
                },
                "value": {"weight": 0.4, "variables": ["earnings_yield", "book_to_market"]},
                "momentum": {"weight": 0.2, "variables": ["return_12_1", "return_6_1"]},
            }
        }
        factors = [
            factor_row("1001", 1.0),
            factor_row("1002", 2.0),
            factor_row("1003", 3.0),
        ]

        for strategy_version in STRATEGY_VERSION_CHOICES:
            scores, _raw_factors = build_scores(
                config=config,
                factor_rows=factors,
                strategy_version=strategy_version,
            )
            ranked = [row for row in scores if row.get("rank")]
            self.assertTrue(ranked, strategy_version)

    def test_constant_factor_values_are_neutral_not_missing(self) -> None:
        config = {
            "factors": {
                "winsorize": {"lower_pct": 0, "upper_pct": 100},
                "quality": {
                    "weight": 0.4,
                    "variables": ["operating_profit_to_total_assets", "equity_to_assets"],
                },
                "value": {"weight": 0.4, "variables": ["earnings_yield", "book_to_market"]},
                "momentum": {"weight": 0.2, "variables": ["return_12_1", "return_6_1"]},
            }
        }
        factors = [factor_row("1001", 1.0), factor_row("1002", 1.0), factor_row("1003", 1.0)]

        scores, raw_factors = build_scores(config=config, factor_rows=factors, strategy_version="qvm")

        self.assertEqual(
            [],
            [row for row in scores if row["missing_score_components"]],
        )
        self.assertEqual(["1", "2", "3"], [str(row["rank"]) for row in scores])
        for row in scores:
            self.assertEqual(0.0, row["qvm_score"])
            for factor in raw_factors:
                self.assertEqual(0.0, row[f"{factor}_z"])

    def test_weighted_group_scoring_respects_config_weights(self) -> None:
        config = weighted_config(weights={"quality": 0.0, "value": 1.0, "momentum": 0.0}, filters=[])
        factors = [
            grouped_factor_row("1001", quality=10, value=1, momentum=1),
            grouped_factor_row("1002", quality=1, value=10, momentum=1),
            grouped_factor_row("1003", quality=5, value=5, momentum=1),
        ]

        scores, _raw_factors = build_scores(
            config=config,
            factor_rows=factors,
            strategy_version="weighted_groups",
        )

        ranked = sorted([row for row in scores if row["rank"]], key=lambda row: int(row["rank"]))
        self.assertEqual(["1002", "1003", "1001"], [row["code"] for row in ranked])
        for row in scores:
            self.assertEqual(row["composite_score"], row["qvm_score"])
            self.assertEqual("pass", row["filter_status"])

    def test_weighted_group_scoring_rejects_bad_weight_config(self) -> None:
        factors = [grouped_factor_row("1001", quality=1, value=1, momentum=1)]

        with self.assertRaisesRegex(ValueError, "Unknown score group"):
            build_scores(
                config=weighted_config(weights={"quality": 1.0, "profit": 1.0}, filters=[]),
                factor_rows=factors,
                strategy_version="weighted_groups",
            )

        with self.assertRaisesRegex(ValueError, "greater than zero"):
            build_scores(
                config=weighted_config(weights={"quality": 0.0, "value": 0.0}, filters=[]),
                factor_rows=factors,
                strategy_version="weighted_groups",
            )

    def test_bottom_pct_filter_keeps_audit_columns_and_removes_rank(self) -> None:
        config = weighted_config(
            weights={"quality": 0.0, "value": 1.0, "momentum": 0.0},
            filters=[{"group": "momentum", "rule": "exclude_bottom_pct", "pct": 20}],
        )
        factors = [
            grouped_factor_row("1001", quality=1, value=5, momentum=1),
            grouped_factor_row("1002", quality=1, value=4, momentum=2),
            grouped_factor_row("1003", quality=1, value=3, momentum=3),
            grouped_factor_row("1004", quality=1, value=2, momentum=4),
            grouped_factor_row("1005", quality=1, value=1, momentum=5),
        ]

        scores, _raw_factors = build_scores(
            config=config,
            factor_rows=factors,
            strategy_version="weighted_groups",
        )

        by_code = {row["code"]: row for row in scores}
        self.assertEqual("filtered", by_code["1001"]["filter_status"])
        self.assertEqual("momentum_bottom_20pct", by_code["1001"]["filter_reasons"])
        self.assertEqual("", by_code["1001"]["rank"])
        self.assertNotEqual("", by_code["1001"]["composite_score"])
        self.assertEqual(["1002", "1003", "1004", "1005"], [row["code"] for row in scores if row["rank"]])

    def test_missing_filter_group_score_is_reported_separately(self) -> None:
        config = weighted_config(
            weights={"quality": 0.0, "value": 1.0, "momentum": 0.0},
            filters=[{"group": "momentum", "rule": "exclude_bottom_pct", "pct": 20}],
        )
        factors = [
            grouped_factor_row("1001", quality=1, value=3, momentum=None),
            grouped_factor_row("1002", quality=1, value=2, momentum=2),
            grouped_factor_row("1003", quality=1, value=1, momentum=3),
        ]

        scores, _raw_factors = build_scores(
            config=config,
            factor_rows=factors,
            strategy_version="weighted_groups",
        )

        by_code = {row["code"]: row for row in scores}
        self.assertEqual("missing_required_score", by_code["1001"]["filter_status"])
        self.assertEqual("momentum_score", by_code["1001"]["filter_reasons"])
        self.assertEqual("momentum_score", by_code["1001"]["missing_score_components"])
        self.assertEqual("", by_code["1001"]["rank"])
        self.assertNotIn("momentum_bottom", by_code["1001"]["filter_reasons"])

    def test_configured_factor_definitions_evaluate_safe_expressions(self) -> None:
        config = {
            "factors": {
                "definitions": [
                    {
                        "name": "profit_margin_proxy",
                        "group": "quality",
                        "expr": "ratio(net_profit, operating_profit)",
                    },
                    {
                        "name": "recent_return",
                        "group": "momentum",
                        "expr": "ts_return(lookback=2, skip=0)",
                    },
                ]
            }
        }

        rows = build_factors(
            config=config,
            rebalance_date=date(2026, 1, 3),
            universe_rows=[
                {
                    "code": "1001",
                    "name": "Synthetic 1001",
                    "market": "Prime",
                    "sector": "Industrials",
                    "latest_unadjusted_close": "120",
                }
            ],
            price_rows=[
                {"date": "2026-01-01", "code": "1001", "adjusted_close": "100", "unadjusted_close": "100"},
                {"date": "2026-01-02", "code": "1001", "adjusted_close": "110", "unadjusted_close": "110"},
                {"date": "2026-01-03", "code": "1001", "adjusted_close": "120", "unadjusted_close": "120"},
            ],
            fundamental_rows=[
                {
                    "code": "1001",
                    "available_date": "2025-12-31",
                    "operating_profit": "100",
                    "net_profit": "50",
                    "equity": "400",
                    "total_assets": "1000",
                    "shares_outstanding": "10",
                }
            ],
        )

        self.assertAlmostEqual(0.5, float(rows[0]["profit_margin_proxy"]))
        self.assertAlmostEqual(0.2, float(rows[0]["recent_return"]))
        self.assertNotIn("profit_margin_proxy", rows[0]["missing_flags"])

    def test_factor_expression_rejects_unsafe_calls(self) -> None:
        config = {
            "factors": {
                "definitions": [
                    {
                        "name": "bad_factor",
                        "group": "quality",
                        "expr": "__import__('os').system('echo no')",
                    }
                ]
            }
        }

        with self.assertRaisesRegex(ValueError, "Unsupported function call"):
            build_factors(
                config=config,
                rebalance_date=date(2026, 1, 1),
                universe_rows=[{"code": "1001", "latest_unadjusted_close": "100"}],
                price_rows=[{"date": "2026-01-01", "code": "1001", "adjusted_close": "100", "unadjusted_close": "100"}],
                fundamental_rows=[],
            )

    def test_where_preserves_missing_condition_as_missing_factor(self) -> None:
        config = {
            "factors": {
                "definitions": [
                    {
                        "name": "conditional_value",
                        "group": "value",
                        "expr": "where(operating_profit_to_total_assets > 0.1, earnings_yield, book_to_market)",
                    }
                ]
            }
        }

        rows = build_factors(
            config=config,
            rebalance_date=date(2026, 1, 1),
            universe_rows=[
                {
                    "code": "1001",
                    "name": "Synthetic 1001",
                    "market": "Prime",
                    "sector": "Industrials",
                    "latest_unadjusted_close": "100",
                }
            ],
            price_rows=[
                {"date": "2026-01-01", "code": "1001", "adjusted_close": "100", "unadjusted_close": "100"},
            ],
            fundamental_rows=[
                {
                    "code": "1001",
                    "available_date": "2025-12-31",
                    "operating_profit": "",
                    "net_profit": "50",
                    "equity": "500",
                    "total_assets": "",
                    "shares_outstanding": "10",
                }
            ],
        )

        self.assertIsNone(rows[0]["operating_profit_to_total_assets"])
        self.assertIsNotNone(rows[0]["book_to_market"])
        self.assertIsNone(rows[0]["conditional_value"])
        self.assertIn("conditional_value", rows[0]["missing_flags"])

    def test_factor_expression_pow_edges_return_missing_instead_of_crashing(self) -> None:
        config = {
            "factors": {
                "definitions": [
                    {"name": "inverse_zero", "group": "quality", "expr": "0 ** -1"},
                    {"name": "overflow_power", "group": "quality", "expr": "10 ** 1000000"},
                ]
            }
        }

        rows = build_factors(
            config=config,
            rebalance_date=date(2026, 1, 1),
            universe_rows=[{"code": "1001", "latest_unadjusted_close": "100"}],
            price_rows=[{"date": "2026-01-01", "code": "1001", "adjusted_close": "100", "unadjusted_close": "100"}],
            fundamental_rows=[],
        )

        self.assertIsNone(rows[0]["inverse_zero"])
        self.assertIsNone(rows[0]["overflow_power"])
        self.assertIn("inverse_zero", rows[0]["missing_flags"])
        self.assertIn("overflow_power", rows[0]["missing_flags"])

    def test_factor_expression_reports_wrong_function_arity_cleanly(self) -> None:
        config = {
            "factors": {
                "definitions": [
                    {"name": "bad_ratio", "group": "quality", "expr": "ratio(net_profit)"},
                ]
            }
        }

        with self.assertRaisesRegex(ValueError, "Invalid arguments for factor expression function ratio"):
            build_factors(
                config=config,
                rebalance_date=date(2026, 1, 1),
                universe_rows=[{"code": "1001", "latest_unadjusted_close": "100"}],
                price_rows=[{"date": "2026-01-01", "code": "1001", "adjusted_close": "100", "unadjusted_close": "100"}],
                fundamental_rows=[],
            )

    def test_configured_factor_definitions_extend_group_scoring(self) -> None:
        config = weighted_config(
            weights={"quality": 1.0, "value": 0.0, "momentum": 0.0},
            filters=[],
        )
        config["factors"]["quality"]["variables"] = []
        config["factors"]["definitions"] = [
            {
                "name": "profit_margin_proxy",
                "group": "quality",
                "expr": "ratio(net_profit, operating_profit)",
            }
        ]
        factors = [
            {"rebalance_date": "2026-03-31", "code": "1001", "profit_margin_proxy": "0.1"},
            {"rebalance_date": "2026-03-31", "code": "1002", "profit_margin_proxy": "0.3"},
            {"rebalance_date": "2026-03-31", "code": "1003", "profit_margin_proxy": "0.2"},
        ]

        scores, raw_factors = build_scores(
            config=config,
            factor_rows=factors,
            strategy_version="weighted_groups",
        )

        self.assertIn("profit_margin_proxy", raw_factors)
        ranked = sorted([row for row in scores if row["rank"]], key=lambda row: int(row["rank"]))
        self.assertEqual(["1002", "1003", "1001"], [row["code"] for row in ranked])

    def test_configurable_weighted_factors_and_field_filters(self) -> None:
        config = {
            "strategy": {
                "scoring": {"mode": "weighted_factors", "weights": {"custom_value": 1.0}},
                "filters": [{"field": "custom_value", "rule": "exclude_bottom_pct", "pct": 20}],
            },
            "factors": {
                "winsorize": {"lower_pct": 0, "upper_pct": 100},
                "quality": {"variables": []},
                "value": {"variables": []},
                "momentum": {"variables": []},
            },
        }
        factors = [
            {"rebalance_date": "2026-03-31", "code": "1001", "custom_value": "1"},
            {"rebalance_date": "2026-03-31", "code": "1002", "custom_value": "2"},
            {"rebalance_date": "2026-03-31", "code": "1003", "custom_value": "3"},
        ]

        scores, raw_factors = build_scores(
            config=config,
            factor_rows=factors,
            strategy_version="configurable",
        )

        self.assertEqual(["custom_value"], raw_factors)
        by_code = {row["code"]: row for row in scores}
        self.assertEqual("filtered", by_code["1001"]["filter_status"])
        self.assertEqual("custom_value_bottom_20pct", by_code["1001"]["filter_reasons"])
        ranked = sorted([row for row in scores if row["rank"]], key=lambda row: int(row["rank"]))
        self.assertEqual(["1003", "1002"], [row["code"] for row in ranked])

    def test_configurable_weighted_factors_reject_unknown_weight_field(self) -> None:
        config = {
            "strategy": {
                "scoring": {"mode": "weighted_factors", "weights": {"custom_typo": 1.0}},
                "filters": [],
            },
            "factors": {"winsorize": {"lower_pct": 0, "upper_pct": 100}},
        }

        with self.assertRaisesRegex(ValueError, "Unknown weighted_factors field"):
            build_scores(
                config=config,
                factor_rows=[
                    {"rebalance_date": "2026-03-31", "code": "1001", "custom_value": "1"},
                    {"rebalance_date": "2026-03-31", "code": "1002", "custom_value": "2"},
                ],
                strategy_version="configurable",
            )

    def test_filter_rejects_unknown_field_instead_of_empty_ranking(self) -> None:
        config = {
            "strategy": {
                "scoring": {"mode": "weighted_factors", "weights": {"custom_value": 1.0}},
                "filters": [{"field": "custom_typo", "rule": "exclude_bottom_pct", "pct": 20}],
            },
            "factors": {"winsorize": {"lower_pct": 0, "upper_pct": 100}},
        }

        with self.assertRaisesRegex(ValueError, "Unknown filter field: custom_typo"):
            build_scores(
                config=config,
                factor_rows=[
                    {"rebalance_date": "2026-03-31", "code": "1001", "custom_value": "1"},
                    {"rebalance_date": "2026-03-31", "code": "1002", "custom_value": "2"},
                ],
                strategy_version="configurable",
            )

    def test_threshold_filter_requires_explicit_z_score_field_for_factor_units(self) -> None:
        config = {
            "strategy": {
                "scoring": {"mode": "weighted_factors", "weights": {"custom_value": 1.0}},
                "filters": [{"field": "custom_value", "rule": "exclude_below", "value": 0}],
            },
            "factors": {"winsorize": {"lower_pct": 0, "upper_pct": 100}},
        }
        factors = [
            {"rebalance_date": "2026-03-31", "code": "1001", "custom_value": "1"},
            {"rebalance_date": "2026-03-31", "code": "1002", "custom_value": "2"},
            {"rebalance_date": "2026-03-31", "code": "1003", "custom_value": "3"},
        ]

        with self.assertRaisesRegex(ValueError, "Unknown filter field: custom_value"):
            build_scores(config=config, factor_rows=factors, strategy_version="configurable")

        config["strategy"]["filters"] = [{"field": "custom_value_z", "rule": "exclude_below", "value": 0}]
        scores, _raw_factors = build_scores(config=config, factor_rows=factors, strategy_version="configurable")

        by_code = {row["code"]: row for row in scores}
        self.assertEqual("filtered", by_code["1001"]["filter_status"])
        self.assertEqual("custom_value_z_below_0", by_code["1001"]["filter_reasons"])
        ranked = sorted([row for row in scores if row["rank"]], key=lambda row: int(row["rank"]))
        self.assertEqual(["1003", "1002"], [row["code"] for row in ranked])

    def test_percentile_filter_pct_must_be_positive(self) -> None:
        config = {
            "strategy": {
                "scoring": {"mode": "weighted_factors", "weights": {"custom_value": 1.0}},
                "filters": [{"field": "custom_value", "rule": "exclude_bottom_pct", "pct": 0}],
            },
            "factors": {"winsorize": {"lower_pct": 0, "upper_pct": 100}},
        }

        with self.assertRaisesRegex(ValueError, "pct must be greater than 0"):
            build_scores(
                config=config,
                factor_rows=[{"rebalance_date": "2026-03-31", "code": "1001", "custom_value": "1"}],
                strategy_version="configurable",
            )


def factor_row(code: str, value: float) -> dict[str, str]:
    return {
        "rebalance_date": "2026-03-31",
        "code": code,
        "name": f"Synthetic {code}",
        "sector": "Industrials",
        "latest_unadjusted_close": "1000",
        "operating_profit_to_total_assets": str(value),
        "equity_to_assets": str(value + 0.1),
        "earnings_yield": str(value + 0.2),
        "book_to_market": str(value + 0.3),
        "return_12_1": str(value + 0.4),
        "return_6_1": str(value + 0.5),
    }


def weighted_config(weights: dict[str, float], filters: list[dict[str, object]]) -> dict[str, object]:
    return {
        "strategy": {
            "scoring": {"mode": "weighted_groups", "weights": weights},
            "filters": filters,
        },
        "factors": {
            "winsorize": {"lower_pct": 0, "upper_pct": 100},
            "quality": {
                "weight": 0.4,
                "variables": ["operating_profit_to_total_assets", "equity_to_assets"],
            },
            "value": {"weight": 0.4, "variables": ["earnings_yield", "book_to_market"]},
            "momentum": {"weight": 0.2, "variables": ["return_12_1", "return_6_1"]},
        },
    }


def grouped_factor_row(
    code: str,
    *,
    quality: float | None,
    value: float | None,
    momentum: float | None,
) -> dict[str, str]:
    def text(number: float | None) -> str:
        return "" if number is None else str(number)

    return {
        "rebalance_date": "2026-03-31",
        "code": code,
        "name": f"Synthetic {code}",
        "sector": "Industrials",
        "latest_unadjusted_close": "1000",
        "operating_profit_to_total_assets": text(quality),
        "equity_to_assets": text(quality),
        "earnings_yield": text(value),
        "book_to_market": text(value),
        "return_12_1": text(momentum),
        "return_6_1": text(momentum),
    }


if __name__ == "__main__":
    unittest.main()
