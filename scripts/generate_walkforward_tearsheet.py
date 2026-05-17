from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from performance_analytics import (
    METRIC_FIELDS,
    metric_rows,
    summarize_walkforward,
    write_svg_line_chart,
)
from research_common import append_manifest, read_csv, write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a public-safe walk-forward performance tear sheet.")
    parser.add_argument("--summary", required=True, type=Path, help="Walk-forward summary CSV.")
    parser.add_argument("--failures", type=Path, help="Optional walk-forward failure-case CSV.")
    parser.add_argument("--out", type=Path, default=Path("reports/walkforward/walkforward_tearsheet.md"))
    parser.add_argument("--metrics-out", type=Path, help="Optional metrics CSV path.")
    parser.add_argument("--chart-dir", type=Path, help="Optional directory for SVG charts.")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def metric_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["metric"]: row for row in rows}


def table_rows(metrics: dict[str, dict[str, str]], names: list[str]) -> list[str]:
    lines = ["| metric | value |", "|---|---:|"]
    for name in names:
        row = metrics.get(name, {})
        lines.append(f"| {name} | {row.get('formatted_value', '')} |")
    return lines


def failure_table(summary: dict[str, Any]) -> list[str]:
    counts = summary["failure_counts"]
    lines = ["| failure type | count |", "|---|---:|"]
    if not counts:
        lines.append("| none | 0 |")
        return lines
    for name, count in counts.most_common():
        lines.append(f"| {name} | {count} |")
    return lines


def write_charts(summary: dict[str, Any], chart_dir: Path) -> dict[str, Path]:
    chart_dir.mkdir(parents=True, exist_ok=True)
    equity_path = chart_dir / "equity_curve.svg"
    drawdown_path = chart_dir / "drawdown.svg"
    implementation_path = chart_dir / "implementation.svg"
    equity_series = [
        ("portfolio", summary["portfolio_equity"], "#2563eb"),
    ]
    if summary.get("benchmark_equity"):
        equity_series.append(("benchmark", summary["benchmark_equity"], "#64748b"))
    write_svg_line_chart(equity_path, equity_series, title="Equity Curve")
    write_svg_line_chart(
        drawdown_path,
        [("drawdown", summary["drawdowns"], "#dc2626")],
        title="Portfolio Drawdown",
        value_format="pct",
    )
    implementation_rows = [
        (row_date, value)
        for row_date, value in summary["portfolio_returns"]
    ]
    benchmark_rows = [
        (row_date, value)
        for row_date, value in summary["benchmark_returns"]
    ]
    series = [("portfolio return", implementation_rows, "#16a34a")]
    if benchmark_rows:
        series.append(("benchmark return", benchmark_rows, "#64748b"))
    write_svg_line_chart(
        implementation_path,
        series,
        title="Period Returns",
        value_format="pct",
    )
    return {
        "equity": equity_path,
        "drawdown": drawdown_path,
        "implementation": implementation_path,
    }


def relative_chart_path(report_path: Path, chart_path: Path) -> str:
    try:
        return chart_path.relative_to(report_path.parent).as_posix()
    except ValueError:
        return chart_path.as_posix()


def write_report(path: Path, summary: dict[str, Any], metric_rows_list: list[dict[str, str]], chart_paths: dict[str, Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = metric_lookup(metric_rows_list)
    lines = [
        f"# Walk-Forward Performance Tear Sheet {summary['period_start']}..{summary['period_end']}",
        "",
        "## Scope",
        "",
        "| field | value |",
        "|---|---:|",
        f"| frequency | {summary['frequency']} |",
        f"| sampled periods | {summary['period_count']} |",
        f"| annualization | {summary['annualization']:.0f} |",
        f"| lifecycle data status | {summary['lifecycle_data_status']} |",
        f"| performance conclusion allowed | {summary['performance_conclusion_allowed']} |",
        "",
        "## Performance",
        "",
        *table_rows(
            metrics,
            [
                "initial_capital",
                "final_equity",
                "total_return",
                "annualized_return",
                "annualized_volatility",
                "sharpe_ratio",
                "sortino_ratio",
                "calmar_ratio",
                "win_rate",
                "best_period_return",
                "worst_period_return",
            ],
        ),
        "",
        f"![Equity curve]({relative_chart_path(path, chart_paths['equity'])})",
        "",
        "## Drawdown",
        "",
        *table_rows(metrics, ["max_drawdown", "longest_drawdown_periods"]),
        "",
        f"![Drawdown]({relative_chart_path(path, chart_paths['drawdown'])})",
        "",
        "## Benchmark",
        "",
        *table_rows(
            metrics,
            [
                "benchmark_label",
                "benchmark_total_return",
                "active_total_return",
                "beta",
                "alpha",
                "tracking_error",
                "information_ratio",
                "correlation",
                "up_capture",
                "down_capture",
            ],
        ),
        "",
        f"![Period returns]({relative_chart_path(path, chart_paths['implementation'])})",
        "",
        "## Implementation",
        "",
        *table_rows(
            metrics,
            [
                "avg_cash_pct",
                "avg_turnover",
                "avg_holdings",
                "avg_zero_lot_targets",
                "avg_skipped_orders",
                "cost_drag",
                "tax_drag",
            ],
        ),
        "",
        "## Failure Cases",
        "",
        *failure_table(summary),
        "",
        "## Caveats",
        "",
        "- Metrics are sampled at the walk-forward rebalance frequency, not necessarily daily.",
        "- Sharpe, Sortino, volatility, beta, and drawdown are only comparable across runs with the same sampling frequency.",
        "- This report summarizes public-engine outputs only. Real decisions and private run ledgers belong in a private workspace.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    summary_rows = read_csv(args.summary)
    failure_rows = read_csv(args.failures) if args.failures and args.failures.exists() else []
    summary = summarize_walkforward(summary_rows, failure_rows)
    rows = metric_rows(summary)
    metrics_out = args.metrics_out or args.out.with_name(f"{args.out.stem}_metrics.csv")
    chart_dir = args.chart_dir or args.out.with_name(f"{args.out.stem}_charts")
    chart_paths = write_charts(summary, chart_dir)
    write_csv(metrics_out, rows, METRIC_FIELDS)
    write_report(args.out, summary, rows, chart_paths)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_walkforward_tearsheet",
            file_path=args.out,
            vendor="local",
            schema_version="walkforward_tearsheet_v0_1",
            date_range=f"{summary['period_start']}..{summary['period_end']}",
            notes=f"metrics={metrics_out.as_posix()}; charts={chart_dir.as_posix()}",
        )
        append_manifest(
            args.manifest,
            source="derived_walkforward_metrics",
            file_path=metrics_out,
            vendor="local",
            schema_version="walkforward_metrics_v0_1",
            date_range=f"{summary['period_start']}..{summary['period_end']}",
            notes=f"{len(rows)} rows",
        )
    print(f"Wrote walk-forward tear sheet to {args.out}")
    print(f"Wrote walk-forward metrics to {metrics_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
