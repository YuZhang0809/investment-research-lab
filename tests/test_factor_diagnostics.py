from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_factor_forward_returns import main as analyze_factor_forward_returns_main  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


class FactorDiagnosticsTest(unittest.TestCase):
    def test_factor_diagnostics_write_alphalens_style_data_and_tearsheet_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            factors_dir = temp / "factors"
            out_dir = temp / "out"
            report_dir = temp / "reports"
            prices_path = temp / "prices.csv"
            factor_fields = ["rebalance_date", "code", "name", "sector", "custom_factor"]

            write_csv(
                factors_dir / "factors_202601.csv",
                [
                    {"rebalance_date": "2026-01-01", "code": "A", "name": "Synthetic A", "sector": "Tech", "custom_factor": 1},
                    {"rebalance_date": "2026-01-01", "code": "B", "name": "Synthetic B", "sector": "Tech", "custom_factor": 2},
                    {"rebalance_date": "2026-01-01", "code": "C", "name": "Synthetic C", "sector": "Health", "custom_factor": 3},
                    {"rebalance_date": "2026-01-01", "code": "D", "name": "Synthetic D", "sector": "Health", "custom_factor": 4},
                ],
                factor_fields,
            )
            write_csv(
                factors_dir / "factors_202602.csv",
                [
                    {"rebalance_date": "2026-02-01", "code": "A", "name": "Synthetic A", "sector": "Tech", "custom_factor": 4},
                    {"rebalance_date": "2026-02-01", "code": "B", "name": "Synthetic B", "sector": "Tech", "custom_factor": 3},
                    {"rebalance_date": "2026-02-01", "code": "C", "name": "Synthetic C", "sector": "Health", "custom_factor": 2},
                    {"rebalance_date": "2026-02-01", "code": "D", "name": "Synthetic D", "sector": "Health", "custom_factor": 1},
                ],
                factor_fields,
            )
            price_rows = []
            for code, jan_end, feb_end in [
                ("A", 101, 104),
                ("B", 102, 103),
                ("C", 103, 102),
                ("D", 104, 101),
            ]:
                price_rows.extend(
                    [
                        {"date": "2026-01-01", "code": code, "adjusted_close": 100, "unadjusted_close": 100},
                        {"date": "2026-01-02", "code": code, "adjusted_close": jan_end, "unadjusted_close": jan_end},
                        {"date": "2026-02-01", "code": code, "adjusted_close": 100, "unadjusted_close": 100},
                        {"date": "2026-02-02", "code": code, "adjusted_close": feb_end, "unadjusted_close": feb_end},
                    ]
                )
            write_csv(prices_path, price_rows, ["date", "code", "adjusted_close", "unadjusted_close"])

            original_argv = sys.argv[:]
            try:
                sys.argv = [
                    "analyze_factor_forward_returns.py",
                    "--factors-dir",
                    str(factors_dir),
                    "--prices",
                    str(prices_path),
                    "--start-date",
                    "2026-01-01",
                    "--end-date",
                    "2026-02-01",
                    "--holding-days",
                    "1",
                    "--factor",
                    "custom_factor",
                    "--quantiles",
                    "2",
                    "--out-dir",
                    str(out_dir),
                    "--report-dir",
                    str(report_dir),
                    "--no-manifest",
                ]
                self.assertEqual(0, analyze_factor_forward_returns_main())
            finally:
                sys.argv = original_argv

            summary_rows = read_csv(out_dir / "factor_forward_returns_202601_202602_1d.csv")
            factor_data_rows = read_csv(out_dir / "alphalens_factor_data_202601_202602_1d.csv")
            report_text = (report_dir / "factor_forward_returns_202601_202602_1d.md").read_text(encoding="utf-8")

            self.assertEqual(2, len(summary_rows))
            self.assertEqual("2", summary_rows[0]["quantile_count"])
            self.assertIn("quantile_1_return", summary_rows[0])
            self.assertIn("top_quantile_turnover", summary_rows[1])
            self.assertNotEqual("", summary_rows[1]["rank_autocorr"])
            self.assertEqual(8, len(factor_data_rows))
            top_january = [
                row for row in factor_data_rows
                if row["date"] == "2026-01-01" and row["asset"] == "D"
            ][0]
            self.assertEqual("2", top_january["factor_quantile"])
            self.assertEqual("0.04", top_january["forward_return_1d"])
            self.assertIn("## Quantile Returns", report_text)
            self.assertIn("rank autocorrelation", report_text)


if __name__ == "__main__":
    unittest.main()
