from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from download_jquants_market_benchmark import compact_date, convert_topix  # noqa: E402


class JQuantsMarketBenchmarkTest(unittest.TestCase):
    def test_convert_topix_writes_market_benchmark_contract(self) -> None:
        rows = [
            {"Date": "2026-01-05", "Open": 1000.0, "High": 1010.0, "Low": 990.0, "Close": 1005.0},
            {"Date": "2026-01-06 00:00:00", "Open": 1005.0, "High": 1020.0, "Low": 1000.0, "Close": 1015.0},
        ]

        converted = convert_topix(rows)

        self.assertEqual("2026-01-05", converted[0]["date"])
        self.assertEqual("TOPIX", converted[0]["benchmark_id"])
        self.assertEqual(1005.0, converted[0]["close"])
        self.assertEqual(1005.0, converted[0]["adjusted_close"])
        self.assertEqual("2026-01-06", converted[1]["date"])

    def test_compact_date_accepts_iso_or_compact(self) -> None:
        self.assertEqual("20260517", compact_date("2026-05-17"))
        self.assertEqual("20260517", compact_date("20260517"))


if __name__ == "__main__":
    unittest.main()
