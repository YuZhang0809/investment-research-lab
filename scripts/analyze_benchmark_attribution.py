from __future__ import annotations

import argparse
import math
from datetime import date
from pathlib import Path
from typing import Any

from performance_analytics import (
    infer_initial_capital,
    period_returns,
    periods_per_year,
    relative_metrics,
)
from research_common import append_manifest, parse_date, parse_float, read_csv, write_csv


ATTRIBUTION_FIELDS = [
    "benchmark_label",
    "benchmark_type",
    "periods",
    "portfolio_total_return",
    "benchmark_total_return",
    "active_total_return",
    "beta",
    "alpha",
    "tracking_error",
    "information_ratio",
    "correlation",
    "up_capture",
    "down_capture",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze portfolio returns against one or more benchmark return series.")
    parser.add_argument("--summary", required=True, type=Path, help="Walk-forward summary CSV.")
    parser.add_argument("--benchmark", action="append", default=[], help="Custom benchmark in label=path CSV format.")
    parser.add_argument("--min-periods", type=int, default=2)
    parser.add_argument("--out", type=Path, default=Path("reports/benchmark/benchmark_attribution.csv"))
    parser.add_argument("--report", type=Path, default=Path("reports/benchmark/benchmark_attribution.md"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value * 100:.2f}%"


def number(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.4f}"


def cumulative_return(returns: list[tuple[date, float]]) -> float | None:
    if not returns:
        return None
    value = 1.0
    for _row_date, period_return in returns:
        value *= 1.0 + period_return
    return value - 1.0


def paired_return_series(
    portfolio_returns: list[tuple[date, float]],
    benchmark_returns: list[tuple[date, float]],
) -> list[tuple[date, float, float]]:
    benchmark_by_date = {row_date: value for row_date, value in benchmark_returns}
    return [
        (row_date, portfolio_return, benchmark_by_date[row_date])
        for row_date, portfolio_return in portfolio_returns
        if row_date in benchmark_by_date
    ]


def active_total_return(portfolio_total: float | None, benchmark_total: float | None) -> float | None:
    if portfolio_total is None or benchmark_total is None or benchmark_total <= -1:
        return None
    return (1 + portfolio_total) / (1 + benchmark_total) - 1.0


def summary_portfolio_returns(summary_rows: list[dict[str, str]]) -> list[tuple[date, float]]:
    initial_capital = infer_initial_capital(summary_rows)
    return period_returns(
        summary_rows,
        equity_column="portfolio_equity_after_cost",
        initial_capital=initial_capital,
        return_column="portfolio_return_after_cost",
    )


def built_in_benchmarks(summary_rows: list[dict[str, str]]) -> list[tuple[str, str, list[tuple[date, float]]]]:
    initial_capital = infer_initial_capital(summary_rows)
    values: list[tuple[str, str, list[tuple[date, float]]]] = []
    filtered = period_returns(summary_rows, equity_column="benchmark_equity", initial_capital=initial_capital)
    if filtered:
        values.append(("filtered_universe_benchmark", "filtered_universe", filtered))
    market = period_returns(
        summary_rows,
        equity_column="market_benchmark_equity",
        initial_capital=initial_capital,
        return_column="market_benchmark_return",
    )
    if market:
        label = next((row.get("market_benchmark_id", "") for row in reversed(summary_rows) if row.get("market_benchmark_id")), "")
        values.append((label or "market_benchmark", "market", market))
    return values


def parse_benchmark_arg(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError("--benchmark must use label=path format.")
    label, path_text = value.split("=", 1)
    label = label.strip()
    if not label:
        raise ValueError("--benchmark label must be non-empty.")
    return label, Path(path_text)


def benchmark_returns_from_file(path: Path) -> list[tuple[date, float]]:
    rows = read_csv(path)
    if not rows:
        raise ValueError(f"Benchmark file is empty: {path}")
    columns = set(rows[0])
    if "date" not in columns:
        raise ValueError(f"Benchmark file is missing required column: date ({path})")
    return_columns = [column for column in ("return", "benchmark_return") if column in columns]
    value_columns = [column for column in ("close", "equity", "value") if column in columns]
    if return_columns and value_columns:
        raise ValueError(f"Benchmark file must contain either a return column or a value column, not both: {path}")
    if return_columns:
        column = return_columns[0]
        values: list[tuple[date, float]] = []
        for row in rows:
            row_date = parse_date(row.get("date"), field_name="benchmark.date")
            period_return = parse_float(row.get(column))
            if row_date is not None and period_return is not None:
                values.append((row_date, period_return))
        return values
    if value_columns:
        column = value_columns[0]
        values_by_date: list[tuple[date, float]] = []
        for row in rows:
            row_date = parse_date(row.get("date"), field_name="benchmark.date")
            value = parse_float(row.get(column))
            if row_date is not None and value is not None and value > 0:
                values_by_date.append((row_date, value))
        values_by_date.sort(key=lambda item: item[0])
        return [
            (row_date, value / previous_value - 1.0)
            for (previous_date, previous_value), (row_date, value) in zip(values_by_date, values_by_date[1:])
            if row_date > previous_date and previous_value > 0
        ]
    raise ValueError(f"Benchmark file must contain return, benchmark_return, close, equity, or value: {path}")


def attribution_rows(
    summary_rows: list[dict[str, str]],
    custom_benchmarks: list[str],
    *,
    min_periods: int,
) -> list[dict[str, Any]]:
    if not summary_rows:
        raise ValueError("summary is empty.")
    frequency = summary_rows[-1].get("frequency") or summary_rows[0].get("frequency") or "monthly"
    annualization = periods_per_year(frequency)
    portfolio = summary_portfolio_returns(summary_rows)
    benchmarks = built_in_benchmarks(summary_rows)
    for item in custom_benchmarks:
        label, path = parse_benchmark_arg(item)
        benchmarks.append((label, "custom", benchmark_returns_from_file(path)))
    rows: list[dict[str, Any]] = []
    for label, benchmark_type, benchmark in benchmarks:
        paired = paired_return_series(portfolio, benchmark)
        paired_portfolio = [(row_date, portfolio_return) for row_date, portfolio_return, _benchmark_return in paired]
        paired_benchmark = [(row_date, benchmark_return) for row_date, _portfolio_return, benchmark_return in paired]
        metrics = relative_metrics(portfolio, benchmark, annualization, min_periods=min_periods)
        portfolio_total = cumulative_return(paired_portfolio)
        benchmark_total = cumulative_return(paired_benchmark)
        rows.append(
            {
                "benchmark_label": label,
                "benchmark_type": benchmark_type,
                "periods": len(paired),
                "portfolio_total_return": portfolio_total,
                "benchmark_total_return": benchmark_total,
                "active_total_return": active_total_return(portfolio_total, benchmark_total),
                **metrics,
            }
        )
    return rows


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Benchmark Attribution",
        "",
        "| benchmark | type | periods | portfolio | benchmark | active | beta | alpha | TE | IR | corr |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if rows:
        for row in rows:
            lines.append(
                f"| {row['benchmark_label']} | {row['benchmark_type']} | {row['periods']} | "
                f"{pct(row.get('portfolio_total_return'))} | {pct(row.get('benchmark_total_return'))} | "
                f"{pct(row.get('active_total_return'))} | {number(row.get('beta'))} | "
                f"{pct(row.get('alpha'))} | {pct(row.get('tracking_error'))} | "
                f"{number(row.get('information_ratio'))} | {number(row.get('correlation'))} |"
            )
    else:
        lines.append("| none |  | 0 |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "Metrics are computed at the summary sampling frequency. Use comparable frequencies when comparing strategies.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    if args.min_periods < 2:
        raise ValueError("--min-periods must be at least 2.")
    rows = attribution_rows(read_csv(args.summary), args.benchmark, min_periods=args.min_periods)
    write_csv(args.out, [{key: fmt(value) for key, value in row.items()} for row in rows], ATTRIBUTION_FIELDS)
    write_report(args.report, rows)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_benchmark_attribution",
            file_path=args.out,
            vendor="local",
            schema_version="benchmark_attribution_v0_1",
            date_range="",
            notes=f"{len(rows)} benchmark rows; report={args.report.as_posix()}",
        )
    print(f"Wrote {len(rows)} benchmark attribution rows to {args.out}")
    print(f"Wrote benchmark attribution report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
