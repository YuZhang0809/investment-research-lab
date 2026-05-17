from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_walkforward_tearsheet  # noqa: E402
from performance_analytics import metric_rows, summarize_walkforward, write_svg_line_chart  # noqa: E402
from research_common import read_csv, write_csv  # noqa: E402


class PerformanceTearsheetTest(unittest.TestCase):
    def test_summarize_walkforward_computes_core_risk_metrics(self) -> None:
        summary = summarize_walkforward(synthetic_summary_rows(), synthetic_failure_rows())

        self.assertEqual("monthly", summary["frequency"])
        self.assertAlmostEqual(0.188, summary["total_return"])
        self.assertAlmostEqual(-0.1, summary["max_drawdown"])
        self.assertAlmostEqual(2 / 3, summary["win_rate"])
        self.assertEqual("market_benchmark", summary["benchmark_label"])
        self.assertEqual(1, summary["failure_counts"]["cash_drag"])

        rows = {row["metric"]: row for row in metric_rows(summary)}
        self.assertEqual("18.80%", rows["total_return"]["formatted_value"])
        self.assertEqual("-10.00%", rows["max_drawdown"]["formatted_value"])
        self.assertEqual("insufficient_sample_count", rows["risk_metric_status"]["value"])
        self.assertEqual("", rows["annualized_return"]["formatted_value"])
        self.assertIn("information_ratio", rows)

    def test_generate_walkforward_tearsheet_writes_metrics_report_and_svg_charts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary_path = temp / "summary.csv"
            failures_path = temp / "failures.csv"
            report_path = temp / "tearsheet.md"
            metrics_path = temp / "metrics.csv"
            chart_dir = temp / "charts"
            write_csv(summary_path, synthetic_summary_rows(), list(synthetic_summary_rows()[0]))
            write_csv(failures_path, synthetic_failure_rows(), ["date", "code", "failure_type", "detail", "value"])

            original_argv = sys.argv[:]
            try:
                sys.argv = [
                    "generate_walkforward_tearsheet.py",
                    "--summary",
                    str(summary_path),
                    "--failures",
                    str(failures_path),
                    "--out",
                    str(report_path),
                    "--metrics-out",
                    str(metrics_path),
                    "--chart-dir",
                    str(chart_dir),
                    "--no-manifest",
                ]
                self.assertEqual(0, generate_walkforward_tearsheet.main())
            finally:
                sys.argv = original_argv

            self.assertTrue(report_path.exists())
            self.assertTrue(metrics_path.exists())
            self.assertTrue((chart_dir / "equity_curve.svg").exists())
            self.assertTrue((chart_dir / "drawdown.svg").exists())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("# Walk-Forward Performance Tear Sheet", report)
            self.assertIn("![Equity curve]", report)

            rows = {row["metric"]: row for row in read_csv(metrics_path)}
            self.assertEqual("18.80%", rows["total_return"]["formatted_value"])
            self.assertEqual("1", rows["cash_drag"]["value"])

    def test_drawdown_starts_from_initial_capital_when_first_period_loses_money(self) -> None:
        rows = synthetic_summary_rows(
            values=[
                ("2026-01-31", "1000", "900", "-0.1", "1000", "0", "1000"),
                ("2026-02-28", "900", "950", "0.0555555556", "1000", "0", "1000"),
            ]
        )

        summary = summarize_walkforward(rows, [])

        self.assertAlmostEqual(-0.1, summary["max_drawdown"])
        self.assertEqual(2, summary["longest_drawdown_periods"])
        self.assertEqual([("2026-01-31", -0.1), ("2026-02-28", -0.05)], [
            (row_date.isoformat(), round(value, 4)) for row_date, value in summary["drawdowns"]
        ])

    def test_zero_final_equity_is_not_dropped_from_total_return(self) -> None:
        rows = synthetic_summary_rows(
            values=[
                ("2026-01-31", "1000", "1100", "0.1", "1000", "0", "1000"),
                ("2026-02-28", "1100", "0", "-1", "1000", "0", "1000"),
            ]
        )

        summary = summarize_walkforward(rows, [])
        metric_by_name = {row["metric"]: row for row in metric_rows(summary)}

        self.assertEqual(0.0, summary["final_equity"])
        self.assertEqual(-1.0, summary["total_return"])
        self.assertEqual("-100.00%", metric_by_name["total_return"]["formatted_value"])
        self.assertEqual("-100.00%", metric_by_name["max_drawdown"]["formatted_value"])

    def test_blank_final_equity_does_not_fall_back_to_previous_equity(self) -> None:
        rows = synthetic_summary_rows(
            values=[
                ("2026-01-31", "1000", "1100", "0.1", "1000", "0", "1000"),
                ("2026-02-28", "1100", "", "", "1000", "0", "1000"),
            ]
        )

        summary = summarize_walkforward(rows, [])
        metric_by_name = {row["metric"]: row for row in metric_rows(summary)}

        self.assertIsNone(summary["final_equity"])
        self.assertIsNone(summary["total_return"])
        self.assertEqual("", metric_by_name["final_equity"]["formatted_value"])
        self.assertEqual("", metric_by_name["total_return"]["formatted_value"])

    def test_tearsheet_prints_lifecycle_warning_at_top(self) -> None:
        rows = synthetic_summary_rows()
        for row in rows:
            row["performance_conclusion_allowed"] = "False"
            row["lifecycle_data_status"] = "snapshot_only"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary_path = temp / "summary.csv"
            report_path = temp / "tearsheet.md"
            write_csv(summary_path, rows, list(rows[0]))

            original_argv = sys.argv[:]
            try:
                sys.argv = [
                    "generate_walkforward_tearsheet.py",
                    "--summary",
                    str(summary_path),
                    "--out",
                    str(report_path),
                    "--no-manifest",
                ]
                self.assertEqual(0, generate_walkforward_tearsheet.main())
            finally:
                sys.argv = original_argv

            report = report_path.read_text(encoding="utf-8")
            self.assertIn("## Data Warning", report)
            self.assertIn("NOT VALID FOR PERFORMANCE CONCLUSION", report)

    def test_empty_svg_chart_writes_no_data_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "charts" / "empty.svg"

            write_svg_line_chart(path, [], title="Empty Chart")

            text = path.read_text(encoding="utf-8")
            self.assertGreater(path.stat().st_size, 0)
            self.assertIn("No data available", text)
            self.assertIn("<svg", text)


