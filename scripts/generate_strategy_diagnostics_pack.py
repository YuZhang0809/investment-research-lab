from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from performance_analytics import METRIC_FIELDS, metric_rows, summarize_walkforward
from research_common import append_manifest, parse_float, read_csv, write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a generic public-safe strategy diagnostics pack.")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--failures", type=Path)
    parser.add_argument("--trades", type=Path)
    parser.add_argument("--candidates", type=Path, help="Optional ranked candidate/scores CSV.")
    parser.add_argument("--contributions", type=Path, help="Optional CSV with date, code, contribution.")
    parser.add_argument("--exposures", type=Path, help="Optional CSV with date, group or sector, weight.")
    parser.add_argument("--data-quality-summary", type=Path)
    parser.add_argument("--benchmark-attribution", type=Path)
    parser.add_argument("--out", type=Path, default=Path("reports/strategy/strategy_diagnostics.md"))
    parser.add_argument("--metrics-out", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def require_columns(rows: list[dict[str, str]], required: set[str], *, table_name: str) -> None:
    if not rows:
        raise ValueError(f"{table_name} is empty.")
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"{table_name} is missing required column(s): {', '.join(missing)}")


def pct(value: Any) -> str:
    number = parse_float(value)
    if number is None:
        return ""
    return f"{number * 100:.2f}%"


def number(value: Any) -> str:
    parsed = parse_float(value)
    if parsed is None:
        return str(value or "")
    return f"{parsed:.4f}"


def metric_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["metric"]: row for row in rows}


def metric_table(metrics: dict[str, dict[str, str]], names: list[str]) -> list[str]:
    lines = ["| metric | value |", "|---|---:|"]
    for name in names:
        row = metrics.get(name, {})
        lines.append(f"| {name} | {row.get('formatted_value', '')} |")
    return lines


def failure_counts(rows: list[dict[str, str]]) -> Counter[str]:
    return Counter(row.get("failure_type", "") for row in rows if row.get("failure_type"))


def failure_table(rows: list[dict[str, str]]) -> list[str]:
    counts = failure_counts(rows)
    lines = ["| failure type | count |", "|---|---:|"]
    if not counts:
        lines.append("| none | 0 |")
        return lines
    for name, count in counts.most_common():
        lines.append(f"| {name} | {count} |")
    return lines


def trade_summary(rows: list[dict[str, str]]) -> list[str]:
    require_columns(rows, {"side", "value"}, table_name="trades")
    counts = Counter(row.get("side", "") for row in rows if row.get("side"))
    value_by_side: Counter[str] = Counter()
    constraints = Counter(row.get("constraint_reason", "") for row in rows if row.get("constraint_reason"))
    for row in rows:
        value = parse_float(row.get("value"))
        if value is not None:
            value_by_side[row.get("side", "")] += value
    lines = ["| metric | value |", "|---|---:|"]
    for side, count in counts.most_common():
        lines.append(f"| {side} trades | {count} |")
        lines.append(f"| {side} traded value | {value_by_side[side]:.10g} |")
    if constraints:
        for reason, count in constraints.most_common(5):
            lines.append(f"| constraint: {reason} | {count} |")
    return lines


def latest_rows(rows: list[dict[str, str]], date_column: str = "date") -> list[dict[str, str]]:
    if not rows or date_column not in rows[0]:
        return rows
    latest = max(row.get(date_column, "") for row in rows)
    return [row for row in rows if row.get(date_column, "") == latest]


def contribution_table(rows: list[dict[str, str]], *, largest: bool) -> list[str]:
    require_columns(rows, {"code", "contribution"}, table_name="contributions")
    selected = sorted(
        latest_rows(rows),
        key=lambda row: parse_float(row.get("contribution")) or 0.0,
        reverse=largest,
    )[:10]
    lines = ["| code | contribution |", "|---|---:|"]
    for row in selected:
        lines.append(f"| {row.get('code', '')} | {pct(row.get('contribution'))} |")
    return lines


