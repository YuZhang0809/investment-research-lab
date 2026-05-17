from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_event_drift  # noqa: E402
import generate_strategy_diagnostics_pack  # noqa: E402
from analyze_benchmark_attribution import attribution_rows  # noqa: E402
from audit_data_quality import audit_listings, audit_prices, summarize_issues  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def synthetic_summary_rows() -> list[dict[str, str]]:
    base = {
        "strategy_version": "synthetic",
        "frequency": "monthly",
        "execution_price": "rebalance_close",
        "cost_scenario": "base",
        "capital_jpy": "1000",
        "lifecycle_data_status": "pit_with_delistings",
        "performance_conclusion_allowed": "True",
        "missing_price_tail_policy": "warn_only",
        "missing_price_tail_max_stale_days": "5",
        "cash_pct": "0.05",
        "turnover": "0.20",
        "holdings_count": "2",
    }
    values = [
        ("2026-01-31", "1000", "1000", "0", "1000", "0", "1000"),
        ("2026-02-28", "1000", "1080", "0.08", "1040", "0.04", "1040"),
        ("2026-03-31", "1080", "1209.6", "0.12", "1060", "0.06", "1102.4"),
    ]
    rows = []
    for rebalance_date, pre, after, period_return, filtered, market_return, market in values:
        row = dict(base)
        row.update(
            {
                "rebalance_date": rebalance_date,
                "portfolio_equity_pre": pre,
                "portfolio_equity_after_cost": after,
                "after_tax_taxable_equity": after,
                "portfolio_return_after_cost": period_return,
                "benchmark_equity": filtered,
                "market_benchmark_id": "SYNMKT",
                "market_benchmark_equity": market,
                "market_benchmark_return": market_return,
            }
        )
        rows.append(row)
    return rows


