from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_common import checksum, parse_float, read_csv, write_csv


DECISIONS = ["EXPLORATORY", "REVIEW", "REJECT", "PAPER_TEST"]
PHASES = ["EXPLORATORY", "VALIDATION", "PAPER_TEST"]
LEDGER_FIELDS = [
    "run_id",
    "run_at",
    "experiment_id",
    "phase",
    "config_hash",
    "data_hash",
    "universe_label",
    "period_start",
    "period_end",
    "strategy_label",
    "rebalance_frequency",
    "cost_scenario",
    "execution_price",
    "key_metric_after_cost",
    "key_metric_after_tax",
    "key_metric_benchmark",
    "market_benchmark_id",
    "market_beta",
    "market_alpha",
    "tracking_error",
    "information_ratio",
    "max_drawdown",
    "avg_cash_pct",
    "avg_turnover",
    "notes_path",
    "decision",
    "decision_reason",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append a walk-forward run summary to a CSV run ledger.")
    parser.add_argument("--summary", required=True, type=Path, help="Walk-forward summary CSV.")
    parser.add_argument("--config", required=True, type=Path, help="Config file used for the run.")
    parser.add_argument("--ledger", required=True, type=Path, help="Run ledger CSV to append.")
    parser.add_argument("--run-id", help="Stable run identifier. Defaults to a summary/config/data fingerprint.")
    parser.add_argument("--run-at", help="ISO timestamp. Defaults to current UTC time.")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--phase", choices=PHASES, default="EXPLORATORY")
    parser.add_argument("--universe-label", default="")
    parser.add_argument("--strategy-label", help="Defaults to strategy_version from the summary.")
    parser.add_argument("--notes-path", default="")
    parser.add_argument("--decision", choices=DECISIONS, default="EXPLORATORY")
    parser.add_argument("--decision-reason", default="")
    parser.add_argument("--data-path", action="append", type=Path, default=[], help="Input data path to fingerprint.")
    parser.add_argument("--data-hash", help="Explicit data hash/fingerprint. Overrides --data-path.")
    parser.add_argument("--allow-duplicate", action="store_true", help="Allow appending an existing run_id.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing run_id instead of failing.")
    parser.add_argument("--market-benchmark-id", default="")
    parser.add_argument("--market-beta", default="")
    parser.add_argument("--market-alpha", default="")
    parser.add_argument("--tracking-error", default="")
    parser.add_argument("--information-ratio", default="")
    return parser


def pct_return(equity: Any, capital: float | None) -> float | None:
    value = parse_float(equity)
    if value is None or capital is None or capital <= 0:
        return None
    return value / capital - 1.0


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    peak = -float("inf")
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = min(drawdown, value / peak - 1.0)
    return drawdown


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def summary_metrics(summary_rows: list[dict[str, str]]) -> dict[str, str]:
    if not summary_rows:
        raise ValueError("Summary CSV has no rows.")
    first = summary_rows[0]
    final = summary_rows[-1]
    capital = parse_float(first.get("capital_jpy")) or parse_float(final.get("capital_jpy"))
    if capital is None:
        capital = parse_float(first.get("portfolio_equity_pre"))

    portfolio_values = [
        value
        for value in (parse_float(row.get("portfolio_equity_after_cost")) for row in summary_rows)
        if value is not None
    ]
    cash_values = [value for value in (parse_float(row.get("cash_pct")) for row in summary_rows) if value is not None]
    turnover_values = [value for value in (parse_float(row.get("turnover")) for row in summary_rows) if value is not None]

    return {
        "period_start": first.get("rebalance_date", ""),
        "period_end": final.get("rebalance_date", ""),
        "strategy_label": final.get("strategy_version", "") or final.get("strategy_label", ""),
        "rebalance_frequency": final.get("frequency", ""),
        "cost_scenario": final.get("cost_scenario", ""),
        "execution_price": final.get("execution_price", ""),
        "key_metric_after_cost": fmt(pct_return(final.get("portfolio_equity_after_cost"), capital)),
        "key_metric_after_tax": fmt(pct_return(final.get("after_tax_taxable_equity"), capital)),
        "key_metric_benchmark": fmt(pct_return(final.get("benchmark_equity"), capital)),
        "max_drawdown": fmt(max_drawdown(portfolio_values)),
        "avg_cash_pct": fmt(mean(cash_values)),
        "avg_turnover": fmt(mean(turnover_values)),
    }


def data_fingerprint(paths: list[Path]) -> str:
    if not paths:
        return ""
    digests = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Data path does not exist: {path}")
        digests.append(checksum(path))
    return hashlib.sha256("\n".join(sorted(digests)).encode("utf-8")).hexdigest()


def default_run_id(summary_path: Path, config_hash: str, data_hash: str) -> str:
    payload = "|".join([summary_path.name, config_hash, data_hash])
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{summary_path.stem}_{token}"


def build_record(args: argparse.Namespace) -> dict[str, str]:
    summary_rows = read_csv(args.summary)
    metrics = summary_metrics(summary_rows)
    config_hash = checksum(args.config)
    data_hash = args.data_hash if args.data_hash is not None else data_fingerprint(args.data_path)
    run_id = args.run_id or default_run_id(args.summary, config_hash, data_hash)
    run_at = args.run_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    strategy_label = args.strategy_label or metrics["strategy_label"]
    record = {
        **{field: "" for field in LEDGER_FIELDS},
        **metrics,
        "run_id": run_id,
        "run_at": run_at,
        "experiment_id": args.experiment_id,
        "phase": args.phase,
        "config_hash": config_hash,
        "data_hash": data_hash,
        "universe_label": args.universe_label,
        "strategy_label": strategy_label,
        "notes_path": args.notes_path,
        "decision": args.decision,
        "decision_reason": args.decision_reason,
        "market_benchmark_id": args.market_benchmark_id,
        "market_beta": args.market_beta,
        "market_alpha": args.market_alpha,
        "tracking_error": args.tracking_error,
        "information_ratio": args.information_ratio,
    }
    return {field: fmt(record.get(field, "")) for field in LEDGER_FIELDS}


def append_record(ledger_path: Path, record: dict[str, str], *, allow_duplicate: bool, replace: bool) -> None:
    rows = read_csv(ledger_path) if ledger_path.exists() else []
    existing = [row for row in rows if row.get("run_id") == record["run_id"]]
    if existing and not allow_duplicate and not replace:
        raise ValueError(f"run_id already exists in ledger: {record['run_id']}")
    if replace:
        rows = [row for row in rows if row.get("run_id") != record["run_id"]]
    rows.append(record)
    write_csv(ledger_path, rows, LEDGER_FIELDS)


def main() -> int:
    args = build_parser().parse_args()
    if args.allow_duplicate and args.replace:
        raise ValueError("Use either --allow-duplicate or --replace, not both.")
    record = build_record(args)
    append_record(args.ledger, record, allow_duplicate=args.allow_duplicate, replace=args.replace)
    print(f"Appended run record {record['run_id']} to {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