def exposure_table(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        raise ValueError("exposures is empty.")
    label = "group" if "group" in rows[0] else "sector" if "sector" in rows[0] else ""
    if not label:
        raise ValueError("exposures is missing required column: group or sector")
    require_columns(rows, {label, "weight"}, table_name="exposures")
    selected = sorted(latest_rows(rows), key=lambda row: abs(parse_float(row.get("weight")) or 0.0), reverse=True)[:10]
    lines = [f"| {label} | weight |", "|---|---:|"]
    for row in selected:
        lines.append(f"| {row.get(label, '')} | {pct(row.get('weight'))} |")
    return lines


def candidate_table(rows: list[dict[str, str]]) -> list[str]:
    require_columns(rows, {"code", "rank"}, table_name="candidates")
    ranked = [row for row in rows if row.get("rank")]
    selected = sorted(ranked, key=lambda row: int(parse_float(row.get("rank")) or 0))[:20]
    lines = ["| rank | code | name | status |", "|---:|---|---|---|"]
    for row in selected:
        lines.append(f"| {row.get('rank', '')} | {row.get('code', '')} | {row.get('name', '')} | {row.get('filter_status', '')} |")
    return lines


def summary_count_table(rows: list[dict[str, str]], label: str) -> list[str]:
    require_columns(rows, {"issue_type", "severity", "count"}, table_name=label)
    lines = ["| issue type | severity | count |", "|---|---|---:|"]
    for row in rows:
        lines.append(f"| {row.get('issue_type', '')} | {row.get('severity', '')} | {row.get('count', '')} |")
    return lines


def benchmark_table(rows: list[dict[str, str]]) -> list[str]:
    require_columns(rows, {"benchmark_label", "benchmark_type", "beta", "alpha", "tracking_error", "information_ratio"}, table_name="benchmark_attribution")
    lines = ["| benchmark | type | beta | alpha | TE | IR |", "|---|---|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(
            f"| {row.get('benchmark_label', '')} | {row.get('benchmark_type', '')} | "
            f"{number(row.get('beta'))} | {pct(row.get('alpha'))} | {pct(row.get('tracking_error'))} | "
            f"{number(row.get('information_ratio'))} |"
        )
    return lines


def add_optional_section(lines: list[str], title: str, path: Path | None, renderer) -> None:
    if path is None:
        return
    rows = read_csv(path)
    lines.extend(["", f"## {title}", "", *renderer(rows)])


def write_report(
    path: Path,
    summary: dict[str, Any],
    metrics_rows: list[dict[str, str]],
    *,
    failures_path: Path | None,
    trades_path: Path | None,
    candidates_path: Path | None,
    contributions_path: Path | None,
    exposures_path: Path | None,
    data_quality_summary_path: Path | None,
    benchmark_attribution_path: Path | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = metric_lookup(metrics_rows)
    lines = [
        f"# Strategy Diagnostics Pack {summary['period_start']}..{summary['period_end']}",
        "",
        "## Performance",
        "",
        *metric_table(metrics, ["total_return", "max_drawdown", "win_rate", "best_period_return", "worst_period_return"]),
        "",
        "## Implementation",
        "",
        *metric_table(metrics, ["avg_cash_pct", "avg_turnover", "avg_holdings", "avg_zero_lot_targets", "avg_skipped_orders", "cost_drag", "tax_drag"]),
        "",
        "## Built-In Benchmark",
        "",
        *metric_table(metrics, ["benchmark_label", "benchmark_total_return", "active_total_return", "beta", "alpha", "tracking_error", "information_ratio"]),
    ]
    add_optional_section(lines, "Benchmark Attribution", benchmark_attribution_path, benchmark_table)
    add_optional_section(lines, "Data Quality", data_quality_summary_path, lambda rows: summary_count_table(rows, "data_quality_summary"))
    add_optional_section(lines, "Failure Cases", failures_path, failure_table)
    add_optional_section(lines, "Trades", trades_path, trade_summary)
    add_optional_section(lines, "Top Contributors", contributions_path, lambda rows: contribution_table(rows, largest=True))
    add_optional_section(lines, "Worst Contributors", contributions_path, lambda rows: contribution_table(rows, largest=False))
    add_optional_section(lines, "Exposures", exposures_path, exposure_table)
    add_optional_section(lines, "Candidate Review", candidates_path, candidate_table)
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "This pack only summarizes supplied public-engine artifacts. It does not infer missing diagnostics from unrelated files.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    summary_rows = read_csv(args.summary)
    failures = read_csv(args.failures) if args.failures else []
    summary = summarize_walkforward(summary_rows, failures)
    rows = metric_rows(summary)
    metrics_out = args.metrics_out or args.out.with_name(f"{args.out.stem}_metrics.csv")
    write_csv(metrics_out, rows, METRIC_FIELDS)
    write_report(
        args.out,
        summary,
        rows,
        failures_path=args.failures,
        trades_path=args.trades,
        candidates_path=args.candidates,
        contributions_path=args.contributions,
        exposures_path=args.exposures,
        data_quality_summary_path=args.data_quality_summary,
        benchmark_attribution_path=args.benchmark_attribution,
    )
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_strategy_diagnostics_pack",
            file_path=args.out,
            vendor="local",
            schema_version="strategy_diagnostics_pack_v0_1",
            date_range=f"{summary['period_start']}..{summary['period_end']}",
            notes=f"metrics={metrics_out.as_posix()}",
        )
    print(f"Wrote strategy diagnostics pack to {args.out}")
    print(f"Wrote strategy diagnostics metrics to {metrics_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
