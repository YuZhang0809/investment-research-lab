from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_price_defensive_factor_panel import build_panel  # noqa: E402
from research_common import read_csv  # noqa: E402


def price_path(
    *,
    code: str,
    start: date,
    returns: list[float],
    initial: float = 100.0,
    price_limit_last: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    value = initial
    rows.append(
        {
            "date": start.isoformat(),
            "code": code,
            "adjusted_close": f"{value:.8f}",
            "unadjusted_close": f"{value:.8f}",
            "price_limit_flag": "false",
        }
    )
    for index, ret in enumerate(returns, start=1):
        value *= 1.0 + ret
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "code": code,
                "adjusted_close": f"{value:.8f}",
                "unadjusted_close": f"{value:.8f}",
                "price_limit_flag": "true" if price_limit_last and index == len(returns) else "false",
            }
        )
    return rows


class PriceDefensiveFactorPanelTest(unittest.TestCase):
    def test_computes_realized_vol_drawdown_and_beta(self) -> None:
        benchmark_returns = [0.01 if index % 2 else -0.005 for index in range(1, 260)]
        stock_returns = [2.0 * value for value in benchmark_returns]
        start = date(2025, 1, 1)
        rows = price_path(code="1001", start=start, returns=stock_returns)
        benchmark = price_path(code="TOPIX", start=start, returns=benchmark_returns)

        panel = build_panel(
            rows,
            rebalance_dates=[start + timedelta(days=len(stock_returns))],
            benchmark_rows=benchmark,
            min_beta_observations=60,
        )

        self.assertEqual(1, len(panel))
        self.assertGreater(float(panel[0]["realized_vol_3m"]), 0)
        self.assertGreater(float(panel[0]["downside_vol_6m"]), 0)
        self.assertLess(float(panel[0]["max_drawdown_6m"]), 0)
        self.assertAlmostEqual(2.0, float(panel[0]["beta_to_benchmark"]), places=6)
        self.assertNotIn("insufficient_history_12m", panel[0]["missing_flags"])

    def test_insufficient_history_and_execution_filter_flags(self) -> None:
        start = date(2026, 1, 1)
        rows = price_path(code="1001", start=start, returns=[0.01] * 20, price_limit_last=True)

        panel = build_panel(
            rows,
            rebalance_dates=[start + timedelta(days=25)],
            stale_filter_days=1,
            flag_price_limit=True,
        )

        self.assertEqual("true", panel[0]["latest_price_stale"])
        self.assertIn("stale_price", panel[0]["defensive_filter_reasons"])
        self.assertIn("price_limit_hit", panel[0]["defensive_filter_reasons"])
        self.assertIn("insufficient_history_3m", panel[0]["missing_flags"])
        self.assertIn("missing_beta_to_benchmark", panel[0]["missing_flags"])
        self.assertEqual("", panel[0]["realized_vol_3m"])
        self.assertEqual("", panel[0]["realized_vol_6m"])
        self.assertEqual("", panel[0]["realized_vol_12m"])

    def test_cli_writes_csv_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            out = temp / "defensive.csv"
            rows = price_path(code="1001", start=date(2026, 1, 1), returns=[0.001] * 70)
            with prices.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_price_defensive_factor_panel.py"),
                    "--prices",
                    str(prices),
                    "--rebalance-date",
                    "2026-03-12",
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

            self.assertIn("Wrote 1 price defensive factor rows", result.stdout)
            self.assertEqual("1001", read_csv(out)[0]["code"])


if __name__ == "__main__":
    unittest.main()
