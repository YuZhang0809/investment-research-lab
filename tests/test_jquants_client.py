from __future__ import annotations

import unittest

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jquants_client import bulk_month_token, filter_bulk_rows, normalize_month_token  # noqa: E402


class JQuantsClientHelpersTest(unittest.TestCase):
    def test_normalizes_month_tokens(self) -> None:
        self.assertEqual("202605", normalize_month_token("2026-05"))
        self.assertEqual("202605", normalize_month_token("20260531"))

    def test_extracts_month_token_from_bulk_key(self) -> None:
        self.assertEqual("202605", bulk_month_token("fins/summary/2026-05.csv.gz"))
        self.assertEqual("202405", bulk_month_token("fins_summary_202405.csv.gz"))

    def test_filters_bulk_rows_by_month_token_when_available(self) -> None:
        rows = [
            {"Key": "fins/summary/2024-12.csv.gz"},
            {"Key": "fins/summary/2025-01.csv.gz"},
            {"Key": "fins/summary/2025-02.csv.gz"},
        ]
        filtered = filter_bulk_rows(rows, "2025-01", "2025-01")
        self.assertEqual([{"Key": "fins/summary/2025-01.csv.gz"}], filtered)


if __name__ == "__main__":
    unittest.main()
