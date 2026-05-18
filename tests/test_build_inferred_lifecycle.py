from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from build_inferred_lifecycle import build_inferred_lifecycle_from_frames  # noqa: E402


class BuildInferredLifecycleTest(unittest.TestCase):
    def test_infers_active_and_terminal_lifecycle_from_snapshots_and_prices(self) -> None:
        listings = pd.DataFrame(
            [
                {
                    "code": "1001",
                    "name": "Active",
                    "source_date": "2026-01-31",
                    "listed_date": "",
                    "delisted_date": "",
                    "security_type": "common_stock",
                    "is_common_stock": "true",
                    "is_etf_reit_infra": "false",
                    "tradable_flag": "",
                    "lot_size": "100",
                    "source": "snapshot",
                },
                {
                    "code": "1001",
                    "name": "Active",
                    "source_date": "2026-02-28",
                    "listed_date": "",
                    "delisted_date": "",
                    "security_type": "common_stock",
                    "is_common_stock": "true",
                    "is_etf_reit_infra": "false",
                    "tradable_flag": "",
                    "lot_size": "100",
                    "source": "snapshot",
                },
                {
                    "code": "2002",
                    "name": "Terminal",
                    "source_date": "2026-01-31",
                    "listed_date": "",
                    "delisted_date": "",
                    "security_type": "common_stock",
                    "is_common_stock": "true",
                    "is_etf_reit_infra": "false",
                    "tradable_flag": "",
                    "lot_size": "100",
                    "source": "snapshot",
                },
            ]
        )
        prices = pd.DataFrame(
            [
                {
                    "code": "1001",
                    "date": "2026-01-10",
                    "unadjusted_close": "100",
                    "adjusted_close": "100",
                    "trading_value": "100000",
                    "tradable_flag": "true",
                },
                {
                    "code": "1001",
                    "date": "2026-02-27",
                    "unadjusted_close": "110",
                    "adjusted_close": "110",
                    "trading_value": "100000",
                    "tradable_flag": "true",
                },
                {
                    "code": "2002",
                    "date": "2026-01-15",
                    "unadjusted_close": "200",
                    "adjusted_close": "200",
                    "trading_value": "100000",
                    "tradable_flag": "true",
                },
            ]
        )

        lifecycle, enriched = build_inferred_lifecycle_from_frames(listings, prices)
        by_code = lifecycle.set_index("code").to_dict(orient="index")

        self.assertEqual("active", by_code["1001"]["lifecycle_status"])
        self.assertEqual("pit_inferred_lifecycle_active", enriched[enriched["code"] == "1001"].iloc[0]["listing_lifecycle_status"])
        self.assertEqual("2026-01-10", by_code["1001"]["inferred_listed_date"])
        self.assertEqual("", by_code["1001"]["inferred_delisted_date"])

        self.assertEqual("delisted", by_code["2002"]["lifecycle_status"])
        self.assertEqual("high", by_code["2002"]["lifecycle_confidence"])
        self.assertEqual("2026-02-28", by_code["2002"]["inferred_delisted_date"])
        self.assertEqual("2026-01-15", by_code["2002"]["inferred_last_trading_date"])
        terminal = enriched[enriched["code"] == "2002"].iloc[0]
        self.assertEqual("pit_inferred_lifecycle_terminal", terminal["listing_lifecycle_status"])
        self.assertEqual("2026-02-28", terminal["delisted_date"])
        self.assertEqual("2026-01-15", terminal["last_trading_date"])

    def test_non_tradable_price_rows_do_not_create_price_evidence(self) -> None:
        listings = pd.DataFrame(
            [
                {
                    "code": "1001",
                    "source_date": "2026-01-31",
                    "listed_date": "",
                    "delisted_date": "",
                }
            ]
        )
        prices = pd.DataFrame(
            [
                {
                    "code": "1001",
                    "date": "2026-01-10",
                    "unadjusted_close": "",
                    "adjusted_close": "",
                    "trading_value": "0",
                    "tradable_flag": "false",
                }
            ]
        )

        lifecycle, _enriched = build_inferred_lifecycle_from_frames(listings, prices)

        row = lifecycle.iloc[0].to_dict()
        self.assertEqual("", row["first_price_date"])
        self.assertIn("no_price_evidence", row["evidence_flags"].split("|"))


if __name__ == "__main__":
    unittest.main()
