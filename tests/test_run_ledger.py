from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path) -> None:
    fieldnames = [
        "rebalance_date",
        "strategy_version",
        "frequency",
        "execution_price",
        "cost_scenario",
        "capital_jpy",
        "portfolio_equity_pre",
        "portfolio_equity_after_cost",
        "after_tax_taxable_equity",
        "benchmark_equity",
        "lifecycle_data_status",
        "performance_conclusion_allowed",
        "missing_price_tail_policy",
        "missing_price_tail_max_stale_days",
        "cash_pct",
        "turnover",
    ]
    write_csv(
        path,
        [
            {
                "rebalance_date": "2026-01-31",
                "strategy_version": "weighted_groups",
                "frequency": "monthly",
                "execution_price": "rebalance_close",
                "cost_scenario": "base",
                "capital_jpy": "1000",
                "portfolio_equity_pre": "1000",
                "portfolio_equity_after_cost": "1000",
                "after_tax_taxable_equity": "1000",
                "benchmark_equity": "1000",
                "lifecycle_data_status": "unknown",
                "performance_conclusion_allowed": "False",
                "missing_price_tail_policy": "warn_only",
                "missing_price_tail_max_stale_days": "5",
                "cash_pct": "0.10",
                "turnover": "0.20",
            },
            {
                "rebalance_date": "2026-02-28",
                "strategy_version": "weighted_groups",
                "frequency": "monthly",
                "execution_price": "rebalance_close",
                "cost_scenario": "base",
                "capital_jpy": "1000",
                "portfolio_equity_pre": "1000",
                "portfolio_equity_after_cost": "1100",
                "after_tax_taxable_equity": "1080",
                "benchmark_equity": "1040",
                "lifecycle_data_status": "unknown",
                "performance_conclusion_allowed": "False",
                "missing_price_tail_policy": "warn_only",
                "missing_price_tail_max_stale_days": "5",
                "cash_pct": "0.20",
                "turnover": "0.30",
            },
        ],
        fieldnames,
    )


def protocol_args() -> list[str]:
    return [
        "--hypothesis",
        "Synthetic hypothesis",
        "--predefined-metric",
        "after_cost_return",
        "--go-no-go-criterion",
        "Synthetic go/no-go criterion",
    ]


