from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from research_common import append_manifest, parse_float, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare multiple QVM walk-forward summary/failure outputs.")
    parser.add_argument("--summary", action="append", required=True, type=Path, help="Walk-forward summary CSV. Repeat for each run.")
    parser.add_argument("--label", action="append", help="Label for each summary. Repeat in the same order as --summary.")
    parser.add_argument("--failures", action="append", type=Path, help="Optional failure-case CSV. Repeat in the same order as --summary.")
    parser.add_argument(
        "--capital-jpy",
        type=float,
        default=None,
        help="Fallback initial capital when a summary cannot infer it from portfolio_equity_pre.",
    )
    parser.add_argument("--out", type=Path, default=Path("reports/walkforward/walkforward_comparison.md"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def max_drawdown(values: list[float]) -> float:
    peak = float("-inf")
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = min(drawdown, value / peak - 1.0)
    return drawdown


def avg(rows: list[dict[str, str]], column: str) -> float | None:
    values = [parse_float(row.get(column)) for row in rows]
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    if value is None:
        return ""
    return f"JPY {value:,.0f}"


def number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.1f}"


def summarize(label: str, summary_path: Path, failures_path: Path | None, fallback_capital: float | None) -> dict[str, Any]:
    rows = read_csv(summary_path)
    if not rows:
        raise ValueError(f"No rows in {summary_path}")
    final = rows[-1]
    initial_capital = parse_float(rows[0].get("portfolio_equity_pre")) or fallback_capital
    if not initial_capital:
        raise ValueError(
            f"Cannot infer initial capital for {summary_path}; pass --capital-jpy as fallback."
        )
    values = [parse_float(row.get("portfolio_equity_after_cost")) for row in rows]
    clean_values = [value for value in values if value is not None]
    failure_rows = read_csv(failures_path) if failures_path and failures_path.exists() else []
    failure_counts = Counter(row.get("failure_type", "") for row in failure_rows if row.get("failure_type"))
    final_equity = parse_float(final.get("portfolio_equity_after_cost"))
    benchmark_equity = parse_float(final.get("benchmark_equity"))
    research_equity = parse_float(final.get("research_equity"))
    return {
        "label": label,
        "months": len(rows),
        "start": rows[0].get("rebalance_date", ""),
        "end": final.get("rebalance_date", ""),
        "frequency": final.get("frequency", ""),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "final_return": final_equity / initial_capital - 1 if final_equity is not None else None,
        "benchmark_return": benchmark_equity / initial_capital - 1 if benchmark_equity is not None else None,
        "research_return": research_equity / initial_capital - 1 if research_equity is not None else None,
        "max_drawdown": max_drawdown(clean_values) if clean_values else None,
        "avg_cash_pct": avg(rows, "cash_pct"),
        "avg_turnover": avg(rows, "turnover"),
        "avg_holdings": avg(rows, "holdings_count"),
        "avg_zero_lot": avg(rows, "zero_lot_targets"),
        "avg_skipped": avg(rows, "skipped_orders"),
        "total_cost_base": sum(parse_float(row.get("estimated_cost_base"), default=0.0) or 0.0 for row in rows),
        "failures": failure_counts,
    }


def write_report(path: Path, summaries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Walk-Forward Comparison",
        "",
        "| run | frequency | period | months | initial capital | final equity | return | benchmark | research | max DD | avg cash | avg turnover | avg holdings | zero-lot | skipped | base cost |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['label']} | {row['frequency'] or 'unknown'} | {row['start']}..{row['end']} | {row['months']} | "
            f"{money(row['initial_capital'])} | {money(row['final_equity'])} | {pct(row['final_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['research_return'])} | "
            f"{pct(row['max_drawdown'])} | {pct(row['avg_cash_pct'])} | "
            f"{pct(row['avg_turnover'])} | {number(row['avg_holdings'])} | "
            f"{number(row['avg_zero_lot'])} | {number(row['avg_skipped'])} | {money(row['total_cost_base'])} |"
        )
    all_failure_types = sorted({key for row in summaries for key in row["failures"]})
    lines.extend(["", "## Failure Cases", "", "| failure type | " + " | ".join(row["label"] for row in summaries) + " |"])
    lines.append("|---|" + "|".join("---:" for _ in summaries) + "|")
    if all_failure_types:
        for failure_type in all_failure_types:
            counts = [str(row["failures"].get(failure_type, 0)) for row in summaries]
            lines.append(f"| {failure_type} | " + " | ".join(counts) + " |")
    else:
        lines.append("| none | " + " | ".join("0" for _ in summaries) + " |")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "This comparison is a lightweight research summary. Read each source run's candidate and failure-case outputs before drawing conclusions.",
            "",
            "Max drawdown is computed on each run's recorded equity points. Runs with different rebalance frequencies have different sampling density, so monthly and quarterly drawdowns are not directly comparable.",
            "",
            "This script only compares the runs provided on the command line. It is not an append-only experiment registry and cannot protect against selecting only the best runs after many trials.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    labels = args.label or [path.stem for path in args.summary]
    if len(labels) != len(args.summary):
        raise ValueError("--label count must match --summary count")
    failures = args.failures or []
    if failures and len(failures) != len(args.summary):
        raise ValueError("--failures count must match --summary count when provided")
    summaries = [
        summarize(
            label=label,
            summary_path=summary_path,
            failures_path=failures[index] if failures else None,
            fallback_capital=args.capital_jpy,
        )
        for index, (label, summary_path) in enumerate(zip(labels, args.summary))
    ]
    write_report(args.out, summaries)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_walkforward_comparison_report",
            file_path=args.out,
            vendor="local",
            schema_version="walkforward_comparison_report_v0_1",
            date_range=";".join(f"{row['label']}:{row['start']}..{row['end']}" for row in summaries),
            notes=f"{len(summaries)} runs",
        )
    print(f"Wrote walk-forward comparison to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
