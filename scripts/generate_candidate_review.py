from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_common import month_key, parse_date, parse_float, parse_int, read_csv, write_csv


RAW_FACTOR_FIELDS = [
    "operating_profit_to_total_assets",
    "equity_to_assets",
    "earnings_yield",
    "book_to_market",
    "return_12_1",
    "return_6_1",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Join QVM ranks, factors, targets, and constraints into candidate review CSV.")
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--factors", required=True, type=Path)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--orders", required=True, type=Path)
    parser.add_argument("--exclusions", type=Path)
    parser.add_argument("--include-exclusions", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/candidate_review"))
    return parser


def by_code(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("code", ""): row for row in rows if row.get("code")}


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def review_status(
    *,
    score: dict[str, str] | None,
    target: dict[str, str] | None,
    order: dict[str, str] | None,
    exclusion: dict[str, str] | None,
) -> str:
    if exclusion and not score and not target:
        return "excluded_before_scoring"
    if not score:
        return "unscored"
    if not target:
        return "scored_not_selected"
    target_shares = parse_int(target.get("target_shares"), default=0) or 0
    order_shares = parse_int(order.get("order_shares"), default=0) if order else target_shares
    if target_shares <= 0:
        return "selected_zero_lot"
    if order_shares is not None and order_shares <= 0:
        return "selected_not_orderable"
    if order and order.get("constraint_reason"):
        return "selected_constrained"
    return "selected_executable"


def main() -> int:
    args = build_parser().parse_args()
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")

    scores = by_code(read_csv(args.scores))
    factors = by_code(read_csv(args.factors))
    targets = by_code(read_csv(args.targets))
    orders = by_code(read_csv(args.orders))
    exclusions = by_code(read_csv(args.exclusions)) if args.exclusions and args.exclusions.exists() else {}

    codes = set(scores) | set(targets) | set(orders)
    if args.include_exclusions:
        codes |= set(exclusions)

    rows: list[dict[str, Any]] = []
    for code in sorted(codes, key=lambda value: parse_int(scores.get(value, {}).get("rank"), default=999999) or 999999):
        score = scores.get(code)
        factor = factors.get(code, {})
        target = targets.get(code)
        order = orders.get(code)
        exclusion = exclusions.get(code)
        selected = target is not None
        target_shares = parse_int(target.get("target_shares"), default=0) if target else 0
        order_shares = parse_int(order.get("order_shares"), default=0) if order else 0
        executable = bool(target_shares and target_shares > 0 and (order_shares is None or order_shares > 0))
        constraint_reason = ""
        if target:
            constraint_reason = target.get("target_constraint_reason", "")
        if order and order.get("constraint_reason"):
            constraint_reason = order.get("constraint_reason", "")
        if exclusion and exclusion.get("reason"):
            constraint_reason = constraint_reason or exclusion.get("reason", "")

        base = {
            "rebalance_date": args.rebalance_date,
            "code": code,
            "name": (score or target or order or exclusion or {}).get("name", ""),
            "sector": (score or target or factor or {}).get("sector", ""),
            "rank": score.get("rank", "") if score else "",
            "review_status": review_status(score=score, target=target, order=order, exclusion=exclusion),
            "selected_flag": bool_text(selected),
            "executable_flag": bool_text(executable),
            "qvm_score": score.get("qvm_score", "") if score else "",
            "composite_score": score.get("composite_score", "") if score else "",
            "quality_score": score.get("quality_score", "") if score else "",
            "value_score": score.get("value_score", "") if score else "",
            "momentum_score": score.get("momentum_score", "") if score else "",
            "filter_status": score.get("filter_status", "") if score else "",
            "filter_reasons": score.get("filter_reasons", "") if score else "",
            "missing_score_components": score.get("missing_score_components", "") if score else "",
            "missing_factor_flags": factor.get("missing_flags", ""),
            "latest_unadjusted_close": (target or score or factor).get("latest_unadjusted_close", "") if (target or score or factor) else "",
            "research_weight": target.get("research_weight", "") if target else "",
            "research_target_value": target.get("research_target_value", "") if target else "",
            "target_shares": target.get("target_shares", "") if target else "",
            "target_value": target.get("target_value", "") if target else "",
            "cash_drag_from_lot": target.get("cash_drag_from_lot", "") if target else "",
            "order_side": order.get("side", "") if order else "",
            "order_shares": order.get("order_shares", "") if order else "",
            "order_value": order.get("order_value", "") if order else "",
            "estimated_cost_base": order.get("estimated_cost_base", "") if order else "",
            "median_60d_trading_value": (target or order or {}).get("median_60d_trading_value", ""),
            "constraint_reason": constraint_reason,
            "exclusion_reason": exclusion.get("reason", "") if exclusion else "",
        }
        for field in RAW_FACTOR_FIELDS:
            base[field] = factor.get(field, "")
        rows.append(base)

    output_path = args.out_dir / f"candidate_review_{month_key(rebalance_date)}.csv"
    fieldnames = [
        "rebalance_date",
        "code",
        "name",
        "sector",
        "rank",
        "review_status",
        "selected_flag",
        "executable_flag",
        "qvm_score",
        "composite_score",
        "quality_score",
        "value_score",
        "momentum_score",
        "filter_status",
        "filter_reasons",
        "operating_profit_to_total_assets",
        "equity_to_assets",
        "earnings_yield",
        "book_to_market",
        "return_12_1",
        "return_6_1",
        "missing_score_components",
        "missing_factor_flags",
        "latest_unadjusted_close",
        "research_weight",
        "research_target_value",
        "target_shares",
        "target_value",
        "cash_drag_from_lot",
        "order_side",
        "order_shares",
        "order_value",
        "estimated_cost_base",
        "median_60d_trading_value",
        "constraint_reason",
        "exclusion_reason",
    ]
    write_csv(output_path, [{key: fmt(value) for key, value in row.items()} for row in rows], fieldnames)
    print(f"Wrote {len(rows)} candidate review rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
