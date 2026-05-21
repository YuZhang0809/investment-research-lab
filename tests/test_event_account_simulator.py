from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_jquants_statement_event_panel import normalize_rows as normalize_statement_events  # noqa: E402
from research_common import read_csv  # noqa: E402
from run_event_account_simulator import run_simulation  # noqa: E402


def price_row(code: str, date: str, open_value: str, close_value: str, tradable: str = "true") -> dict[str, str]:
    return {
        "code": code,
        "date": date,
        "adjusted_open": open_value,
        "adjusted_close": close_value,
        "tradable_flag": tradable,
    }


def jquants_price_row(code: str, date: str, open_value: str, close_value: str, tradable: str = "true") -> dict[str, str]:
    return {
        "Code": code,
        "Date": date,
        "AdjustmentOpen": open_value,
        "AdjustmentClose": close_value,
        "TradableFlag": tradable,
    }


def event_row(event_id: str = "evt1", announcement: str = "2026-01-01 15:00:00", code: str = "1001") -> dict[str, str]:
    return {
        "event_id": event_id,
        "announcement_datetime": announcement,
        "code": code,
        "event_label": "financial_statement",
    }


class EventAccountSimulatorTest(unittest.TestCase):
    def test_statement_event_builder_normalizes_standard_fields(self) -> None:
        rows = [
            {
                "LocalCode": "1001",
                "DisclosedDate": "2026-02-01",
                "DisclosedTime": "15:00:00",
                "TypeOfDocument": "FYFinancialStatements_Consolidated_IFRS",
                "TypeOfCurrentPeriod": "FY",
                "CurrentPeriodEndDate": "2025-12-31",
                "DisclosureNumber": "20260201000001",
            },
            {
                "LocalCode": "1002",
                "DisclosedDate": "2026-02-02",
                "DisclosedTime": "15:00:00",
                "TypeOfDocument": "DividendForecastRevision",
                "DisclosureNumber": "20260202000001",
                "ForecastDividendPerShareAnnual": "12",
            },
        ]

        events = normalize_statement_events(rows)

        self.assertEqual("financial_statement", events[0]["event_label"])
        self.assertEqual("dividend_forecast_revision", events[1]["event_label"])
        self.assertEqual("2026-02-01 15:00:00", events[0]["announcement_datetime"])
        self.assertEqual("12", events[1]["forecast_dividend_per_share"])

    def test_tplus1_open_entry_has_no_prefill_return(self) -> None:
        prices = [
            price_row("1001", "2026-01-01", "50", "50"),
            price_row("1001", "2026-01-02", "100", "110"),
            price_row("1001", "2026-01-03", "120", "121"),
        ]

        rows = run_simulation(
            [event_row()],
            prices,
            run_label="synthetic",
            initial_capital=10_000,
            target_event_weight=1.0,
            max_concurrent_positions=10,
            lot_size=1,
            commission_bps=0,
            tax_rate=0,
            entry_lag_trading_days=1,
            entry_price_mode="next_open",
            holding_trading_days=1,
            exit_price_mode="close",
        )

        self.assertEqual("10000", rows["equity"][0]["equity"])
        self.assertEqual("2026-01-01", rows["equity"][0]["date"])
        self.assertEqual("2026-01-02", rows["trades"][0]["execution_date"])
        self.assertEqual("BUY", rows["trades"][0]["side"])
        self.assertEqual("100", rows["trades"][0]["price"])
        self.assertEqual("2026-01-03", rows["trades"][1]["execution_date"])
        self.assertEqual("SELL", rows["trades"][1]["side"])
        self.assertEqual("12100", rows["summary"][0]["final_equity"])
        self.assertEqual("0.21", rows["positions"][0]["gross_return"])

    def test_jquants_date_headers_build_calendar_and_prices(self) -> None:
        prices = [
            jquants_price_row("1001", "2026-01-01", "50", "50"),
            jquants_price_row("1001", "2026-01-02", "100", "110"),
            jquants_price_row("1001", "2026-01-03", "120", "121"),
        ]

        rows = run_simulation(
            [event_row()],
            prices,
            run_label="synthetic",
            initial_capital=10_000,
            target_event_weight=1.0,
            max_concurrent_positions=10,
            lot_size=1,
            commission_bps=0,
            tax_rate=0,
            entry_lag_trading_days=1,
            entry_price_mode="next_open",
            holding_trading_days=1,
            exit_price_mode="close",
        )

        self.assertEqual("2026-01-02", rows["trades"][0]["execution_date"])
        self.assertEqual("12100", rows["summary"][0]["final_equity"])

    def test_missing_next_open_skips_entry_without_forward_fill(self) -> None:
        prices = [
            price_row("1001", "2026-01-01", "50", "50"),
            price_row("1001", "2026-01-02", "", "110"),
            price_row("1001", "2026-01-03", "120", "121"),
        ]

        rows = run_simulation(
            [event_row()],
            prices,
            run_label="synthetic",
            initial_capital=10_000,
            target_event_weight=1.0,
            max_concurrent_positions=10,
            lot_size=1,
            commission_bps=0,
            tax_rate=0,
            entry_lag_trading_days=1,
            entry_price_mode="next_open",
            holding_trading_days=1,
            exit_price_mode="close",
        )

        self.assertEqual([], rows["trades"])
        self.assertEqual("missing_entry_price", rows["failures"][0]["failure_type"])
        self.assertEqual("10000", rows["summary"][0]["final_equity"])

    def test_duplicate_event_id_is_rejected_before_trading(self) -> None:
        prices = [
            price_row("1001", "2026-01-01", "50", "50"),
            price_row("1001", "2026-01-02", "100", "100"),
            price_row("1001", "2026-01-03", "100", "100"),
            price_row("1002", "2026-01-01", "50", "50"),
            price_row("1002", "2026-01-02", "100", "100"),
            price_row("1002", "2026-01-03", "100", "100"),
        ]

        with self.assertRaisesRegex(ValueError, "Duplicate event_id"):
            run_simulation(
                [
                    event_row(event_id="dup", code="1001"),
                    event_row(event_id="dup", code="1002"),
                ],
                prices,
                run_label="synthetic",
                initial_capital=10_000,
                target_event_weight=0.5,
                max_concurrent_positions=10,
                lot_size=1,
                commission_bps=0,
                tax_rate=0,
                entry_lag_trading_days=1,
                entry_price_mode="next_open",
                holding_trading_days=1,
                exit_price_mode="close",
            )

    def test_missing_entry_date_emits_single_failure(self) -> None:
        rows = run_simulation(
            [event_row()],
            [price_row("1001", "2026-01-01", "100", "100")],
            run_label="synthetic",
            initial_capital=10_000,
            target_event_weight=1.0,
            max_concurrent_positions=10,
            lot_size=1,
            commission_bps=0,
            tax_rate=0,
            entry_lag_trading_days=1,
            entry_price_mode="next_open",
            holding_trading_days=1,
            exit_price_mode="close",
        )

        self.assertEqual(["missing_entry_date"], [row["failure_type"] for row in rows["failures"]])

    def test_commission_and_tax_reduce_final_equity(self) -> None:
        rows = run_simulation(
            [event_row()],
            [
                price_row("1001", "2026-01-01", "50", "50"),
                price_row("1001", "2026-01-02", "100", "100"),
                price_row("1001", "2026-01-03", "120", "120"),
            ],
            run_label="synthetic",
            initial_capital=10_000,
            target_event_weight=1.0,
            max_concurrent_positions=10,
            lot_size=1,
            commission_bps=100,
            tax_rate=0.2,
            entry_lag_trading_days=1,
            entry_price_mode="next_open",
            holding_trading_days=1,
            exit_price_mode="close",
        )

        self.assertEqual("99", rows["trades"][0]["shares"])
        self.assertEqual("99", rows["trades"][0]["commission"])
        self.assertEqual("118.8", rows["trades"][1]["commission"])
        self.assertEqual("396", rows["trades"][1]["estimated_tax"])
        self.assertEqual("11366.2", rows["summary"][0]["final_equity"])

    def test_max_concurrent_and_duplicate_open_position_failures(self) -> None:
        prices = [
            price_row("1001", "2026-01-01", "50", "50"),
            price_row("1001", "2026-01-02", "100", "100"),
            price_row("1001", "2026-01-03", "100", "100"),
            price_row("1001", "2026-01-04", "100", "100"),
            price_row("1002", "2026-01-01", "50", "50"),
            price_row("1002", "2026-01-02", "100", "100"),
            price_row("1002", "2026-01-03", "100", "100"),
            price_row("1002", "2026-01-04", "100", "100"),
        ]

        capped = run_simulation(
            [
                event_row(event_id="evt1", code="1001"),
                event_row(event_id="evt2", code="1002"),
            ],
            prices,
            run_label="synthetic",
            initial_capital=10_000,
            target_event_weight=0.5,
            max_concurrent_positions=1,
            lot_size=1,
            commission_bps=0,
            tax_rate=0,
            entry_lag_trading_days=1,
            entry_price_mode="next_open",
            holding_trading_days=2,
            exit_price_mode="close",
        )
        self.assertIn("max_concurrent_positions", [row["failure_type"] for row in capped["failures"]])

        duplicate = run_simulation(
            [
                event_row(event_id="evt1", code="1001"),
                event_row(event_id="evt2", code="1001"),
            ],
            prices,
            run_label="synthetic",
            initial_capital=10_000,
            target_event_weight=0.5,
            max_concurrent_positions=10,
            lot_size=1,
            commission_bps=0,
            tax_rate=0,
            entry_lag_trading_days=1,
            entry_price_mode="next_open",
            holding_trading_days=2,
            exit_price_mode="close",
        )
        self.assertIn("duplicate_open_position", [row["failure_type"] for row in duplicate["failures"]])

    def test_standard_daily_bar_mode_rejects_same_day_entry_lag(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1"):
            run_simulation(
                [event_row()],
                [price_row("1001", "2026-01-01", "100", "100")],
                run_label="synthetic",
                initial_capital=10_000,
                target_event_weight=1.0,
                max_concurrent_positions=10,
                lot_size=1,
                commission_bps=0,
                tax_rate=0,
                entry_lag_trading_days=0,
                entry_price_mode="next_open",
                holding_trading_days=1,
                exit_price_mode="close",
            )

    def test_cli_writes_account_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            events_path = temp / "events.csv"
            prices_path = temp / "prices.csv"
            out_dir = temp / "out"
            with events_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(event_row()))
                writer.writeheader()
                writer.writerow(event_row())
            with prices_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(price_row("1001", "2026-01-01", "50", "50")))
                writer.writeheader()
                writer.writerows(
                    [
                        price_row("1001", "2026-01-01", "50", "50"),
                        price_row("1001", "2026-01-02", "100", "110"),
                        price_row("1001", "2026-01-03", "120", "121"),
                    ]
                )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_event_account_simulator.py"),
                    "--events",
                    str(events_path),
                    "--prices",
                    str(prices_path),
                    "--out-dir",
                    str(out_dir),
                    "--run-label",
                    "synthetic",
                    "--initial-capital",
                    "10000",
                    "--target-event-weight",
                    "1",
                    "--lot-size",
                    "1",
                    "--holding-trading-days",
                    "1",
                    "--no-manifest",
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("Wrote event account simulation outputs", result.stdout)
            self.assertEqual("12100", read_csv(out_dir / "event_account_summary_synthetic.csv")[0]["final_equity"])


if __name__ == "__main__":
    unittest.main()
