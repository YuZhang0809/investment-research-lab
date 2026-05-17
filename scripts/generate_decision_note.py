from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from append_run_record import DECISIONS, summary_metrics
from research_common import load_yaml, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a lightweight research decision note.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ledger", type=Path, help="Run ledger CSV.")
    source.add_argument("--summary", type=Path, help="Walk-forward summary CSV.")
    parser.add_argument("--run-id", help="Run ID to select from ledger. Defaults to the last ledger row.")
    parser.add_argument("--rules-config", type=Path, help="Optional YAML with decision_note defaults.")
    parser.add_argument("--out", required=True, type=Path, help="Markdown note output path.")
    parser.add_argument("--decision", choices=DECISIONS, help="Override decision label.")
    parser.add_argument("--decision-reason", help="Override short reason.")
    parser.add_argument("--known-caveat", action="append", default=[], help="Known caveat bullet. Repeatable.")
    parser.add_argument("--next-action", help="Suggested next action.")
    return parser


def pct(value: Any) -> str:
    if value is None or str(value) == "":
        return ""
    try:
        return f"{float(value) * 100:.2f}%"
    except ValueError:
        return str(value)


def number(value: Any) -> str:
    if value is None or str(value) == "":
        return ""
    try:
        return f"{float(value):.4f}"
    except ValueError:
        return str(value)


def load_rules(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    values = load_yaml(path)
    return values.get("decision_note", values) if isinstance(values, dict) else {}


def ledger_record(path: Path, run_id: str | None) -> dict[str, str]:
    rows = read_csv(path)
    if not rows:
        raise ValueError("Run ledger has no rows.")
    if run_id:
        matches = [row for row in rows if row.get("run_id") == run_id]
        if not matches:
            raise ValueError(f"run_id not found in ledger: {run_id}")
        return matches[-1]
    return rows[-1]


def summary_record(path: Path) -> dict[str, str]:
    metrics = summary_metrics(read_csv(path))
    return {
        "run_id": path.stem,
        "experiment_id": "",
        "phase": "EXPLORATORY",
        "decision": "EXPLORATORY",
        "decision_reason": "",
        **metrics,
    }


def coalesce(*values: Any) -> str:
    for value in values:
        if value is not None and str(value) != "":
            return str(value)
    return ""


def note_lines(record: dict[str, str], args: argparse.Namespace, rules: dict[str, Any]) -> list[str]:
    decision = coalesce(args.decision, record.get("decision"), rules.get("decision"), "EXPLORATORY")
    if decision not in DECISIONS:
        raise ValueError(f"Unsupported decision: {decision}")
    reason = coalesce(args.decision_reason, record.get("decision_reason"), rules.get("reason"), "Research note generated from run metrics.")
    configured_caveats = rules.get("known_caveats", []) or []
    if isinstance(configured_caveats, str):
        configured_caveats = [configured_caveats]
    caveats = list(configured_caveats) + list(args.known_caveat)
    if not caveats:
        caveats = ["Research note only; not an approval, compliance artifact, or immutable record."]
    next_action = coalesce(args.next_action, rules.get("next_action"), "Review candidates, failures, and assumptions before another run.")

    metric_rows = [
        ("after-cost return", pct(record.get("key_metric_after_cost"))),
        ("after-tax return", pct(record.get("key_metric_after_tax"))),
        ("benchmark return", pct(record.get("key_metric_benchmark"))),
        ("max drawdown", pct(record.get("max_drawdown"))),
        ("avg cash pct", pct(record.get("avg_cash_pct"))),
        ("avg turnover", pct(record.get("avg_turnover"))),
    ]
    optional_metrics = [
        ("market benchmark", record.get("market_benchmark_id", "")),
        ("market beta", number(record.get("market_beta"))),
        ("market alpha", pct(record.get("market_alpha"))),
        ("tracking error", pct(record.get("tracking_error"))),
        ("information ratio", number(record.get("information_ratio"))),
    ]
    metric_rows.extend([row for row in optional_metrics if row[1]])

    lines = [
        f"# Decision Note: {record.get('run_id', '')}",
        "",
        "This is a research note, not an approval or compliance record.",
        "",
        "## Decision",
        "",
        f"- decision: {decision}",
        f"- short reason: {reason}",
        f"- experiment_id: {record.get('experiment_id', '')}",
        f"- phase: {record.get('phase', '')}",
        f"- period: {record.get('period_start', '')}..{record.get('period_end', '')}",
        "",
        "## Key Metrics",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    lines.extend([f"| {name} | {value} |" for name, value in metric_rows])
    lines.extend(["", "## Known Caveats", ""])
    lines.extend([f"- {value}" for value in caveats])
    lines.extend(["", "## Next Action", "", next_action, ""])
    return lines


def main() -> int:
    args = build_parser().parse_args()
    rules = load_rules(args.rules_config)
    record = ledger_record(args.ledger, args.run_id) if args.ledger else summary_record(args.summary)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(note_lines(record, args, rules)), encoding="utf-8")
    print(f"Wrote decision note to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
