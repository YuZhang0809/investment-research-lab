from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from performance_analytics import METRIC_FIELDS, metric_row, metric_rows, summarize_walkforward
from research_common import append_manifest, parse_float, read_csv, write_csv


BLOCKING_ERROR = "blocking_error"
EXECUTION_CONSTRAINT = "execution_constraint"
REVIEW_REQUIRED = "review_required"
INFO = "info"
DATA_QUALITY_SEVERITIES = {BLOCKING_ERROR, EXECUTION_CONSTRAINT, REVIEW_REQUIRED, INFO}


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


def bool_text(value: bool) -> str:
    return "True" if value else "False"


def count_value(row: dict[str, str]) -> int:
    parsed = parse_float(row.get("count"))
    return int(parsed) if parsed is not None else 0


def validate_data_quality_summary(rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    require_columns(rows, {"issue_type", "severity", "count"}, table_name="data_quality_summary")
    unknown = sorted({row.get("severity", "") for row in rows} - DATA_QUALITY_SEVERITIES)
    if unknown:
        raise ValueError(f"data_quality_summary has unknown severity value(s): {', '.join(unknown)}")


def issue_count_list(rows: list[dict[str, str]], severity: str) -> str:
    values = [
        f"{row.get('issue_type', '')}({count_value(row)})"
        for row in rows
        if row.get("severity") == severity and count_value(row) > 0
    ]
    return ", ".join(values) if values else "none"


def data_quality_status(rows: list[dict[str, str]] | None) -> str:
    if rows is None:
        return "not_supplied"
    validate_data_quality_summary(rows)
    counts: Counter[str] = Counter()
    for row in rows:
        counts[row.get("severity", "")] += count_value(row)
    if counts[BLOCKING_ERROR]:
        return "blocked"
    if counts[REVIEW_REQUIRED]:
        return "review_required"
    if counts[EXECUTION_CONSTRAINT]:
        return "ok_with_execution_constraints"
    return "ok"


def research_gate(summary: dict[str, Any], data_quality_rows: list[dict[str, str]] | None) -> dict[str, str]:
    validate_data_quality_summary(data_quality_rows or [])
    lifecycle_status = str(summary.get("lifecycle_data_status", "") or "")
    summary_allowed = str(summary.get("performance_conclusion_allowed", "")).strip().lower() == "true"
    supplied = data_quality_rows is not None
    blocking = bool(data_quality_rows) and any(row.get("severity") == BLOCKING_ERROR and count_value(row) > 0 for row in data_quality_rows)
    review = bool(data_quality_rows) and any(row.get("severity") == REVIEW_REQUIRED and count_value(row) > 0 for row in data_quality_rows)
    exploration: str
    validation: str
    if not supplied:
        exploration = "unknown"
        validation = "unknown"
    else:
        exploration = bool_text(not blocking)
        validation = bool_text((not blocking) and (not review) and lifecycle_status == "pit_with_delistings")
    performance_allowed = summary_allowed and validation == "True"
    reasons: list[str] = []
    if not supplied:
        reasons.append("data_quality_summary_not_supplied")
    if data_quality_rows:
        blocking_issues = issue_count_list(data_quality_rows, BLOCKING_ERROR)
        review_issues = issue_count_list(data_quality_rows, REVIEW_REQUIRED)
        if blocking_issues != "none":
            reasons.append(f"data_quality_blocking_error={blocking_issues}")
        if review_issues != "none":
            reasons.append(f"data_quality_review_required={review_issues}")
    if lifecycle_status != "pit_with_delistings":
        reasons.append(f"lifecycle_data_status={lifecycle_status or 'missing'}")
    elif not summary_allowed:
        reasons.append("summary_performance_conclusion_allowed=False")
    return {
        "data_quality_status": data_quality_status(data_quality_rows),
        "research_safe_for_exploration": exploration,
        "research_safe_for_validation": validation,
        "performance_conclusion_allowed": bool_text(performance_allowed),
        "performance_blocked_reason": "; ".join(reasons) if reasons else "none",
        "lifecycle_data_status": lifecycle_status,
        "risk_metric_status": str(summary.get("risk_metric_status", "") or ""),
    }


def apply_gate_metrics(rows: list[dict[str, str]], gate: dict[str, str]) -> list[dict[str, str]]:
    updated: list[dict[str, str]] = []
    replaced_conclusion = False
    for row in rows:
        next_row = dict(row)
        if next_row.get("metric") == "performance_conclusion_allowed":
            next_row.update(
                {
                    "category": "data_gate",
                    "value": gate["performance_conclusion_allowed"],
                    "formatted_value": gate["performance_conclusion_allowed"],
                }
            )
            replaced_conclusion = True
        updated.append(next_row)
    if not replaced_conclusion:
        updated.append(metric_row("data_gate", "performance_conclusion_allowed", gate["performance_conclusion_allowed"]))
    for name in ["data_quality_status", "research_safe_for_exploration", "research_safe_for_validation", "performance_blocked_reason"]:
        updated.append(metric_row("data_gate", name, gate[name]))
    return updated


def data_gate_table(gate: dict[str, str]) -> list[str]:
    lines = ["| metric | value |", "|---|---|"]
    for name in [
        "data_quality_status",
        "research_safe_for_exploration",
        "research_safe_for_validation",
        "performance_conclusion_allowed",
        "performance_blocked_reason",
        "lifecycle_data_status",
        "risk_metric_status",
    ]:
        lines.append(f"| {name} | {gate.get(name, '')} |")
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
    lines = ["| metric | value |", "|---|---:|"]
    if not rows:
        lines.append("| none | 0 |")
        return lines
    require_columns(rows, {"side", "value"}, table_name="trades")
    counts = Counter(row.get("side", "") for row in rows if row.get("side"))
    value_by_side: Counter[str] = Counter()
    constraints = Counter(row.get("constraint_reason", "") for row in rows if row.get("constraint_reason"))
    for row in rows:
        value = parse_float(row.get("value"))
        if value is not None:
            value_by_side[row.get("side", "")] += value
    for side, count in counts.most_common():
        lines.append(f"| {side} trades | {count} |")
        lines.append(f"| {side} traded value | {value_by_side[side]:.10g} |")
    if constraints:
        for reason, count in constraints.most_common(5):
            lines.append(f"| constraint: {reason} | {count} |")
    if len(lines) == 2:
        lines.append("| none | 0 |")
    return lines


def latest_rows(rows: list[dict[str, str]], date_column: str = "date") -> list[dict[str, str]]:
    if not rows or date_column not in rows[0]:
        return rows
    latest = max(row.get(date_column, "") for row in rows)
    return [row for row in rows if row.get(date_column, "") == latest]


def contribution_table(rows: list[dict[str, str]], *, largest: bool) -> list[str]:
    lines = ["| code | contribution |", "|---|---:|"]
    if not rows:
        lines.append("| none |  |")
        return lines
    require_columns(rows, {"code", "contribution"}, table_name="contributions")
    selected = sorted(
        latest_rows(rows),
        key=lambda row: parse_float(row.get("contribution")) or 0.0,
        reverse=largest,
    )[:10]
    for row in selected:
        lines.append(f"| {row.get('code', '')} | {pct(row.get('contribution'))} |")
    if len(lines) == 2:
        lines.append("| none |  |")
    return lines


def exposure_table(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["| exposure | weight |", "|---|---:|", "| none |  |"]
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
    lines = ["| rank | code | name | status |", "|---:|---|---|---|"]
    if not rows:
        lines.append("|  | none |  |  |")
        return lines
    require_columns(rows, {"code", "rank"}, table_name="candidates")
    ranked = [row for row in rows if row.get("rank")]
    selected = sorted(ranked, key=lambda row: int(parse_float(row.get("rank")) or 0))[:20]
    for row in selected:
        lines.append(f"| {row.get('rank', '')} | {row.get('code', '')} | {row.get('name', '')} | {row.get('filter_status', '')} |")
    if len(lines) == 2:
        lines.append("|  | none |  |  |")
    return lines


def summary_count_table(rows: list[dict[str, str]], label: str) -> list[str]:
    lines = ["| issue type | severity | count |", "|---|---|---:|"]
    if not rows:
        lines.append("| none |  | 0 |")
        return lines
    require_columns(rows, {"issue_type", "severity", "count"}, table_name=label)
    for row in rows:
        lines.append(f"| {row.get('issue_type', '')} | {row.get('severity', '')} | {row.get('count', '')} |")
    return lines


def benchmark_table(rows: list[dict[str, str]]) -> list[str]:
    lines = ["| benchmark | type | beta | alpha | TE | IR |", "|---|---|---:|---:|---:|---:|"]
    if not rows:
        lines.append("| none |  |  |  |  |  |")
        return lines
    require_columns(rows, {"benchmark_label", "benchmark_type", "beta", "alpha", "tracking_error", "information_ratio"}, table_name="benchmark_attribution")
    for row in rows:
        lines.append(
            f"| {row.get('benchmark_label', '')} | {row.get('benchmark_type', '')} | "
            f"{number(row.get('beta'))} | {pct(row.get('alpha'))} | {pct(row.get('tracking_error'))} | "
            f"{number(row.get('information_ratio'))} |"
        )
    if len(lines) == 2:
        lines.append("| none |  |  |  |  |  |")
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
    data_quality_summary_rows: list[dict[str, str]] | None,
    benchmark_attribution_path: Path | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = metric_lookup(metrics_rows)
    gate = research_gate(summary, data_quality_summary_rows)
    lines = [
        f"# Strategy Diagnostics Pack {summary['period_start']}..{summary['period_end']}",
        "",
        "## Data Gate",
        "",
        *data_gate_table(gate),
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
    if data_quality_summary_rows is not None:
        lines.extend(["", "## Data Quality", "", *summary_count_table(data_quality_summary_rows, "data_quality_summary")])
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
    data_quality_rows = read_csv(args.data_quality_summary) if args.data_quality_summary else None
    summary = summarize_walkforward(summary_rows, failures)
    gate = research_gate(summary, data_quality_rows)
    rows = apply_gate_metrics(metric_rows(summary), gate)
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
        data_quality_summary_rows=data_quality_rows,
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
