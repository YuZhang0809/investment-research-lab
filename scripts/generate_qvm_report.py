from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from research_common import parse_float, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Markdown research report for QVM CSV pipeline.")
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--factors", required=True, type=Path)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--orders", required=True, type=Path)
    parser.add_argument("--backtest-summary", required=True, type=Path)
    parser.add_argument("--candidate-review", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    return parser


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    if value is None:
        return ""
    return f"JPY {value:,.0f}"


def main() -> int:
    args = build_parser().parse_args()
    factors = read_csv(args.factors)
    scores = read_csv(args.scores)
    targets = read_csv(args.targets)
    orders = read_csv(args.orders)
    summary = read_csv(args.backtest_summary)
    candidate_review = read_csv(args.candidate_review) if args.candidate_review and args.candidate_review.exists() else []
    output = args.out or Path(f"reports/monthly/qvm_pipeline_{args.rebalance_date[:7].replace('-', '')}.md")
    output.parent.mkdir(parents=True, exist_ok=True)

    missing_counter: Counter[str] = Counter()
    for row in factors:
        for flag in (row.get("missing_flags") or "").split(";"):
            if flag:
                missing_counter[flag] += 1
    sector_counter = Counter(row.get("sector", "") for row in targets)
    ranked = sorted(scores, key=lambda row: int(row.get("rank") or 999999))[:10]
    order_reductions = [row for row in orders if row.get("constraint_reason") or row.get("side") == "SKIP"]
    executable_targets = [row for row in targets if parse_float(row.get("target_shares"), default=0) > 0]
    zero_lot_targets = [row for row in targets if parse_float(row.get("target_shares"), default=0) == 0]
    side_counter = Counter(row.get("side", "") for row in orders)
    review_status_counter = Counter(row.get("review_status", "") for row in candidate_review)
    constraint_counter = Counter(
        row.get("constraint_reason", "")
        for row in candidate_review
        if row.get("constraint_reason")
    )
    summary_row = summary[0] if summary else {}
    capital = parse_float(summary_row.get("capital_jpy"))
    cash = parse_float(summary_row.get("cash"))
    cash_pct = cash / capital if cash is not None and capital else None

    lines = [
        f"# QVM Research Report {args.rebalance_date}",
        "",
        "## Scope",
        "",
        f"- factor rows: {len(factors)}",
        f"- score rows: {len(scores)}",
        f"- target rows: {len(targets)}",
        f"- order rows: {len(orders)}",
        "",
        "## Candidate Review",
        "",
        f"- selected targets: {len(targets)}",
        f"- executable targets after lot and order checks: {len(executable_targets)}",
        f"- zero-lot targets: {len(zero_lot_targets)}",
        f"- constrained / skipped orders: {len(order_reductions)}",
        f"- cash after snapshot orders: {money(cash)} ({pct(cash_pct)})",
        "",
        "| review status | count |",
        "|---|---:|",
    ]
    if review_status_counter:
        for key, count in review_status_counter.most_common():
            lines.append(f"| {key or 'unknown'} | {count} |")
    else:
        lines.append("| candidate_review_not_provided | 0 |")
    lines.extend(["", "## Constraint Reasons", "", "| reason | count |", "|---|---:|"])
    if constraint_counter:
        for key, count in constraint_counter.most_common():
            lines.append(f"| {key} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Factor QA",
            "",
            "| missing flag | count |",
            "|---|---:|",
        ]
    )
    if missing_counter:
        for key, count in missing_counter.most_common():
            lines.append(f"| {key} | {count} |")
    else:
        lines.append(f"| none | {len(factors)} |")
    lines.extend(["", "## Top Scores", "", "| rank | code | name | qvm | quality | value | momentum |", "|---:|---|---|---:|---:|---:|---:|"])
    for row in ranked:
        lines.append(
            f"| {row.get('rank')} | {row.get('code')} | {row.get('name')} | "
            f"{parse_float(row.get('qvm_score'), default=0):.3f} | "
            f"{parse_float(row.get('quality_score'), default=0):.3f} | "
            f"{parse_float(row.get('value_score'), default=0):.3f} | "
            f"{parse_float(row.get('momentum_score'), default=0):.3f} |"
        )
    lines.extend(["", "## Target Sector Exposure", "", "| sector | count |", "|---|---:|"])
    for sector, count in sector_counter.most_common():
        lines.append(f"| {sector or 'unknown'} | {count} |")
    lines.extend(
        [
            "",
            "## Execution",
            "",
            f"- selected targets: {len(targets)}",
            f"- executable targets after 100-share lot: {len(executable_targets)}",
            f"- zero-lot targets: {len(zero_lot_targets)}",
            f"- buy orders: {side_counter.get('BUY', 0)}",
            f"- skip orders: {side_counter.get('SKIP', 0)}",
            f"- constrained / skipped orders: {len(order_reductions)}",
            f"- invested value: {money(parse_float(summary_row.get('invested_value')))}",
            f"- cash: {money(cash)} ({pct(cash_pct)})",
            f"- estimated base cost: {money(parse_float(summary_row.get('estimated_cost_base')))}",
            f"- after-cost snapshot return: {pct(parse_float(summary_row.get('after_cost_pre_tax_return')))}",
            "",
            "## Caveat",
            "",
            "This is a single-rebalance execution snapshot. It validates the CSV pipeline and execution constraints, not strategy performance.",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote report to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
