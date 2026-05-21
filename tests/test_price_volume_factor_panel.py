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

from build_price_volume_factor_panel import (  # noqa: E402
    ALPHA_FIELDS,
    build_panel,
    cross_sectional_rank_pct,
    decay_linear,
    normalize_prices,
    rolling_arg,
    rolling_cov,
    safe_divide,
    trim_price_history,
    ts_rank,
)
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

    def test_blank_adjusted_close_falls_back_row_wise_to_unadjusted_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = synthetic_prices(80)
            for row in rows:
                row["adjusted_close"] = ""
            write_rows(prices, rows)

            panel, _fields = build_panel(prices, rebalance_date_values=["2026-03-01"], input_format="csv")
            row = panel[panel["code"] == "1001"].iloc[0]

            self.assertEqual("unadjusted_close", row["effective_close_source"])
            self.assertEqual("adjusted_close_fallback_used", row["effective_close_flag"])
            self.assertIn("adjusted_close_fallback_used", row["coverage_flags"])
            self.assertNotEqual("", row["effective_close"])
            self.assertNotEqual("", row["returns"])
            self.assertNotEqual("", row["wq_alpha_033_proxy"])
            self.assertNotEqual("", row["wq_alpha_034_proxy"])

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

    def test_cross_sectional_rank_pct_handles_three_code_date(self) -> None:
        pd = __import__("pandas")
        ranks = cross_sectional_rank_pct(
            pd.Series([30.0, 10.0, 20.0]),
            pd.Series(["2026-01-31", "2026-01-31", "2026-01-31"]),
        )

        self.assertEqual([1.0, 0.0, 0.5], [float(value) for value in ranks])

    def test_operator_layer_covariance_arg_and_decay_are_deterministic(self) -> None:
        pd = __import__("pandas")
        frame = pd.DataFrame(
            {
                "code": ["1001", "1001", "1001"],
                "x": [1.0, 2.0, 3.0],
                "y": [2.0, 4.0, 6.0],
            }
        )

        cov = rolling_cov(frame, "x", "y", 3)
        argmax = rolling_arg(frame, "x", 3, which="max")
        argmin = rolling_arg(frame, "x", 3, which="min")
        decay = decay_linear(frame, "x", 3)
        divided = safe_divide(pd.Series([1.0, 2.0]), pd.Series([0.0, 4.0]))

        self.assertAlmostEqual(2.0, float(cov.iloc[-1]))
        self.assertAlmostEqual(3.0, float(argmax.iloc[-1]))
        self.assertAlmostEqual(1.0, float(argmin.iloc[-1]))
        self.assertAlmostEqual(14.0 / 6.0, float(decay.iloc[-1]))
        self.assertTrue(pd.isna(divided.iloc[0]))
        self.assertAlmostEqual(0.5, float(divided.iloc[1]))

    def test_single_code_input_does_not_crash_rolling_correlation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = [row for row in synthetic_prices(80) if row["code"] == "1001"]
            write_rows(prices, rows)

            panel, _fields = build_panel(prices, rebalance_date_values=["2026-03-01"], input_format="csv")

            self.assertEqual(["1001"], panel["code"].tolist())
            self.assertEqual("2026-03-01", panel.iloc[0]["latest_price_date"])
            self.assertNotEqual("", panel.iloc[0]["wq_alpha_101_proxy"])

    def test_universe_code_without_price_preserves_key_and_missing_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            universe = temp / "universe.csv"
            rows = [row for row in synthetic_prices(30) if row["code"] == "1001"]
            write_rows(prices, rows)
            write_rows(
                universe,
                [
                    {"rebalance_date": "2026-01-30", "code": "1001", "included_flag": "true"},
                    {"rebalance_date": "2026-01-30", "code": "9999", "included_flag": "true"},
                ],
            )

            panel, _fields = build_panel(
                prices,
                universe_panel_path=universe,
                rebalance_date_values=["2026-01-30"],
                input_format="csv",
            )
            row = panel[panel["code"] == "9999"].iloc[0]

            self.assertEqual("9999", row["code"])
            self.assertEqual("", row["latest_price_date"])
            self.assertIn("missing_price_on_or_before_rebalance", row["missing_flags"])

    def test_group_field_can_be_preserved_from_prices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = synthetic_prices(30)
            for row in rows:
                row["sector"] = "Tech" if row["code"] == "1001" else "Utility"
            write_rows(prices, rows)

            panel, fields = build_panel(
                prices,
                rebalance_date_values=["2026-01-30"],
                group_field="sector",
                input_format="csv",
            )
            by_code = {row["code"]: row for row in panel.to_dict(orient="records")}

            self.assertIn("sector", fields)
            self.assertEqual("Tech", by_code["1001"]["sector"])
            self.assertEqual("Utility", by_code["1002"]["sector"])

    def test_trim_price_history_limits_universe_codes_and_lookback_window(self) -> None:
        pd = __import__("pandas")
        prices = pd.DataFrame(
            {
                "code": ["1001", "1001", "1001", "1002"],
                "date": [date(2025, 5, 1), date(2025, 8, 1), date(2026, 1, 31), date(2026, 1, 31)],
            }
        )
        universe = pd.DataFrame({"rebalance_date": [date(2026, 1, 31)], "code": ["1001"]})

        trimmed = trim_price_history(prices, [date(2026, 1, 31)], universe)

        self.assertEqual(["1001", "1001"], trimmed["code"].tolist())
        self.assertEqual([date(2025, 8, 1), date(2026, 1, 31)], trimmed["date"].tolist())

    def test_universe_group_field_takes_precedence_over_price_group_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            universe = temp / "universe.csv"
            rows = synthetic_prices(30)
            for row in rows:
                row["sector"] = "PriceSector"
            write_rows(prices, rows)
            write_rows(
                universe,
                [
                    {"rebalance_date": "2026-01-30", "code": "1001", "included_flag": "true", "sector": "UniverseSector"},
                ],
            )

            panel, _fields = build_panel(
                prices,
                universe_panel_path=universe,
                rebalance_date_values=["2026-01-30"],
                group_field="sector",
                input_format="csv",
            )

            self.assertEqual("UniverseSector", panel.iloc[0]["sector"])

    def test_alpha_012_sign_chain_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = [
                {
                    "date": "2026-01-01",
                    "code": "1001",
                    "unadjusted_open": "10",
                    "unadjusted_high": "12",
                    "unadjusted_low": "9",
                    "unadjusted_close": "10",
                    "adjusted_close": "10",
                    "volume": "100",
                    "trading_value": "1000",
                    "price_limit_flag": "false",
                },
                {
                    "date": "2026-01-02",
                    "code": "1001",
                    "unadjusted_open": "11",
                    "unadjusted_high": "13",
                    "unadjusted_low": "10",
                    "unadjusted_close": "12",
                    "adjusted_close": "12",
                    "volume": "110",
                    "trading_value": "1320",
                    "price_limit_flag": "false",
                },
                {
                    "date": "2026-01-03",
                    "code": "1001",
                    "unadjusted_open": "12",
                    "unadjusted_high": "13",
                    "unadjusted_low": "10",
                    "unadjusted_close": "11",
                    "adjusted_close": "11",
                    "volume": "90",
                    "trading_value": "990",
                    "price_limit_flag": "false",
                },
            ]
            write_rows(prices, rows)

            panel, _fields = build_panel(
                prices,
                rebalance_date_values=["2026-01-02", "2026-01-03"],
                input_format="csv",
            )
            by_date = {row["rebalance_date"]: row for row in panel.to_dict(orient="records")}

            self.assertAlmostEqual(-2.0, float(by_date["2026-01-02"]["wq_alpha_012_proxy"]))
            self.assertAlmostEqual(-1.0, float(by_date["2026-01-03"]["wq_alpha_012_proxy"]))

    def test_coverage_flags_include_price_limit_and_short_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            prices = temp / "prices.csv"
            rows = synthetic_prices(10)
            for row in rows:
                if row["code"] == "1001" and row["date"] == "2026-01-10":
                    row["price_limit_flag"] = "true"
            write_rows(prices, rows)

            panel, _fields = build_panel(prices, rebalance_date_values=["2026-01-10"], input_format="csv")
            row = panel[panel["code"] == "1001"].iloc[0]

            self.assertIn("price_limit_flag", row["coverage_flags"])
            self.assertIn("insufficient_adv20", row["coverage_flags"])
            self.assertIn("insufficient_adv60", row["coverage_flags"])

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