class ResearchEngineLayersTest(unittest.TestCase):
    def test_data_quality_audit_flags_price_contract_issues(self) -> None:
        price_rows = [
            {"date": "2026-01-01", "code": "1001", "unadjusted_close": "100", "adjusted_close": "100", "adjustment_factor": "1"},
            {"date": "2026-01-02", "code": "1001", "unadjusted_close": "100", "adjusted_close": "", "adjustment_factor": ""},
            {"date": "2026-01-20", "code": "1001", "unadjusted_close": "100", "adjusted_close": "200", "adjustment_factor": "0.5"},
            {"date": "2026-01-21", "code": "1001", "unadjusted_close": "100", "adjusted_close": "200", "adjustment_factor": "0.5"},
            {"date": "2026-01-22", "code": "1001", "unadjusted_close": "100", "adjusted_close": "200", "adjustment_factor": "0.5"},
        ]
        listing_rows = [{"code": "1001", "delisted_date": "2026-01-10"}]

        issues = [
            *audit_prices(price_rows, jump_threshold=0.5, max_calendar_gap_days=10, stale_repeat_count=3),
            *audit_listings(listing_rows, price_rows),
        ]
        issue_types = {row["issue_type"] for row in issues}
        summary = summarize_issues(issues)

        self.assertIn("missing_adjusted_price", issue_types)
        self.assertIn("missing_adjusted_price_and_adjustment_factor", issue_types)
        self.assertIn("price_calendar_gap", issue_types)
        self.assertIn("stale_adjusted_price_run", issue_types)
        self.assertIn("price_after_delisting", issue_types)
        self.assertIn({"issue_type": "price_after_delisting", "severity": "error", "count": 1}, summary)

    def test_benchmark_attribution_handles_builtin_and_custom_benchmarks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            custom = temp / "custom_benchmark.csv"
            write_csv(
                custom,
                [
                    {"date": "2026-01-31", "close": "100"},
                    {"date": "2026-02-28", "close": "104"},
                    {"date": "2026-03-31", "close": "110.24"},
                ],
                ["date", "close"],
            )

            rows = attribution_rows(synthetic_summary_rows(), [f"custom_size={custom}"], min_periods=2)

            labels = {row["benchmark_label"]: row for row in rows}
            self.assertIn("filtered_universe_benchmark", labels)
            self.assertIn("SYNMKT", labels)
            self.assertIn("custom_size", labels)
            self.assertGreater(float(labels["SYNMKT"]["beta"] or 0), 0)
            self.assertGreater(float(labels["custom_size"]["periods"]), 0)

    def test_strategy_diagnostics_pack_consumes_generic_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.csv"
            failures = temp / "failures.csv"
            data_quality = temp / "data_quality_summary.csv"
            benchmark = temp / "benchmark_attribution.csv"
            candidates = temp / "scores.csv"
            report = temp / "strategy_diagnostics.md"
            write_csv(summary, synthetic_summary_rows(), list(synthetic_summary_rows()[0]))
            write_csv(failures, [{"date": "2026-02-28", "code": "", "failure_type": "cash_drag", "detail": "", "value": "50"}], ["date", "code", "failure_type", "detail", "value"])
            write_csv(data_quality, [{"issue_type": "missing_adjusted_price", "severity": "error", "count": "1"}], ["issue_type", "severity", "count"])
            write_csv(
                benchmark,
                [{"benchmark_label": "SYNMKT", "benchmark_type": "market", "beta": "2", "alpha": "0", "tracking_error": "0.1", "information_ratio": "1.5"}],
                ["benchmark_label", "benchmark_type", "beta", "alpha", "tracking_error", "information_ratio"],
            )
            write_csv(candidates, [{"rank": "1", "code": "1001", "name": "Synthetic 1001", "filter_status": "pass"}], ["rank", "code", "name", "filter_status"])

            original_argv = sys.argv[:]
            try:
                sys.argv = [
                    "generate_strategy_diagnostics_pack.py",
                    "--summary",
                    str(summary),
                    "--failures",
                    str(failures),
                    "--data-quality-summary",
                    str(data_quality),
                    "--benchmark-attribution",
                    str(benchmark),
                    "--candidates",
                    str(candidates),
                    "--out",
                    str(report),
                    "--no-manifest",
                ]
                self.assertEqual(0, generate_strategy_diagnostics_pack.main())
            finally:
                sys.argv = original_argv

            text = report.read_text(encoding="utf-8")
            self.assertIn("# Strategy Diagnostics Pack", text)
            self.assertIn("## Data Quality", text)
            self.assertIn("## Benchmark Attribution", text)
            self.assertIn("## Candidate Review", text)

    def test_event_drift_adds_tradable_timestamp_and_overlap_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            events = temp / "events.csv"
            prices = temp / "prices.csv"
            out_dir = temp / "out"
            write_csv(
                events,
                [
                    {
                        "event_id": "evt1",
                        "announcement_datetime": "2026-01-01 16:00",
                        "code": "1001",
                        "company_name": "Synthetic 1001",
                        "document_type": "revision",
                        "event_label": "upward_revision",
                        "title": "Synthetic event",
                        "url_or_doc_id": "doc1",
                        "parsed_flag": "true",
                        "parse_confidence": "1",
                        "notes": "",
                    },
                    {
                        "event_id": "evt2",
                        "announcement_datetime": "2026-01-02 10:00",
                        "code": "1001",
                        "company_name": "Synthetic 1001",
                        "document_type": "buyback",
                        "event_label": "buyback",
                        "title": "Synthetic event 2",
                        "url_or_doc_id": "doc2",
                        "parsed_flag": "true",
                        "parse_confidence": "1",
                        "notes": "",
                    },
                ],
                ["event_id", "announcement_datetime", "code", "company_name", "document_type", "event_label", "title", "url_or_doc_id", "parsed_flag", "parse_confidence", "notes"],
            )
            write_csv(
                prices,
                [
                    {"date": "2026-01-01", "code": "1001", "adjusted_close": "100", "unadjusted_close": "100"},
                    {"date": "2026-01-02", "code": "1001", "adjusted_close": "110", "unadjusted_close": "110"},
                    {"date": "2026-01-03", "code": "1001", "adjusted_close": "121", "unadjusted_close": "121"},
                ],
                ["date", "code", "adjusted_close", "unadjusted_close"],
            )

            original_argv = sys.argv[:]
            try:
                sys.argv = [
                    "analyze_event_drift.py",
                    "--events",
                    str(events),
                    "--prices",
                    str(prices),
                    "--window",
                    "1",
                    "--out-dir",
                    str(out_dir),
                    "--no-manifest",
                ]
                self.assertEqual(0, analyze_event_drift.main())
            finally:
                sys.argv = original_argv

            rows = read_csv(out_dir / "tdnet_event_drift_202601_202601.csv")
            self.assertEqual("2026-01-02", rows[0]["entry_date"])
            self.assertEqual("2026-01-02 09:00:00", rows[0]["tradable_timestamp"])
            self.assertEqual("1", rows[0]["event_overlap_count"])
            self.assertEqual("1", rows[0]["duplicate_event_count"])
            self.assertEqual("0.1", rows[0]["next_1d_return"])


if __name__ == "__main__":
    unittest.main()
