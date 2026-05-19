from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import time
import shutil
import unittest
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from duckdb_query import parquet_scan, query  # noqa: E402
from build_rebalance_factor_score_panel import build_factor_score_panel  # noqa: E402
from build_rebalance_price_universe_panel import build_panel  # noqa: E402
from research_common import load_yaml, parse_float, read_csv  # noqa: E402
import run_qvm_walkforward  # noqa: E402


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


def write_synthetic_market_benchmark(temp: Path) -> Path:
    benchmark = temp / "market_benchmark.csv"
    days = trading_days(date(2026, 3, 31), 280)
    write_csv(
        benchmark,
        [
            {
                "date": day.isoformat(),
                "benchmark_id": "SYNMKT",
                "close": 1000 + day_index,
            }
            for day_index, day in enumerate(days)
        ],
        ["date", "benchmark_id", "close"],
    )
    return benchmark


def cache_namespaces(cache_dir: Path, layer: str) -> list[Path]:
    layer_dir = cache_dir / layer
    if not layer_dir.exists():
        return []
    return [path for path in layer_dir.iterdir() if path.is_dir()]


def cache_namespace(cache_dir: Path, layer: str) -> Path:
    namespaces = cache_namespaces(cache_dir, layer)
    if len(namespaces) != 1:
        raise AssertionError(f"Expected one {layer} cache namespace, found {namespaces}")
    return namespaces[0]


def without_fields(rows: list[dict[str, str]], excluded: set[str]) -> list[dict[str, str]]:
    return [{key: value for key, value in row.items() if key not in excluded} for row in rows]


