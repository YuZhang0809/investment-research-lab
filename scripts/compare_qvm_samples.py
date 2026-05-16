from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from research_common import append_manifest, parse_float, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare QVM walk-forward outputs across two samples.")
    parser.add_argument("--sample-a-label", default="300")
    parser.add_argument("--sample-a-monthly", required=True, type=Path)
    parser.add_argument("--sample-a-monthly-failures", required=True, type=Path)
    parser.add_argument("--sample-a-quarterly", required=True, type=Path)
    parser.add_argument("--sample-a-quarterly-failures", required=True, type=Path)
    parser.add_argument("--sample-b-label", default="800")
    parser.add_argument("--sample-b-monthly", required=True, type=Path)
    parser.add_argument("--sample-b-monthly-failures", required=True, type=Path)
    parser.add_argument("--sample-b-quarterly", required=True, type=Path)
    parser.add_argument("--sample-b-quarterly-failures", required=True, type=Path)
    parser.add_argument("--capital-jpy", type=float, default=5_000_000)
    parser.add_argument("--out", type=Path, default=Path("reports/engineering/qvm_sample_300_vs_800.md"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    if value is None:
        return ""
    return f"JPY {value:,.0f}"


def avg(rows: list[dict[str, str]], column: str) -> float | None:
    values = [parse_float(row.get(column)) for row in rows]
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def final_return(final: dict[str, str], column: str, capital: float) -> float | None:
    value = parse_float(final.get(column))
    if value is None or not capital:
        return None
    return value / capital - 1.0


def summarize(summary_path: Path, failures_path: Path, capital: float) -> dict[str, Any]:
    rows = read_csv(summary_path)
    failures = read_csv(failures_path)
    if not rows:
        raise ValueError(f"No summary rows in {summary_path}")
    final = rows[-1]
    return {
        "start": rows[0].get("rebalance_date", ""),
        "end": final.get("rebalance_date", ""),
        "rows": len(rows),
        "universe_count": avg(rows, "universe_count"),
        "after_cost": final_return(final, "portfolio_equity_after_cost", capital),
        "after_tax": final_return(final, "after_tax_taxable_equity", capital),
        "benchmark": final_return(final, "benchmark_equity", capital),
        "research": final_return(final, "research_equity", capital),
        "avg_cash": avg(rows, "cash_pct"),
        "avg_turnover": avg(rows, "turnover"),
        "avg_holdings": avg(rows, "holdings_count"),
        "avg_zero_lot": avg(rows, "zero_lot_targets"),
        "avg_skipped": avg(rows, "skipped_orders"),
        "base_cost": parse_float(final.get("cumulative_cost_base")),
        "tax": parse_float(final.get("cumulative_tax")),
        "failures": Counter(row.get("failure_type", "") for row in failures if row.get("failure_type")),
    }


def delta(new: float | None, old: float | None, formatter) -> str:
    if new is None or old is None:
        return ""
    return formatter(new - old)


def metric_table(label_a: str, label_b: str, a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    rows = [
        ("date range", f"{a['start']}..{a['end']}", f"{b['start']}..{b['end']}", ""),
        ("rebalance rows", str(a["rows"]), str(b["rows"]), str(b["rows"] - a["rows"])),
        ("avg universe count", f"{a['universe_count']:.1f}", f"{b['universe_count']:.1f}", f"{b['universe_count'] - a['universe_count']:.1f}"),
        ("after-cost return", pct(a["after_cost"]), pct(b["after_cost"]), delta(b["after_cost"], a["after_cost"], pct)),
        ("after-tax taxable return", pct(a["after_tax"]), pct(b["after_tax"]), delta(b["after_tax"], a["after_tax"], pct)),
        ("filtered-universe benchmark", pct(a["benchmark"]), pct(b["benchmark"]), delta(b["benchmark"], a["benchmark"], pct)),
        ("theoretical research basket", pct(a["research"]), pct(b["research"]), delta(b["research"], a["research"], pct)),
        ("avg cash", pct(a["avg_cash"]), pct(b["avg_cash"]), delta(b["avg_cash"], a["avg_cash"], pct)),
        ("avg turnover", pct(a["avg_turnover"]), pct(b["avg_turnover"]), delta(b["avg_turnover"], a["avg_turnover"], pct)),
        ("avg holdings", f"{a['avg_holdings']:.1f}", f"{b['avg_holdings']:.1f}", f"{b['avg_holdings'] - a['avg_holdings']:.1f}"),
        ("avg zero-lot targets", f"{a['avg_zero_lot']:.1f}", f"{b['avg_zero_lot']:.1f}", f"{b['avg_zero_lot'] - a['avg_zero_lot']:.1f}"),
        ("avg skipped orders", f"{a['avg_skipped']:.1f}", f"{b['avg_skipped']:.1f}", f"{b['avg_skipped'] - a['avg_skipped']:.1f}"),
        ("cumulative base cost", money(a["base_cost"]), money(b["base_cost"]), money((b["base_cost"] or 0) - (a["base_cost"] or 0))),
        ("cumulative tax", money(a["tax"]), money(b["tax"]), money((b["tax"] or 0) - (a["tax"] or 0))),
    ]
    lines = [f"| metric | {label_a} | {label_b} | delta |", "|---|---:|---:|---:|"]
    for name, value_a, value_b, diff in rows:
        lines.append(f"| {name} | {value_a} | {value_b} | {diff} |")
    return lines


def failure_table(label_a: str, label_b: str, a: Counter[str], b: Counter[str]) -> list[str]:
    names = sorted(set(a) | set(b))
    lines = [f"| failure_type | {label_a} | {label_b} | delta |", "|---|---:|---:|---:|"]
    for name in names:
        lines.append(f"| {name} | {a.get(name, 0)} | {b.get(name, 0)} | {b.get(name, 0) - a.get(name, 0)} |")
    return lines


def read_direction(name: str, a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if b["avg_cash"] is not None and a["avg_cash"] is not None:
        if b["avg_cash"] < a["avg_cash"]:
            lines.append(f"- {name}: cash drag improved in the larger sample.")
        else:
            lines.append(f"- {name}: cash drag did not improve in the larger sample.")
    if b["after_tax"] is not None and a["after_tax"] is not None:
        if b["after_tax"] > a["after_tax"]:
            lines.append(f"- {name}: after-tax executable return improved versus the smaller sample.")
        else:
            lines.append(f"- {name}: after-tax executable return did not improve versus the smaller sample.")
    return lines


def write_report(path: Path, args: argparse.Namespace, monthly_a: dict[str, Any], monthly_b: dict[str, Any], quarterly_a: dict[str, Any], quarterly_b: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# QVM Sample Comparison",
        "",
        "## Scope",
        "",
        f"- sample A: {args.sample_a_label}",
        f"- sample B: {args.sample_b_label}",
        "- execution: next trading day open",
        "- cost scenario: base",
        "- tax model: rough FIFO realized-gain tax",
        "",
        "## Monthly",
        "",
        *metric_table(args.sample_a_label, args.sample_b_label, monthly_a, monthly_b),
        "",
        "### Monthly Failure Counts",
        "",
        *failure_table(args.sample_a_label, args.sample_b_label, monthly_a["failures"], monthly_b["failures"]),
        "",
        "## Quarterly",
        "",
        *metric_table(args.sample_a_label, args.sample_b_label, quarterly_a, quarterly_b),
        "",
        "### Quarterly Failure Counts",
        "",
        *failure_table(args.sample_a_label, args.sample_b_label, quarterly_a["failures"], quarterly_b["failures"]),
        "",
        "## Engineering Read",
        "",
        *read_direction("Monthly", monthly_a, monthly_b),
        *read_direction("Quarterly", quarterly_a, quarterly_b),
        "- Treat this as a tradability gate, not as a final factor-quality verdict.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    monthly_a = summarize(args.sample_a_monthly, args.sample_a_monthly_failures, args.capital_jpy)
    monthly_b = summarize(args.sample_b_monthly, args.sample_b_monthly_failures, args.capital_jpy)
    quarterly_a = summarize(args.sample_a_quarterly, args.sample_a_quarterly_failures, args.capital_jpy)
    quarterly_b = summarize(args.sample_b_quarterly, args.sample_b_quarterly_failures, args.capital_jpy)
    write_report(args.out, args, monthly_a, monthly_b, quarterly_a, quarterly_b)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_sample_comparison_report",
            file_path=args.out,
            vendor="local",
            schema_version="sample_comparison_report_v0_1",
            date_range=f"{monthly_a['start']}..{monthly_a['end']};{quarterly_a['start']}..{quarterly_a['end']}",
            notes=f"{args.sample_a_label} vs {args.sample_b_label}",
        )
    print(f"Wrote sample comparison report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
