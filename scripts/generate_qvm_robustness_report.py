from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from research_common import append_manifest, parse_float, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a QVM robustness report from walk-forward CSVs.")
    parser.add_argument("--monthly-summary", required=True, type=Path)
    parser.add_argument("--monthly-failures", required=True, type=Path)
    parser.add_argument("--quarterly-summary", required=True, type=Path)
    parser.add_argument("--quarterly-failures", required=True, type=Path)
    parser.add_argument("--capital-jpy", type=float, default=5_000_000)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports/robustness/qvm_robustness_202401_202604.md"),
    )
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def max_drawdown(values: list[float]) -> float:
    peak = None
    worst = 0.0
    for value in values:
        peak = value if peak is None else max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1.0)
    return worst


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


def summarize(rows: list[dict[str, str]], failures: list[dict[str, str]], capital: float) -> dict[str, Any]:
    if not rows:
        raise ValueError("summary CSV has no rows")
    final = rows[-1]
    portfolio_values = [parse_float(row.get("portfolio_equity_after_cost")) for row in rows]
    taxable_values = [parse_float(row.get("after_tax_taxable_equity")) for row in rows]
    portfolio_clean = [value for value in portfolio_values if value is not None]
    taxable_clean = [value for value in taxable_values if value is not None]
    failure_counts = Counter(row.get("failure_type", "") for row in failures if row.get("failure_type"))
    return {
        "months": len(rows),
        "start": rows[0].get("rebalance_date", ""),
        "end": final.get("rebalance_date", ""),
        "frequency": final.get("frequency", ""),
        "execution_price": final.get("execution_price", ""),
        "cost_scenario": final.get("cost_scenario", ""),
        "portfolio_return": final_return(final, "portfolio_equity_after_cost", capital),
        "taxable_return": final_return(final, "after_tax_taxable_equity", capital),
        "benchmark_return": final_return(final, "benchmark_equity", capital),
        "research_return": final_return(final, "research_equity", capital),
        "optimistic_return": final_return(final, "portfolio_equity_optimistic", capital),
        "pessimistic_return": final_return(final, "portfolio_equity_pessimistic", capital),
        "portfolio_mdd": max_drawdown(portfolio_clean),
        "taxable_mdd": max_drawdown(taxable_clean),
        "avg_cash": avg(rows, "cash_pct"),
        "avg_turnover": avg(rows, "turnover"),
        "avg_holdings": avg(rows, "holdings_count"),
        "avg_zero_lot": avg(rows, "zero_lot_targets"),
        "avg_skipped": avg(rows, "skipped_orders"),
        "base_cost": parse_float(final.get("cumulative_cost_base")),
        "tax": parse_float(final.get("cumulative_tax")),
        "failure_counts": failure_counts,
    }


def failure_table(counts: Counter[str]) -> list[str]:
    if not counts:
        return ["| failure_type | count |", "|---|---:|", "| none | 0 |"]
    lines = ["| failure_type | count |", "|---|---:|"]
    for name, count in counts.most_common():
        lines.append(f"| {name} | {count} |")
    return lines


def write_report(path: Path, monthly: dict[str, Any], quarterly: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# QVM Robustness Report",
        "",
        "## Scope",
        "",
        "- sample: user-provided local research sample",
        "- execution: next trading day open",
        "- cost scenario used for actual cash path: base",
        "- tax model: rough FIFO realized-gain tax, dividends not yet modeled",
        "- caveat: monthly and quarterly windows differ because quarterly uses quarter-end rebalance dates only",
        "",
        "## Monthly vs Quarterly",
        "",
        "| metric | monthly | quarterly |",
        "|---|---:|---:|",
        f"| date range | {monthly['start']}..{monthly['end']} | {quarterly['start']}..{quarterly['end']} |",
        f"| rebalance rows | {monthly['months']} | {quarterly['months']} |",
        f"| after-cost return | {pct(monthly['portfolio_return'])} | {pct(quarterly['portfolio_return'])} |",
        f"| after-tax taxable return | {pct(monthly['taxable_return'])} | {pct(quarterly['taxable_return'])} |",
        f"| filtered universe benchmark | {pct(monthly['benchmark_return'])} | {pct(quarterly['benchmark_return'])} |",
        f"| theoretical research basket | {pct(monthly['research_return'])} | {pct(quarterly['research_return'])} |",
        f"| portfolio max drawdown | {pct(monthly['portfolio_mdd'])} | {pct(quarterly['portfolio_mdd'])} |",
        f"| taxable max drawdown | {pct(monthly['taxable_mdd'])} | {pct(quarterly['taxable_mdd'])} |",
        f"| avg cash | {pct(monthly['avg_cash'])} | {pct(quarterly['avg_cash'])} |",
        f"| avg turnover | {pct(monthly['avg_turnover'])} | {pct(quarterly['avg_turnover'])} |",
        f"| avg holdings | {monthly['avg_holdings']:.1f} | {quarterly['avg_holdings']:.1f} |",
        f"| avg zero-lot targets | {monthly['avg_zero_lot']:.1f} | {quarterly['avg_zero_lot']:.1f} |",
        f"| avg skipped orders | {monthly['avg_skipped']:.1f} | {quarterly['avg_skipped']:.1f} |",
        f"| cumulative base cost | {money(monthly['base_cost'])} | {money(quarterly['base_cost'])} |",
        f"| cumulative taxable tax | {money(monthly['tax'])} | {money(quarterly['tax'])} |",
        "",
        "## Cost Scenario Sensitivity",
        "",
        "| metric | monthly | quarterly |",
        "|---|---:|---:|",
        f"| optimistic final return | {pct(monthly['optimistic_return'])} | {pct(quarterly['optimistic_return'])} |",
        f"| base final return | {pct(monthly['portfolio_return'])} | {pct(quarterly['portfolio_return'])} |",
        f"| pessimistic final return | {pct(monthly['pessimistic_return'])} | {pct(quarterly['pessimistic_return'])} |",
        "",
        "## Monthly Failure Counts",
        "",
        *failure_table(monthly["failure_counts"]),
        "",
        "## Quarterly Failure Counts",
        "",
        *failure_table(quarterly["failure_counts"]),
        "",
        "## Engineering Read",
        "",
        "- The executable portfolio still trails the filtered-universe and theoretical baskets, so alpha discussion remains premature.",
        "- ADV caps and cash drag are the main execution blockers. This argues for either larger/more liquid samples, fewer target names, or lower per-name target value.",
        "- Quarterly rebalancing reduces cumulative cost and realized-tax drag, but it does not solve lot-size cash drag by itself.",
        "- Next gate should focus on sample expansion, paper-trading logs, and rulebook discipline before any live trading decision.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    monthly = summarize(read_csv(args.monthly_summary), read_csv(args.monthly_failures), args.capital_jpy)
    quarterly = summarize(read_csv(args.quarterly_summary), read_csv(args.quarterly_failures), args.capital_jpy)
    write_report(args.out, monthly, quarterly)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_robustness_report",
            file_path=args.out,
            vendor="local",
            schema_version="robustness_report_v0_1",
            date_range=f"{monthly['start']}..{monthly['end']};{quarterly['start']}..{quarterly['end']}",
            notes="QVM monthly vs quarterly robustness report",
        )
    print(f"Wrote robustness report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