def assert_panel_fields_match(
    testcase: unittest.TestCase,
    left: list[dict[str, str]],
    right: list[dict[str, str]],
    fields: list[str],
) -> None:
    left = sorted(left, key=lambda row: (row.get("rebalance_date", ""), row.get("code", "")))
    right = sorted(right, key=lambda row: (row.get("rebalance_date", ""), row.get("code", "")))
    testcase.assertEqual(len(left), len(right))
    for left_row, right_row in zip(left, right):
        for field in fields:
            left_value = left_row.get(field, "")
            right_value = right_row.get(field, "")
            left_number = parse_float(left_value)
            right_number = parse_float(right_value)
            if left_number is not None or right_number is not None:
                testcase.assertIsNotNone(left_number, (left_row.get("rebalance_date"), left_row.get("code"), field, left_value, right_value))
                testcase.assertIsNotNone(right_number, (left_row.get("rebalance_date"), left_row.get("code"), field, left_value, right_value))
                testcase.assertAlmostEqual(left_number or 0.0, right_number or 0.0, places=8)
            else:
                testcase.assertEqual(
                    left_value,
                    right_value,
                    (left_row.get("rebalance_date"), left_row.get("code"), field),
                )


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
            inputs_namespace = cache_namespace(cache_dir, "inputs")
            universe_namespace = cache_namespace(cache_dir, "universe")
            factors_namespace = cache_namespace(cache_dir, "factors")
            scores_namespace = cache_namespace(cache_dir, "scores")
            run_namespace = cache_namespace(cache_dir, "rebalance_candidates")

            expected_cache_files = [
                inputs_namespace / "processed_prices.parquet",
                inputs_namespace / "processed_fundamentals.parquet",
                universe_namespace / "universe_202603.parquet",
                factors_namespace / "factors_202603.parquet",
                scores_namespace / "scores_202603_qvm.parquet",
            ]
            for path in expected_cache_files:
                self.assertTrue(path.exists(), path)
            universe_cache = universe_namespace / "universe_202603.parquet"
            universe_frame = query(
                f"""
                select source, listing_lifecycle_status, last_trading_date,
                       lifecycle_exit_date, delisting_reason, successor_code
                from {parquet_scan(universe_cache)}
                limit 1
                """
            )
            for column in [
                "source",
                "listing_lifecycle_status",
                "last_trading_date",
                "lifecycle_exit_date",
                "delisting_reason",
                "successor_code",
            ]:
                self.assertIn(column, universe_frame.columns)
            candidate_files = list(run_namespace.glob("rebalance_candidates_202603_*.parquet"))
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
            self.assertEqual("pit_with_delistings", summary[-1]["lifecycle_data_status"])
            self.assertEqual("True", summary[-1]["performance_conclusion_allowed"])
            self.assertIn("cache_fingerprint", summary[-1])

            scores_cache = scores_namespace / "scores_202603_qvm.parquet"
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

    def test_fast_panels_walkforward_match_legacy_portfolio_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            config_path = ROOT / "configs" / "qvm_v0_1.example.yml"
            fast_panel = temp / "fast_price_universe_panel.parquet"
            build_panel(
                config=load_yaml(config_path),
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                input_format="csv",
                out_path=fast_panel,
                output_format="parquet",
            )
            legacy_factor_score_panel = temp / "legacy_factor_score_panel.parquet"
            build_factor_score_panel(
                config=load_yaml(config_path),
                price_universe_panel_path=fast_panel,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                strategy_version="qvm",
                out_path=legacy_factor_score_panel,
                output_format="parquet",
            )
            factor_score_panel = temp / "factor_score_panel.parquet"
            build_factor_score_panel(
                config=load_yaml(config_path),
                price_universe_panel_path=fast_panel,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                strategy_version="qvm",
                out_path=factor_score_panel,
                output_format="parquet",
                engine="duckdb",
            )
            assert_panel_fields_match(
                self,
                read_csv(legacy_factor_score_panel),
                read_csv(factor_score_panel),
                [
                    "rebalance_date",
                    "code",
                    "included_flag",
                    "rank",
                    "candidate_rank",
                    "quality_score",
                    "value_score",
                    "momentum_score",
                    "composite_score",
                    "qvm_score",
                    "filter_status",
                    "filter_reasons",
                    "missing_score_components",
                    "operating_profit_to_total_assets",
                    "book_to_market",
                    "return_12_1",
                    "operating_profit_to_total_assets_z",
                    "book_to_market_z",
                    "return_12_1_z",
                ],
            )

            base_command = [
                sys.executable,
                str(ROOT / "scripts" / "run_qvm_walkforward.py"),
                "--config",
                str(config_path),
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
                "--no-manifest",
                "--skip-stage-manifest",
            ]
            legacy_out = temp / "legacy_out"
            fast_out = temp / "fast_out"
            subprocess.run(
                [
                    *base_command,
                    "--cache-dir",
                    str(temp / "legacy_cache"),
                    "--out-dir",
                    str(legacy_out),
                    "--report-dir",
                    str(temp / "legacy_reports"),
                    "--run-label",
                    "legacy",
                ],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(
                [
                    *base_command,
                    "--cache-dir",
                    str(temp / "fast_cache"),
                    "--out-dir",
                    str(fast_out),
                    "--report-dir",
                    str(temp / "fast_reports"),
                    "--run-label",
                    "fast",
                    "--price-universe-panel",
                    str(fast_panel),
                ],
                cwd=ROOT,
                check=True,
            )
            factor_score_out = temp / "factor_score_out"
            subprocess.run(
                [
                    *base_command,
                    "--cache-dir",
                    str(temp / "factor_score_cache"),
                    "--out-dir",
                    str(factor_score_out),
                    "--report-dir",
                    str(temp / "factor_score_reports"),
                    "--run-label",
                    "factor_score",
                    "--factor-score-panel",
                    str(factor_score_panel),
                ],
                cwd=ROOT,
                check=True,
            )

            artifact_pairs = [
                (
                    legacy_out / "qvm_walkforward_summary_legacy_202601_202603.csv",
                    fast_out / "qvm_walkforward_summary_fast_202601_202603.csv",
                    factor_score_out / "qvm_walkforward_summary_factor_score_202601_202603.csv",
                    {"cache_fingerprint"},
                ),
                (
                    legacy_out / "qvm_walkforward_trades_legacy_202601_202603.csv",
                    fast_out / "qvm_walkforward_trades_fast_202601_202603.csv",
                    factor_score_out / "qvm_walkforward_trades_factor_score_202601_202603.csv",
                    set(),
                ),
                (
                    legacy_out / "qvm_walkforward_holdings_legacy_202601_202603.csv",
                    fast_out / "qvm_walkforward_holdings_fast_202601_202603.csv",
                    factor_score_out / "qvm_walkforward_holdings_factor_score_202601_202603.csv",
                    set(),
                ),
                (
                    legacy_out / "qvm_walkforward_equity_legacy_202601_202603.csv",
                    fast_out / "qvm_walkforward_equity_fast_202601_202603.csv",
                    factor_score_out / "qvm_walkforward_equity_factor_score_202601_202603.csv",
                    set(),
                ),
                (
                    legacy_out / "qvm_walkforward_failure_cases_legacy_202601_202603.csv",
                    fast_out / "qvm_walkforward_failure_cases_fast_202601_202603.csv",
                    factor_score_out / "qvm_walkforward_failure_cases_factor_score_202601_202603.csv",
                    set(),
                ),
            ]
            for legacy_path, price_panel_path, factor_panel_path, excluded_fields in artifact_pairs:
                expected = without_fields(read_csv(legacy_path), excluded_fields)
                self.assertEqual(expected, without_fields(read_csv(price_panel_path), excluded_fields), legacy_path.name)
                self.assertEqual(expected, without_fields(read_csv(factor_panel_path), excluded_fields), legacy_path.name)

    def test_factor_score_panel_preserves_missing_and_excluded_rows_without_ranking_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            config_path = temp / "qvm_allow_missing_fundamentals.yml"
            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_path.write_text(config_text.replace("require_fundamentals: true", "require_fundamentals: false"), encoding="utf-8")
            empty_fundamentals = temp / "empty_fundamentals.csv"
            write_csv(
                empty_fundamentals,
                [],
                [
                    "code",
                    "available_date",
                    "available_time",
                    "document_type",
                    "period_end",
                    "operating_profit",
                    "net_profit",
                    "equity",
                    "total_assets",
                    "shares_outstanding",
                ],
            )
            price_panel = temp / "price_panel.parquet"
            factor_score_panel = temp / "factor_score_panel.parquet"
            config = load_yaml(config_path)
            build_panel(
                config=config,
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=empty_fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                input_format="csv",
                out_path=price_panel,
                output_format="parquet",
            )
            build_factor_score_panel(
                config=config,
                price_universe_panel_path=price_panel,
                prices_path=prices,
                fundamentals_path=empty_fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                strategy_version="qvm",
                out_path=factor_score_panel,
                output_format="parquet",
                engine="duckdb",
            )

            rows = read_csv(factor_score_panel)
            by_key = {(row["rebalance_date"], row["code"]): row for row in rows}
            stale_row = by_key[("2026-03-31", "1002")]
            self.assertEqual("true", stale_row["included_flag"])
            self.assertEqual("True", stale_row["latest_price_stale"])
            self.assertEqual("", stale_row["rank"])
            self.assertIn("quality_score", stale_row["missing_score_components"])

            excluded_row = by_key[("2026-03-31", "1003")]
            self.assertEqual("false", excluded_row["included_flag"])
            self.assertIn("delisted_before_rebalance", excluded_row["exclusion_reason"])
            self.assertEqual("", excluded_row["rank"])
            self.assertEqual("", excluded_row["candidate_rank"])

    def test_duckdb_factor_score_panel_matches_legacy_for_supported_strategy_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            config_path = ROOT / "configs" / "qvm_v0_1.example.yml"
            config = load_yaml(config_path)
            price_panel = temp / "price_panel.parquet"
            build_panel(
                config=config,
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                input_format="csv",
                out_path=price_panel,
                output_format="parquet",
            )
            fields = [
                "rebalance_date",
                "code",
                "included_flag",
                "rank",
                "candidate_rank",
                "quality_score",
                "value_score",
                "momentum_score",
                "composite_score",
                "qvm_score",
                "filter_status",
                "filter_reasons",
                "missing_score_components",
                "operating_profit_to_total_assets",
                "equity_to_assets",
                "earnings_yield",
                "book_to_market",
                "return_12_1",
                "return_6_1",
            ]
            for strategy_version in ["qvm", "qv", "value_only", "weighted_groups"]:
                legacy_panel = temp / f"legacy_{strategy_version}.parquet"
                duckdb_panel = temp / f"duckdb_{strategy_version}.parquet"
                build_factor_score_panel(
                    config=config,
                    price_universe_panel_path=price_panel,
                    prices_path=prices,
                    fundamentals_path=fundamentals,
                    start_date="2026-01-01",
                    end_date="2026-03-31",
                    frequency="monthly",
                    strategy_version=strategy_version,
                    out_path=legacy_panel,
                    output_format="parquet",
                )
                build_factor_score_panel(
                    config=config,
                    price_universe_panel_path=price_panel,
                    prices_path=prices,
                    fundamentals_path=fundamentals,
                    start_date="2026-01-01",
                    end_date="2026-03-31",
                    frequency="monthly",
                    strategy_version=strategy_version,
                    out_path=duckdb_panel,
                    output_format="parquet",
                    engine="duckdb",
                )
                assert_panel_fields_match(self, read_csv(legacy_panel), read_csv(duckdb_panel), fields)

    def test_duckdb_factor_score_panel_rejects_unsupported_strategy_mechanics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            base_config = load_yaml(ROOT / "configs" / "qvm_v0_1.example.yml")
            price_panel = temp / "price_panel.parquet"
            build_panel(
                config=base_config,
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                input_format="csv",
                out_path=price_panel,
                output_format="parquet",
            )

            expression_config = load_yaml(ROOT / "configs" / "qvm_v0_1.example.yml")
            expression_config.setdefault("factors", {})["definitions"] = [
                {"name": "quality_blend", "group": "quality", "expr": "operating_profit_to_total_assets"}
            ]
            with self.assertRaisesRegex(ValueError, "does not support factors.definitions"):
                build_factor_score_panel(
                    config=expression_config,
                    price_universe_panel_path=price_panel,
                    prices_path=prices,
                    fundamentals_path=fundamentals,
                    start_date="2026-01-01",
                    end_date="2026-03-31",
                    frequency="monthly",
                    strategy_version="qvm",
                    out_path=temp / "unsupported_expression.parquet",
                    output_format="parquet",
                    engine="duckdb",
                )

            field_filter_config = load_yaml(ROOT / "configs" / "qvm_v0_1.example.yml")
            field_filter_config["strategy"]["filters"] = [
                {"field": "book_to_market", "rule": "exclude_bottom_pct", "pct": 20}
            ]
            with self.assertRaisesRegex(ValueError, "supports group filters only"):
                build_factor_score_panel(
                    config=field_filter_config,
                    price_universe_panel_path=price_panel,
                    prices_path=prices,
                    fundamentals_path=fundamentals,
                    start_date="2026-01-01",
                    end_date="2026-03-31",
                    frequency="monthly",
                    strategy_version="weighted_groups",
                    out_path=temp / "unsupported_field_filter.parquet",
                    output_format="parquet",
                    engine="duckdb",
                )

    def test_duckdb_factor_score_panel_requires_complete_rebalance_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            config = load_yaml(ROOT / "configs" / "qvm_v0_1.example.yml")
            price_panel = temp / "price_panel.parquet"
            build_panel(
                config=config,
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                input_format="csv",
                out_path=price_panel,
                output_format="parquet",
            )
            panel_rows = read_csv(price_panel)
            pruned_panel = temp / "pruned_price_panel.csv"
            write_csv(
                pruned_panel,
                [row for row in panel_rows if row["rebalance_date"] != "2026-02-27"],
                list(panel_rows[0].keys()),
            )

            with self.assertRaisesRegex(ValueError, "No price/universe panel rows found for rebalance date 2026-02-27"):
                build_factor_score_panel(
                    config=config,
                    price_universe_panel_path=pruned_panel,
                    prices_path=prices,
                    fundamentals_path=fundamentals,
                    start_date="2026-01-01",
                    end_date="2026-03-31",
                    frequency="monthly",
                    strategy_version="qvm",
                    out_path=temp / "duckdb_missing_rebalance.parquet",
                    output_format="parquet",
                    engine="duckdb",
                )

    def test_duckdb_factor_score_panel_matches_legacy_with_comma_numeric_fundamentals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            comma_fundamentals = temp / "comma_fundamentals.csv"
            fundamental_rows = read_csv(fundamentals)
            numeric_fields = [
                "operating_profit",
                "net_profit",
                "equity",
                "total_assets",
                "shares_outstanding",
            ]
            for row in fundamental_rows:
                for field in numeric_fields:
                    row[field] = f"{int(float(row[field])):,}"
            write_csv(comma_fundamentals, fundamental_rows, list(fundamental_rows[0].keys()))

            config = load_yaml(ROOT / "configs" / "qvm_v0_1.example.yml")
            price_panel = temp / "price_panel.parquet"
            build_panel(
                config=config,
                listings_path=listings,
                prices_path=prices,
                fundamentals_path=comma_fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                input_format="csv",
                out_path=price_panel,
                output_format="parquet",
            )
            legacy_panel = temp / "legacy_comma.parquet"
            duckdb_panel = temp / "duckdb_comma.parquet"
            build_factor_score_panel(
                config=config,
                price_universe_panel_path=price_panel,
                prices_path=prices,
                fundamentals_path=comma_fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                strategy_version="qvm",
                out_path=legacy_panel,
                output_format="parquet",
            )
            build_factor_score_panel(
                config=config,
                price_universe_panel_path=price_panel,
                prices_path=prices,
                fundamentals_path=comma_fundamentals,
                start_date="2026-01-01",
                end_date="2026-03-31",
                frequency="monthly",
                strategy_version="qvm",
                out_path=duckdb_panel,
                output_format="parquet",
                engine="duckdb",
            )
            assert_panel_fields_match(
                self,
                read_csv(legacy_panel),
                read_csv(duckdb_panel),
                [
                    "rebalance_date",
                    "code",
                    "rank",
                    "candidate_rank",
                    "operating_profit",
                    "net_profit",
                    "equity",
                    "total_assets",
                    "shares",
                    "operating_profit_to_total_assets",
                    "earnings_yield",
                    "book_to_market",
                    "quality_score",
                    "value_score",
                    "composite_score",
                    "qvm_score",
                ],
            )

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
            first_score_namespaces = {path.name for path in cache_namespaces(cache_dir, "scores")}
            self.assertEqual(1, len(first_score_namespaces))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "inputs")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "universe")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "factors")))

            subprocess.run(command(config_b), cwd=ROOT, check=True)
            second_score_namespaces = {path.name for path in cache_namespaces(cache_dir, "scores")}
            self.assertEqual(2, len(second_score_namespaces))
            self.assertTrue(first_score_namespaces < second_score_namespaces)
            self.assertEqual(1, len(cache_namespaces(cache_dir, "inputs")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "universe")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "factors")))

    def test_weighted_group_walkforward_and_score_config_cache_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            cache_dir = temp / "cache"
            config_a = temp / "weighted_a.yml"
            config_b = temp / "weighted_b.yml"
            config_text = (ROOT / "configs" / "qvm_v0_1.example.yml").read_text(encoding="utf-8")
            config_a.write_text(config_text, encoding="utf-8")
            config_b.write_text(
                config_text.replace("      value: 0.4", "      value: 0.6", 1),
                encoding="utf-8",
            )

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
                    "--strategy-version",
                    "weighted_groups",
                    "--no-manifest",
                    "--skip-stage-manifest",
                ]

            subprocess.run(command(config_a), cwd=ROOT, check=True)
            first_score_namespaces = {path.name for path in cache_namespaces(cache_dir, "scores")}
            self.assertEqual(1, len(first_score_namespaces))
            scores_namespace_a = cache_namespace(cache_dir, "scores")
            score_files = list(scores_namespace_a.glob("scores_202603_weighted_groups.parquet"))
            self.assertEqual(1, len(score_files))
            rows = query(
                f"""
                select composite_score, qvm_score, filter_status
                from {parquet_scan(score_files[0])}
                """
            )
            self.assertIn("composite_score", rows.columns)
            self.assertTrue((rows["filter_status"] != "").all())

            subprocess.run(command(config_b), cwd=ROOT, check=True)
            second_score_namespaces = {path.name for path in cache_namespaces(cache_dir, "scores")}
            self.assertEqual(2, len(second_score_namespaces))
            self.assertTrue(first_score_namespaces < second_score_namespaces)
            self.assertEqual(1, len(cache_namespaces(cache_dir, "inputs")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "universe")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "factors")))

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
            first_input_namespaces = {path.name for path in cache_namespaces(cache_dir, "inputs")}
            self.assertEqual(1, len(first_input_namespaces))

            text = fundamentals.read_text(encoding="utf-8")
            self.assertIn("75000000", text)
            fundamentals.write_text(text.replace("75000000", "75000001", 1), encoding="utf-8")

            subprocess.run(command, cwd=ROOT, check=True)
            second_input_namespaces = {path.name for path in cache_namespaces(cache_dir, "inputs")}
            self.assertEqual(2, len(second_input_namespaces))
            self.assertTrue(first_input_namespaces < second_input_namespaces)
            self.assertEqual(2, len(cache_namespaces(cache_dir, "universe")))
            self.assertEqual(2, len(cache_namespaces(cache_dir, "factors")))
            self.assertEqual(2, len(cache_namespaces(cache_dir, "scores")))

    def test_portfolio_parameter_change_reuses_upstream_cache_layers(self) -> None:
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

            subprocess.run([*base_command, "--target-holdings", "15", "--adv-cap", "0.005"], cwd=ROOT, check=True)
            universe_cache = cache_namespace(cache_dir, "universe") / "universe_202603.parquet"
            factors_cache = cache_namespace(cache_dir, "factors") / "factors_202603.parquet"
            scores_cache = cache_namespace(cache_dir, "scores") / "scores_202603_qvm.parquet"
            mtimes = {
                "universe": universe_cache.stat().st_mtime_ns,
                "factors": factors_cache.stat().st_mtime_ns,
                "scores": scores_cache.stat().st_mtime_ns,
            }

            subprocess.run([*base_command, "--target-holdings", "30", "--adv-cap", "0.01"], cwd=ROOT, check=True)

            self.assertEqual(1, len(cache_namespaces(cache_dir, "inputs")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "universe")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "factors")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "scores")))
            self.assertEqual(2, len(cache_namespaces(cache_dir, "rebalance_candidates")))
            self.assertEqual(mtimes["universe"], universe_cache.stat().st_mtime_ns)
            self.assertEqual(mtimes["factors"], factors_cache.stat().st_mtime_ns)
            self.assertEqual(mtimes["scores"], scores_cache.stat().st_mtime_ns)

    def test_cache_fingerprints_include_stage_source_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            args = Namespace(
                listings=listings,
                prices=prices,
                fundamentals=fundamentals,
                strategy_version="qvm",
                start_date="2026-03-01",
                end_date="2026-03-31",
                frequency="monthly",
                execution_price="rebalance_close",
                cost_scenario="base",
                capital_jpy=5_000_000,
                tax_rate=0.20315,
            )

            config = load_yaml(ROOT / "configs" / "qvm_v0_1.example.yml")
            fingerprints = run_qvm_walkforward.compute_cache_fingerprints(args, config)
            original_source_checksum = run_qvm_walkforward.source_checksum
            try:
                run_qvm_walkforward.source_checksum = lambda name: f"changed-{name}"
                changed = run_qvm_walkforward.compute_cache_fingerprints(args, config)
            finally:
                run_qvm_walkforward.source_checksum = original_source_checksum

            self.assertEqual({"inputs", "universe", "factors", "scores", "run"}, set(fingerprints))
            self.assertEqual(fingerprints["inputs"], changed["inputs"])
            self.assertNotEqual(fingerprints["universe"], changed["universe"])
            self.assertNotEqual(fingerprints["factors"], changed["factors"])
            self.assertNotEqual(fingerprints["scores"], changed["scores"])
            self.assertNotEqual(fingerprints["run"], changed["run"])

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

            namespace = cache_namespaces(cache_dir, "rebalance_candidates")
            self.assertEqual(2, len(namespace))
            candidate_names = sorted(
                path.name
                for run_namespace in namespace
                for path in run_namespace.glob("rebalance_candidates_202603_*.parquet")
            )
            self.assertEqual(2, len(candidate_names))
            self.assertTrue(any("capital5000000" in name for name in candidate_names))
            self.assertTrue(any("capital20000000" in name for name in candidate_names))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "inputs")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "universe")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "factors")))
            self.assertEqual(1, len(cache_namespaces(cache_dir, "scores")))

    def test_walkforward_outputs_market_benchmark_series(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            listings, prices, fundamentals = write_synthetic_walkforward_fixture(temp)
            market_benchmark = write_synthetic_market_benchmark(temp)
            out_dir = temp / "out"
            report_dir = temp / "reports"
            subprocess.run(
                [
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
                    "--market-benchmark-prices",
                    str(market_benchmark),
                    "--market-benchmark-id",
                    "SYNMKT",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                    "--skip-stage-manifest",
                ],
                cwd=ROOT,
                check=True,
            )

            summary_paths = sorted(out_dir.glob("qvm_walkforward_summary_*.csv"))
            with summary_paths[-1].open("r", encoding="utf-8", newline="") as file:
                summary = list(csv.DictReader(file))
            self.assertEqual("SYNMKT", summary[-1]["market_benchmark_id"])
            self.assertGreater(float(summary[-1]["market_benchmark_equity"]), 5_000_000)
            self.assertNotEqual("", summary[-1]["market_benchmark_return"])

            equity_paths = sorted(out_dir.glob("qvm_walkforward_equity_*.csv"))
            with equity_paths[-1].open("r", encoding="utf-8", newline="") as file:
                equity = list(csv.DictReader(file))
            self.assertEqual("SYNMKT", equity[-1]["market_benchmark_id"])
            self.assertIn("market benchmark return", sorted(report_dir.glob("qvm_walkforward_*.md"))[-1].read_text())


if __name__ == "__main__":
    unittest.main()
