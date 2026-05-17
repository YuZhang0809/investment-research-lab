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


if __name__ == "__main__":
    unittest.main()
