from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from profile_price_volume_factor_panel import (  # noqa: E402
    profile_price_volume_factor_panel,
    write_synthetic_inputs,
)


class PriceVolumeProfileTest(unittest.TestCase):
    def test_synthetic_profile_reports_scale_memory_and_audit_rates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices, universe, rebalance_values, price_rows, universe_rows = write_synthetic_inputs(
                temp,
                codes=6,
                days=90,
                rebalances=3,
                table_format="parquet",
            )

            summary, panel, fields = profile_price_volume_factor_panel(
                prices_path=prices,
                universe_panel_path=universe,
                rebalance_dates_path=None,
                rebalance_date_values=rebalance_values,
                group_field="sector",
                input_format="parquet",
                run_label="synthetic_profile_test",
                synthetic=True,
                price_rows=price_rows,
                universe_rows=universe_rows,
            )

            self.assertEqual(price_rows, summary["price_rows"])
            self.assertEqual(universe_rows, summary["universe_panel_rows"])
            self.assertEqual(3, summary["rebalance_count"])
            self.assertEqual(18, summary["output_rows"])
            self.assertIn("peak_python_memory_mb", summary)
            self.assertIn("coverage_clean_rate", summary)
            self.assertIn("vwap_proxy_ok_count", summary)
            self.assertEqual(18, len(panel))
            self.assertIn("wq_alpha_101_proxy", fields)


if __name__ == "__main__":
    unittest.main()
