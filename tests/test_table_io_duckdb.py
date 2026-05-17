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
