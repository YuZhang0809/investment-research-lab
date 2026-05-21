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

from build_price_volume_factor_panel import ALPHA_FIELDS, build_panel, normalize_prices, ts_rank  # noqa: E402
from external_factor_panels import join_external_factor_panels  # noqa: E402
from research_common import read_csv, write_table  # noqa: E402


def synthetic_prices(days: int = 80) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    start = date(2026, 1, 1)
    for code_index, code in enumerate(["1001", "1002"], start=1):
        for offset in range(days):
            current = start + timedelta(days=offset)
            base = 100 + code_index * 10 + offset * code_index
            open_value = base
            high = base + 3 + code_index
            low = base - 2
            close = base + (1 if offset % 2 else -1) * code_index
            volume = 1000 + offset * 10 + code_index * 100
            trading_value = volume * (base + 0.5)
            rows.append(
                {
                    "date": current.isoformat(),
                    "code": code,
                    "unadjusted_open": f"{open_value:.2f}",
                    "unadjusted_high": f"{high:.2f}",
                    "unadjusted_low": f"{low:.2f}",
                    "unadjusted_close": f"{close:.2f}",
                    "adjusted_close": f"{close:.2f}",
                    "volume": str(volume),
                    "trading_value": f"{trading_value:.2f}",
                    "price_limit_flag": "false",
                }
            )
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class PriceVolumeFactorPanelTest(unittest.TestCase):
    def test_vwap_and_proxy_fields_are_deterministic_without_lookahead(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = synthetic_prices(80)
            write_rows(prices, rows)

            panel, _fields = build_panel(
                prices,
                rebalance_date_values=["2026-02-10"],
                input_format="csv",
            )
            by_code = {row["code"]: row for row in panel.to_dict(orient="records")}

            self.assertEqual("2026-02-10", by_code["1001"]["latest_price_date"])
            source = [row for row in rows if row["code"] == "1001" and row["date"] == "2026-02-10"][0]
            expected_vwap = float(source["trading_value"]) / float(source["volume"])
            self.assertAlmostEqual(expected_vwap, float(by_code["1001"]["vwap_proxy"]))
            self.assertTrue(any(by_code["1001"][field] != "" for field in ALPHA_FIELDS))
            self.assertEqual("price_volume_proxy_v0_1", by_code["1001"]["operator_version"])

    def test_rebalance_date_never_uses_future_prices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = synthetic_prices(10)
            rows.append(
                {
                    **rows[-1],
                    "code": "1001",
                    "date": "2026-03-01",
                    "unadjusted_close": "9999",
                    "adjusted_close": "9999",
                }
            )
            write_rows(prices, rows)

            panel, _fields = build_panel(prices, rebalance_date_values=["2026-01-05"], input_format="csv")

            self.assertEqual("2026-01-05", panel[panel["code"] == "1001"].iloc[0]["latest_price_date"])

    def test_duplicate_code_date_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = synthetic_prices(3)
            rows.append(dict(rows[0]))
            write_rows(prices, rows)

            with self.assertRaisesRegex(ValueError, "Duplicate price row"):
                normalize_prices(prices, "csv")

    def test_missing_and_zero_volume_are_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = synthetic_prices(70)
            for row in rows:
                if row["code"] == "1001" and row["date"] == "2026-03-01":
                    row["volume"] = "0"
            write_rows(prices, rows)

            panel, _fields = build_panel(prices, rebalance_date_values=["2026-03-01"], input_format="csv")
            row = panel[panel["code"] == "1001"].iloc[0]

            self.assertEqual("zero_volume", row["vwap_proxy_flag"])
            self.assertIn("zero_volume", row["missing_flags"])

    def test_ts_rank_operator_uses_only_rolling_window(self) -> None:
        pd = __import__("pandas")
        frame = pd.DataFrame(
            {
                "code": ["1001"] * 5,
                "value": [5.0, 1.0, 3.0, 2.0, 4.0],
            }
        )

        ranks = ts_rank(frame, "value", 3)

        self.assertAlmostEqual(1.0, float(ranks.iloc[-1]))
        self.assertAlmostEqual(0.5, float(ranks.iloc[-2]))

    def test_csv_and_parquet_output_are_semantically_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            csv_out = temp / "panel.csv"
            parquet_out = temp / "panel.parquet"
            rows = synthetic_prices(80)
            write_rows(prices, rows)

            for output, fmt in [(csv_out, "csv"), (parquet_out, "parquet")]:
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "build_price_volume_factor_panel.py"),
                        "--prices",
                        str(prices),
                        "--rebalance-date",
                        "2026-03-01",
                        "--out",
                        str(output),
                        "--output-format",
                        fmt,
                        "--no-manifest",
                    ],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            csv_rows = read_csv(csv_out)
            parquet_rows = read_csv(parquet_out)
            self.assertEqual(csv_rows[0]["code"], parquet_rows[0]["code"])
            self.assertAlmostEqual(float(csv_rows[0]["vwap_proxy"]), float(parquet_rows[0]["vwap_proxy"]))
            self.assertAlmostEqual(float(csv_rows[0]["wq_alpha_101_proxy"]), float(parquet_rows[0]["wq_alpha_101_proxy"]))

    def test_generated_panel_can_join_as_external_factor_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            panel_path = temp / "panel.csv"
            rows = synthetic_prices(80)
            write_rows(prices, rows)
            panel, fields = build_panel(prices, rebalance_date_values=["2026-03-01"], input_format="csv")
            write_table(panel, panel_path, format="csv", fieldnames=fields)

            joined = join_external_factor_panels(
                [{"rebalance_date": "2026-03-01", "code": "1001"}],
                {
                    "external_factor_panels": [
                        {
                            "name": "price_volume",
                            "path": str(panel_path),
                            "join_keys": ["rebalance_date", "code"],
                            "fields": [{"name": "wq_alpha_101_proxy", "dtype": "float"}],
                        }
                    ]
                },
            )

            self.assertNotEqual("", joined[0]["wq_alpha_101_proxy"])


if __name__ == "__main__":
    unittest.main()
