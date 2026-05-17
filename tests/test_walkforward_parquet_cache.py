from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import time
import shutil
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from duckdb_query import parquet_scan, query  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def trading_days(end: date, count: int) -> list[date]:
    values: list[date] = []
    current = end
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current -= timedelta(days=1)
    return sorted(values)


def write_synthetic_walkforward_fixture(temp: Path) -> tuple[Path, Path, Path]:
    listings = temp / "listings.csv"
    prices = temp / "prices.csv"
    fundamentals = temp / "fundamentals.csv"
    codes = ["1001", "1002", "1003", "1004"]

    write_csv(
        listings,
        [
            {
                "code": code,
                "name": f"Synthetic {code}",
                "market": "Prime",
                "sector": "Industrials",
                "listed_date": "2020-01-01",
                "delisted_date": "2026-03-15" if code == "1003" else "",
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
            "security_type",
            "is_common_stock",
            "is_etf_reit_infra",
            "tradable_flag",
            "lot_size",
        ],
    )

    days = trading_days(date(2026, 3, 31), 280)
    price_rows: list[dict[str, object]] = []
    for code_index, code in enumerate(codes, start=1):
        base = 800 + code_index * 100
        drift = code_index * 0.25
        for day_index, day in enumerate(days):
            if code == "1002" and day > date(2026, 2, 27):
                continue
            if code == "1003" and day > date(2026, 3, 13):
                continue
            close = base + day_index * drift
            adjusted_close = close
            if code == "1001" and day < date(2026, 3, 2):
                close = close * 2
            price_rows.append(
                {
                    "date": day.isoformat(),
                    "code": code,
                    "unadjusted_open": round(close - 1, 2),
                    "unadjusted_close": round(close, 2),
                    "adjusted_close": round(adjusted_close, 2),
                    "trading_value": 50_000_000 + code_index * 5_000_000,
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
            "unadjusted_open",
            "unadjusted_close",
            "adjusted_close",
            "trading_value",
            "tradable_flag",
            "price_limit_flag",
        ],
    )

    write_csv(
        fundamentals,
        [
            {
                "code": code,
                "available_date": "2026-02-15",
                "available_time": "15:00",
                "document_type": "annual",
                "operating_profit": 100_000_000 + code_index * 10_000_000,
                "net_profit": 70_000_000 + code_index * 5_000_000,
                "equity": 900_000_000 + code_index * 30_000_000,
                "total_assets": 1_500_000_000 + code_index * 80_000_000,
                "shares_outstanding": 1_000_000,
            }
            for code_index, code in enumerate(codes, start=1)
        ],
        [
            "code",
            "available_date",
            "available_time",
            "document_type",
            "operating_profit",
            "net_profit",
            "equity",
            "total_assets",
            "shares_outstanding",
        ],
    )
    return listings, prices, fundamentals


def cache_namespace(cache_dir: Path) -> Path:
    namespaces = [path for path in cache_dir.iterdir() if path.is_dir()]
    if len(namespaces) != 1:
        raise AssertionError(f"Expected one cache namespace, found {namespaces}")
    return namespaces[0]


class WalkForwardParquetCacheTest(unittest.TestCase):
    def test_walkforward_uses_reuses_and_rebuilds_parquet_cache_while_writing_csv_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            cache_dir = temp / "cache"
            out_dir = temp / "out"
            report_dir = temp / "reports"
            base_command = [
                sys.executable,
                str(ROOT / "scripts" / "run_qvm_walkforward.py"),
                "--config",
                str(ROOT / "configs" / "qvm_v0_1.example.yml"),
                "--listings",
                str(listings),
                "--prices",
                str(prices),
                "--fundamentals",
                str(fundamentals),
                "--start-date",
                "2026-01-01",
                "--end-date",
                "2026-03-31",
                "--rebalance",
                "monthly",
                "--target-holdings",
                "15",
                "--adv-cap",
                "0.005",
                "--cache-format",
                "parquet",
                "--cache-dir",
                str(cache_dir),
                "--out-dir",
                str(out_dir),
                "--report-dir",
                str(report_dir),
                "--no-manifest",
                "--skip-stage-manifest",
            ]

            subprocess.run([*base_command, "--force-rebuild"], cwd=ROOT, check=True)
            namespace = cache_namespace(cache_dir)

            expected_cache_files = [
                namespace / "inputs" / "processed_prices.parquet",
                namespace / "inputs" / "processed_fundamentals.parquet",
                namespace / "universe" / "universe_202603.parquet",
                namespace / "factors" / "factors_202603.parquet",
                namespace / "scores" / "scores_202603_qvm.parquet",
            ]
            for path in expected_cache_files:
                self.assertTrue(path.exists(), path)
            candidate_files = list((namespace / "rebalance_candidates").glob("rebalance_candidates_202603_*.parquet"))
            self.assertEqual(1, len(candidate_files))
            self.assertIn("capital5000000", candidate_files[0].name)
            self.assertIn("rebalance_close_base", candidate_files[0].name)

            for prefix in [
                "qvm_walkforward_summary_",
                "qvm_walkforward_trades_",
                "qvm_walkforward_holdings_",
                "qvm_walkforward_equity_",
                "qvm_walkforward_failure_cases_",
            ]:
                self.assertTrue(list(out_dir.glob(f"{prefix}*.csv")), prefix)
            summary_paths = sorted(out_dir.glob("qvm_walkforward_summary_*.csv"))
            with summary_paths[-1].open("r", encoding="utf-8", newline="") as file:
                summary = list(csv.DictReader(file))
            self.assertEqual("pit", summary[-1]["lifecycle_data_status"])
            self.assertEqual("True", summary[-1]["performance_conclusion_allowed"])
            self.assertIn("cache_fingerprint", summary[-1])

            scores_cache = namespace / "scores" / "scores_202603_qvm.parquet"
            cache_mtime = scores_cache.stat().st_mtime_ns
            subprocess.run(base_command, cwd=ROOT, check=True)
            self.assertEqual(cache_mtime, scores_cache.stat().st_mtime_ns)

            frame = query(
                f"""
                select count(*) as scored_rows
                from {parquet_scan(scores_cache)}
                where qvm_score <> ''
                """
            )
            self.assertGreater(int(frame.loc[0, "scored_rows"]), 0)

            time.sleep(0.05)
            subprocess.run([*base_command, "--force-rebuild"], cwd=ROOT, check=True)
            self.assertGreaterEqual(scores_cache.stat().st_mtime_ns, cache_mtime)

            failures = []
            failure_paths = sorted(out_dir.glob("qvm_walkforward_failure_cases_*.csv"))
            with failure_paths[-1].open("r", encoding="utf-8", newline="") as file:
                failures = list(csv.DictReader(file))
            by_type = {row["failure_type"] for row in failures}
            self.assertIn("assumed_delisting_loss", by_type)
            self.assertIn("price_tail_gap", by_type)
            self.assertEqual(
                ["1003"],
                [row["code"] for row in failures if row["failure_type"] == "assumed_delisting_loss"],
            )
            self.assertIn("1002", [row["code"] for row in failures if row["failure_type"] == "price_tail_gap"])

            holdings_paths = sorted(out_dir.glob("qvm_walkforward_holdings_*.csv"))
            with holdings_paths[-1].open("r", encoding="utf-8", newline="") as file:
                holdings = list(csv.DictReader(file))
            trades_paths = sorted(out_dir.glob("qvm_walkforward_trades_*.csv"))
            with trades_paths[-1].open("r", encoding="utf-8", newline="") as file:
                trades = list(csv.DictReader(file))
            buy_shares = sum(
                float(row["filled_shares"])
                for row in trades
                if row["code"] == "1001" and row["side"] == "BUY"
            )
            final_split_holding = [
                row for row in holdings if row["code"] == "1001" and row["date"] == "2026-03-31"
            ][0]
            self.assertGreater(float(final_split_holding["shares"]), buy_shares)

    def test_cache_namespace_changes_when_config_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            cache_dir = temp / "cache"
            config_a = temp / "qvm_a.yml"
            config_b = temp / "qvm_b.yml"
            shutil.copy(ROOT / "configs" / "qvm_v0_1.example.yml", config_a)
            config_text = config_a.read_text(encoding="utf-8")
            config_b.write_text(config_text.replace("lower_pct: 1", "lower_pct: 2"), encoding="utf-8")

            def command(config: Path) -> list[str]:
                return [
                    sys.executable,
                    str(ROOT / "scripts" / "run_qvm_walkforward.py"),
                    "--config",
                    str(config),
                    "--listings",
                    str(listings),
                    "--prices",
                    str(prices),
                    "--fundamentals",
                    str(fundamentals),
                    "--start-date",
                    "2026-03-01",
                    "--end-date",
                    "2026-03-31",
                    "--cache-format",
                    "parquet",
                    "--cache-dir",
                    str(cache_dir),
                    "--out-dir",
                    str(temp / "out"),
                    "--report-dir",
                    str(temp / "reports"),
                    "--no-manifest",
                    "--skip-stage-manifest",
                ]

            subprocess.run(command(config_a), cwd=ROOT, check=True)
            first_namespaces = {path.name for path in cache_dir.iterdir() if path.is_dir()}
            self.assertEqual(1, len(first_namespaces))

            subprocess.run(command(config_b), cwd=ROOT, check=True)
            second_namespaces = {path.name for path in cache_dir.iterdir() if path.is_dir()}
            self.assertEqual(2, len(second_namespaces))
            self.assertTrue(first_namespaces < second_namespaces)

    def test_cache_namespace_changes_when_input_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            cache_dir = temp / "cache"

            command = [
                sys.executable,
                str(ROOT / "scripts" / "run_qvm_walkforward.py"),
                "--config",
                str(ROOT / "configs" / "qvm_v0_1.example.yml"),
                "--listings",
                str(listings),
                "--prices",
                str(prices),
                "--fundamentals",
                str(fundamentals),
                "--start-date",
                "2026-03-01",
                "--end-date",
                "2026-03-31",
                "--cache-format",
                "parquet",
                "--cache-dir",
                str(cache_dir),
                "--out-dir",
                str(temp / "out"),
                "--report-dir",
                str(temp / "reports"),
                "--no-manifest",
                "--skip-stage-manifest",
            ]

            subprocess.run(command, cwd=ROOT, check=True)
            first_namespaces = {path.name for path in cache_dir.iterdir() if path.is_dir()}
            self.assertEqual(1, len(first_namespaces))

            text = fundamentals.read_text(encoding="utf-8")
            self.assertIn("75000000", text)
            fundamentals.write_text(text.replace("75000000", "75000001", 1), encoding="utf-8")

            subprocess.run(command, cwd=ROOT, check=True)
            second_namespaces = {path.name for path in cache_dir.iterdir() if path.is_dir()}
            self.assertEqual(2, len(second_namespaces))
            self.assertTrue(first_namespaces < second_namespaces)

    def test_rebalance_candidate_cache_is_run_dependent_for_capital(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            cache_dir = temp / "cache"
            base_command = [
                sys.executable,
                str(ROOT / "scripts" / "run_qvm_walkforward.py"),
                "--config",
                str(ROOT / "configs" / "qvm_v0_1.example.yml"),
                "--listings",
                str(listings),
                "--prices",
                str(prices),
                "--fundamentals",
                str(fundamentals),
                "--start-date",
                "2026-03-01",
                "--end-date",
                "2026-03-31",
                "--cache-format",
                "parquet",
                "--cache-dir",
                str(cache_dir),
                "--out-dir",
                str(temp / "out"),
                "--report-dir",
                str(temp / "reports"),
                "--no-manifest",
                "--skip-stage-manifest",
            ]

            subprocess.run(base_command, cwd=ROOT, check=True)
            subprocess.run([*base_command, "--capital-jpy", "20000000"], cwd=ROOT, check=True)

            namespace = cache_namespace(cache_dir)
            candidate_names = sorted(
                path.name for path in (namespace / "rebalance_candidates").glob("rebalance_candidates_202603_*.parquet")
            )
            self.assertEqual(2, len(candidate_names))
            self.assertTrue(any("capital5000000" in name for name in candidate_names))
            self.assertTrue(any("capital20000000" in name for name in candidate_names))


if __name__ == "__main__":
    unittest.main()