class RunLedgerTest(unittest.TestCase):
    def test_append_run_record_writes_header_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.csv"
            config = temp / "config.example.yml"
            data = temp / "prices.csv"
            ledger = temp / "run_ledger.csv"
            write_summary(summary)
            config.write_text("experiment_id: synthetic\n", encoding="utf-8")
            data.write_text("date,code\n2026-01-31,1001\n", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "append_run_record.py"),
                    "--summary",
                    str(summary),
                    "--config",
                    str(config),
                    "--ledger",
                    str(ledger),
                    "--run-id",
                    "synthetic_run",
                    "--run-at",
                    "2026-03-01T00:00:00Z",
                    "--experiment-id",
                    "synthetic_experiment",
                    "--universe-label",
                    "synthetic",
                    "--decision",
                    "REVIEW",
                    "--decision-reason",
                    "Synthetic review",
                    *protocol_args(),
                    "--data-path",
                    str(data),
                ],
                cwd=ROOT,
                check=True,
            )

            with ledger.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(1, len(rows))
            self.assertEqual("synthetic_run", rows[0]["run_id"])
            self.assertEqual("synthetic_experiment", rows[0]["experiment_id"])
            self.assertEqual("Synthetic hypothesis", rows[0]["hypothesis"])
            self.assertEqual("after_cost_return", rows[0]["predefined_metrics"])
            self.assertEqual("Synthetic go/no-go criterion", rows[0]["go_no_go_criteria"])
            self.assertEqual("REVIEW", rows[0]["decision"])
            self.assertTrue(rows[0]["code_version"])
            self.assertTrue(rows[0]["engine_hash"])
            self.assertEqual("2", rows[0]["rebalance_count"])
            self.assertEqual("unknown", rows[0]["lifecycle_data_status"])
            self.assertEqual("0.1", rows[0]["key_metric_after_cost"])
            self.assertEqual("0.08", rows[0]["key_metric_after_tax"])
            self.assertEqual("0.04", rows[0]["key_metric_benchmark"])
            self.assertEqual("0.15", rows[0]["avg_cash_pct"])
            self.assertEqual("0.25", rows[0]["avg_turnover"])
            self.assertNotIn(str(temp), rows[0]["data_hash"])

    def test_duplicate_run_id_fails_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.csv"
            config = temp / "config.example.yml"
            ledger = temp / "run_ledger.csv"
            write_summary(summary)
            config.write_text("experiment_id: synthetic\n", encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "scripts" / "append_run_record.py"),
                "--summary",
                str(summary),
                "--config",
                str(config),
                "--ledger",
                str(ledger),
                "--run-id",
                "duplicate_run",
                *protocol_args(),
            ]

            subprocess.run(command, cwd=ROOT, check=True)
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("run_id already exists", result.stderr)

    def test_invalid_decision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.csv"
            config = temp / "config.example.yml"
            ledger = temp / "run_ledger.csv"
            write_summary(summary)
            config.write_text("experiment_id: synthetic\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "append_run_record.py"),
                    "--summary",
                    str(summary),
                    "--config",
                    str(config),
                    "--ledger",
                    str(ledger),
                    "--run-id",
                    "bad_decision",
                    *protocol_args(),
                    "--decision",
                    "GO",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("invalid choice", result.stderr)

    def test_default_run_id_changes_with_code_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.csv"
            config = temp / "config.example.yml"
            ledger = temp / "run_ledger.csv"
            write_summary(summary)
            config.write_text("experiment_id: synthetic\n", encoding="utf-8")
            base_command = [
                sys.executable,
                str(ROOT / "scripts" / "append_run_record.py"),
                "--summary",
                str(summary),
                "--config",
                str(config),
                "--ledger",
                str(ledger),
                "--engine-hash",
                "engine-a",
                *protocol_args(),
            ]

            subprocess.run([*base_command, "--code-version", "code-a"], cwd=ROOT, check=True)
            subprocess.run([*base_command, "--code-version", "code-b"], cwd=ROOT, check=True)

            with ledger.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(2, len(rows))
            self.assertNotEqual(rows[0]["run_id"], rows[1]["run_id"])

    def test_append_run_record_derives_market_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.csv"
            config = temp / "config.example.yml"
            ledger = temp / "run_ledger.csv"
            fieldnames = [
                "rebalance_date",
                "strategy_version",
                "frequency",
                "execution_price",
                "cost_scenario",
                "capital_jpy",
                "portfolio_equity_after_cost",
                "after_tax_taxable_equity",
                "benchmark_equity",
                "portfolio_return_after_cost",
                "market_benchmark_id",
                "market_benchmark_equity",
                "market_benchmark_return",
                "cash_pct",
                "turnover",
            ]
            write_csv(
                summary,
                [
                    {
                        "rebalance_date": "2026-01-31",
                        "strategy_version": "weighted_groups",
                        "frequency": "monthly",
                        "execution_price": "rebalance_close",
                        "cost_scenario": "base",
                        "capital_jpy": "1000",
                        "portfolio_equity_after_cost": "1000",
                        "after_tax_taxable_equity": "1000",
                        "benchmark_equity": "1000",
                        "portfolio_return_after_cost": "0",
                        "market_benchmark_id": "SYNMKT",
                        "market_benchmark_equity": "1000",
                        "market_benchmark_return": "0",
                        "cash_pct": "0",
                        "turnover": "0",
                    },
                    {
                        "rebalance_date": "2026-02-28",
                        "strategy_version": "weighted_groups",
                        "frequency": "monthly",
                        "execution_price": "rebalance_close",
                        "cost_scenario": "base",
                        "capital_jpy": "1000",
                        "portfolio_equity_after_cost": "1080",
                        "after_tax_taxable_equity": "1080",
                        "benchmark_equity": "1040",
                        "portfolio_return_after_cost": "0.08",
                        "market_benchmark_id": "SYNMKT",
                        "market_benchmark_equity": "1040",
                        "market_benchmark_return": "0.04",
                        "cash_pct": "0",
                        "turnover": "0",
                    },
                    {
                        "rebalance_date": "2026-03-31",
                        "strategy_version": "weighted_groups",
                        "frequency": "monthly",
                        "execution_price": "rebalance_close",
                        "cost_scenario": "base",
                        "capital_jpy": "1000",
                        "portfolio_equity_after_cost": "1209.6",
                        "after_tax_taxable_equity": "1209.6",
                        "benchmark_equity": "1060",
                        "portfolio_return_after_cost": "0.12",
                        "market_benchmark_id": "SYNMKT",
                        "market_benchmark_equity": "1102.4",
                        "market_benchmark_return": "0.06",
                        "cash_pct": "0",
                        "turnover": "0",
                    },
                ],
                fieldnames,
            )
            config.write_text("experiment_id: synthetic\n", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "append_run_record.py"),
                    "--summary",
                    str(summary),
                    "--config",
                    str(config),
                    "--ledger",
                    str(ledger),
                    "--run-id",
                    "market_run",
                    *protocol_args(),
                ],
                cwd=ROOT,
                check=True,
            )

            with ledger.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual("SYNMKT", rows[0]["market_benchmark_id"])
            self.assertAlmostEqual(2.0, float(rows[0]["market_beta"]))
            self.assertAlmostEqual(0.0, float(rows[0]["market_alpha"]), places=10)
            self.assertGreater(float(rows[0]["tracking_error"]), 0)
            self.assertGreater(float(rows[0]["information_ratio"]), 0)

    def test_generate_decision_note_from_synthetic_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.csv"
            config = temp / "config.example.yml"
            ledger = temp / "run_ledger.csv"
            note = temp / "decision.md"
            write_summary(summary)
            config.write_text("experiment_id: synthetic\n", encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "append_run_record.py"),
                    "--summary",
                    str(summary),
                    "--config",
                    str(config),
                    "--ledger",
                    str(ledger),
                    "--run-id",
                    "note_run",
                    *protocol_args(),
                    "--decision",
                    "PAPER_TEST",
                    "--decision-reason",
                    "Synthetic threshold met",
                ],
                cwd=ROOT,
                check=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_decision_note.py"),
                    "--ledger",
                    str(ledger),
                    "--run-id",
                    "note_run",
                    "--out",
                    str(note),
                    "--known-caveat",
                    "Synthetic data only",
                    "--next-action",
                    "Review failure cases",
                ],
                cwd=ROOT,
                check=True,
            )

            text = note.read_text(encoding="utf-8")
            self.assertIn("decision: PAPER_TEST", text)
            self.assertIn("Synthetic threshold met", text)
            self.assertIn("| after-cost return | 10.00% |", text)
            self.assertIn("Synthetic data only", text)
            self.assertIn("Performance conclusion not allowed", text)
            self.assertIn("| rebalance count | 2.0000 |", text)
            self.assertIn("| lifecycle status | unknown |", text)
            self.assertIn("Missing price tail policy: warn_only", text)
            self.assertIn("Review failure cases", text)
            self.assertIn("not an approval", text)


if __name__ == "__main__":
    unittest.main()