def synthetic_summary_rows(
    values: list[tuple[str, str, str, str, str, str, str]] | None = None,
) -> list[dict[str, str]]:
    base = {
        "strategy_version": "synthetic",
        "frequency": "monthly",
        "execution_price": "rebalance_close",
        "cost_scenario": "base",
        "capital_jpy": "1000",
        "target_holdings": "3",
        "adv_cap": "0.005",
        "tax_rate": "0.2",
        "cache_fingerprint": "",
        "lifecycle_data_status": "pit_with_delistings",
        "performance_conclusion_allowed": "True",
        "strict_rebalance_price_filter": "False",
        "missing_price_tail_policy": "warn_only",
        "missing_price_tail_max_stale_days": "5",
        "universe_count": "10",
        "selected_count": "3",
        "zero_lot_targets": "0",
        "holdings_count": "3",
        "portfolio_equity_optimistic": "0",
        "portfolio_equity_base": "0",
        "portfolio_equity_pessimistic": "0",
        "after_tax_taxable_equity": "0",
        "after_tax_nisa_like_equity": "0",
        "research_equity": "0",
        "market_benchmark_id": "SYNMKT",
        "cash": "0",
        "cash_pct": "0.05",
        "turnover": "0.2",
        "estimated_cost_base": "1",
        "cumulative_cost_optimistic": "0",
        "cumulative_cost_base": "3",
        "cumulative_cost_pessimistic": "0",
        "cumulative_realized_gain": "0",
        "cumulative_tax": "2",
        "buy_trades": "1",
        "sell_trades": "0",
        "skipped_orders": "0",
    }
    rows = []
    values = values or [
        ("2026-01-31", "1000", "1100", "0.1", "1050", "0.05", "1050"),
        ("2026-02-28", "1100", "990", "-0.1", "1029", "-0.02", "1029"),
        ("2026-03-31", "990", "1188", "0.2", "1080.45", "0.05", "1080.45"),
    ]
    for rebalance_date, pre, after, ret, benchmark, market_ret, market_equity in values:
        row = dict(base)
        row.update(
            {
                "rebalance_date": rebalance_date,
                "portfolio_equity_pre": pre,
                "portfolio_equity_after_cost": after,
                "after_tax_taxable_equity": after,
                "portfolio_return_after_cost": ret,
                "benchmark_equity": benchmark,
                "market_benchmark_equity": market_equity,
                "market_benchmark_return": market_ret,
            }
        )
        rows.append(row)
    return rows


def synthetic_failure_rows() -> list[dict[str, str]]:
    return [
        {
            "date": "2026-02-28",
            "code": "",
            "failure_type": "cash_drag",
            "detail": "synthetic",
            "value": "50",
        }
    ]


if __name__ == "__main__":
    unittest.main()
