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

from build_derived_fundamental_factor_panel import build_panel  # noqa: E402
from research_common import read_csv  # noqa: E402


def row(
    *,
    code: str = "1001",
    available_date: str,
    available_time: str = "15:00:00",
    period_type: str = "annual",
    period_end: str,
    sales: str = "100",
    operating_profit: str = "10",
    net_profit: str = "8",
    equity: str = "40",
    total_assets: str = "100",
    shares_outstanding: str = "1000",
    consolidated_flag: str = "consolidated",
    disclosure_number: str = "A",
) -> dict[str, str]:
    return {
        "code": code,
        "available_date": available_date,
        "available_time": available_time,
        "period_type": period_type,
        "period_end": period_end,
        "sales": sales,
        "operating_profit": operating_profit,
        "net_profit": net_profit,
        "equity": equity,
        "total_assets": total_assets,
        "shares_outstanding": shares_outstanding,
        "consolidated_flag": consolidated_flag,
        "disclosure_number": disclosure_number,
        "document_type": "FinancialStatement",
    }


class DerivedFundamentalFactorPanelTest(unittest.TestCase):
    def test_rebalance_panel_computes_yoy_without_lookahead(self) -> None:
        rows = [
            row(available_date="2025-02-01", period_end="2024-12-31", sales="100", operating_profit="10", net_profit="8", equity="40", total_assets="100", shares_outstanding="1000"),
            row(available_date="2026-02-01", period_end="2025-12-31", sales="120", operating_profit="15", net_profit="12", equity="60", total_assets="150", shares_outstanding="1100"),
            row(available_date="2026-04-01", period_end="2026-12-31", sales="999", operating_profit="999", net_profit="999", equity="999", total_assets="999", shares_outstanding="999"),
        ]

        panel = build_panel(rows, panel_mode="rebalance", rebalance_dates=[date(2026, 3, 31)])

        self.assertEqual(1, len(panel))
        self.assertEqual("2026-03-31", panel[0]["rebalance_date"])
        self.assertAlmostEqual(0.2, float(panel[0]["sales_yoy"]))
        self.assertAlmostEqual(0.5, float(panel[0]["operating_profit_yoy"]))
        self.assertAlmostEqual(0.125, float(panel[0]["operating_margin"]))
        self.assertAlmostEqual(0.025, float(panel[0]["operating_margin_delta_yoy"]))
        self.assertAlmostEqual(0.2, float(panel[0]["roe"]))
        self.assertNotIn("missing_prior_year", panel[0]["missing_flags"])

    def test_quarterly_missing_prior_and_scope_are_respected(self) -> None:
        rows = [
            row(
                available_date="2025-05-01",
                period_type="q1",
                period_end="2025-03-31",
                sales="100",
                consolidated_flag="non_consolidated",
            ),
            row(
                available_date="2026-05-01",
                period_type="q1",
                period_end="2026-03-31",
                sales="120",
                consolidated_flag="consolidated",
            ),
        ]

        panel = build_panel(rows, panel_mode="event")
        current = [item for item in panel if item["available_date"] == "2026-05-01"][0]

        self.assertEqual("", current["sales_yoy"])
        self.assertIn("missing_prior_year", current["missing_flags"])

    def test_duplicate_disclosures_are_deduped_and_restatements_are_pit(self) -> None:
        rows = [
            row(available_date="2025-02-01", period_end="2024-12-31", sales="100"),
            row(available_date="2026-02-01", period_end="2025-12-31", sales="110", disclosure_number="A"),
            row(available_date="2026-02-01", period_end="2025-12-31", sales="115", disclosure_number="B"),
            row(available_date="2026-03-01", period_end="2025-12-31", sales="130", disclosure_number="C"),
        ]

        panel = build_panel(rows, panel_mode="rebalance", rebalance_dates=[date(2026, 2, 15), date(2026, 3, 15)])
        by_date = {item["rebalance_date"]: item for item in panel}

        self.assertEqual("115", by_date["2026-02-15"]["sales"])
        self.assertEqual(2, by_date["2026-02-15"]["source_duplicate_count"])
        self.assertEqual("130", by_date["2026-03-15"]["sales"])
        self.assertAlmostEqual(0.15, float(by_date["2026-02-15"]["sales_yoy"]))
        self.assertAlmostEqual(0.3, float(by_date["2026-03-15"]["sales_yoy"]))

    def test_event_panel_is_unique_by_code_available_date(self) -> None:
        rows = [
            row(available_date="2025-02-01", period_end="2024-12-31", sales="100"),
            row(available_date="2026-02-01", available_time="12:00:00", period_end="2025-12-31", sales="110"),
            row(available_date="2026-02-01", available_time="15:00:00", period_end="2025-12-31", sales="120"),
        ]

        panel = build_panel(rows, panel_mode="event")
        current = [item for item in panel if item["available_date"] == "2026-02-01"][0]

        self.assertEqual(2, len(panel))
        self.assertEqual("120", current["sales"])
        self.assertEqual(2, current["source_disclosure_count"])

    def test_cli_writes_csv_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fundamentals = temp / "fundamentals.csv"
            out = temp / "derived.csv"
            with fundamentals.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(row(available_date="2025-02-01", period_end="2024-12-31").keys()))
                writer.writeheader()
                writer.writerow(row(available_date="2025-02-01", period_end="2024-12-31"))
                writer.writerow(row(available_date="2026-02-01", period_end="2025-12-31", sales="125"))

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_derived_fundamental_factor_panel.py"),
                    "--fundamentals",
                    str(fundamentals),
                    "--panel-mode",
                    "rebalance",
                    "--rebalance-date",
                    "2026-03-31",
                    "--out",
                    str(out),
                    "--output-format",
                    "csv",
                    "--no-manifest",
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Wrote 1 derived fundamental factor rows", result.stdout)
            self.assertEqual("0.25", read_csv(out)[0]["sales_yoy"])


if __name__ == "__main__":
    unittest.main()
