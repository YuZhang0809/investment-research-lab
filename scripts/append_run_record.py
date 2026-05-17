from __future__ import annotations

import argparse
import hashlib
import math
import os
import subprocess
import tempfile
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
    "code_version",
    "engine_hash",
    "universe_label",
    "period_start",
    "period_end",
    "rebalance_count",
    "strategy_label",
    "rebalance_frequency",
    "cost_scenario",
    "execution_price",
    "lifecycle_data_status",
    "performance_conclusion_allowed",
    "missing_price_tail_policy",
    "missing_price_tail_max_stale_days",
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
    parser.add_argument("--code-version", help="Code version identifier. Defaults to current git HEAD when available.")
    parser.add_argument("--engine-hash", help="Engine source hash. Defaults to a hash of public engine scripts.")
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


def periods_per_year(frequency: str) -> float:
    normalized = frequency.strip().lower()
    if normalized == "monthly":
        return 12.0
    if normalized == "quarterly":
        return 4.0
    return 1.0


def market_attribution(summary_rows: list[dict[str, str]]) -> dict[str, str]:
    if not summary_rows:
        return {}
    frequency = summary_rows[-1].get("frequency", "")
    annualizer = periods_per_year(frequency)
    paired: list[tuple[float, float]] = []
    for row in summary_rows[1:]:
        portfolio_return = parse_float(row.get("portfolio_return_after_cost"))
        market_return = parse_float(row.get("market_benchmark_return"))
        if portfolio_return is None or market_return is None:
            continue
        paired.append((portfolio_return, market_return))
    if len(paired) < 2:
        return {
            "market_benchmark_id": summary_rows[-1].get("market_benchmark_id", ""),
            "market_beta": "",
            "market_alpha": "",
            "tracking_error": "",
            "information_ratio": "",
        }

    portfolio_returns = [item[0] for item in paired]
    market_returns = [item[1] for item in paired]
    active_returns = [portfolio - market for portfolio, market in paired]
    mean_portfolio = mean(portfolio_returns) or 0.0
    mean_market = mean(market_returns) or 0.0
    mean_active = mean(active_returns) or 0.0
    market_variance = sum((value - mean_market) ** 2 for value in market_returns) / (len(market_returns) - 1)
    if market_variance <= 0:
        beta = None
    else:
        covariance = sum(
            (portfolio - mean_portfolio) * (market - mean_market)
            for portfolio, market in paired
        ) / (len(paired) - 1)
        beta = covariance / market_variance
    tracking_variance = sum((value - mean_active) ** 2 for value in active_returns) / (len(active_returns) - 1)
    tracking_error = math.sqrt(max(tracking_variance, 0.0)) * math.sqrt(annualizer)
    alpha = None if beta is None else (mean_portfolio - beta * mean_market) * annualizer
    information_ratio = None if tracking_error <= 0 else (mean_active * annualizer) / tracking_error
    return {
        "market_benchmark_id": summary_rows[-1].get("market_benchmark_id", ""),
        "market_beta": fmt(beta),
        "market_alpha": fmt(alpha),
        "tracking_error": fmt(tracking_error),
        "information_ratio": fmt(information_ratio),
    }


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

    metrics = {
        "period_start": first.get("rebalance_date", ""),
        "period_end": final.get("rebalance_date", ""),
        "rebalance_count": fmt(len(summary_rows)),
        "strategy_label": final.get("strategy_version", "") or final.get("strategy_label", ""),
        "rebalance_frequency": final.get("frequency", ""),
        "cost_scenario": final.get("cost_scenario", ""),
        "execution_price": final.get("execution_price", ""),
        "lifecycle_data_status": final.get("lifecycle_data_status", ""),
        "performance_conclusion_allowed": final.get("performance_conclusion_allowed", ""),
        "missing_price_tail_policy": final.get("missing_price_tail_policy", ""),
        "missing_price_tail_max_stale_days": final.get("missing_price_tail_max_stale_days", ""),
        "key_metric_after_cost": fmt(pct_return(final.get("portfolio_equity_after_cost"), capital)),
        "key_metric_after_tax": fmt(pct_return(final.get("after_tax_taxable_equity"), capital)),
        "key_metric_benchmark": fmt(pct_return(final.get("benchmark_equity"), capital)),
        "max_drawdown": fmt(max_drawdown(portfolio_values)),
        "avg_cash_pct": fmt(mean(cash_values)),
        "avg_turnover": fmt(mean(turnover_values)),
    }
    metrics.update(market_attribution(summary_rows))
    return metrics


def data_fingerprint(paths: list[Path]) -> str:
    if not paths:
        return ""
    digests = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Data path does not exist: {path}")
        digests.append(checksum(path))
    return hashlib.sha256("\n".join(sorted(digests)).encode("utf-8")).hexdigest()


def git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def engine_fingerprint() -> str:
    scripts_dir = Path(__file__).resolve().parent
    source_files = [
        "research_common.py",
        "build_universe.py",
        "build_factors.py",
        "build_scores.py",
        "run_qvm_walkforward.py",
        "append_run_record.py",
        "generate_decision_note.py",
    ]
    digests = []
    for name in source_files:
        path = scripts_dir / name
        if path.exists():
            digests.append(f"{name}:{checksum(path)}")
    return hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest()


def default_run_id(summary_path: Path, config_hash: str, data_hash: str, code_version: str, engine_hash: str) -> str:
    payload = "|".join([summary_path.name, config_hash, data_hash, code_version, engine_hash])
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{summary_path.stem}_{token}"


def build_record(args: argparse.Namespace) -> dict[str, str]:
    summary_rows = read_csv(args.summary)
    metrics = summary_metrics(summary_rows)
    config_hash = checksum(args.config)
    data_hash = args.data_hash if args.data_hash is not None else data_fingerprint(args.data_path)
    code_version = args.code_version if args.code_version is not None else git_head()
    engine_hash = args.engine_hash if args.engine_hash is not None else engine_fingerprint()
    run_id = args.run_id or default_run_id(args.summary, config_hash, data_hash, code_version, engine_hash)
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
        "code_version": code_version,
        "engine_hash": engine_hash,
        "universe_label": args.universe_label,
        "strategy_label": strategy_label,
        "notes_path": args.notes_path,
        "decision": args.decision,
        "decision_reason": args.decision_reason,
        "market_benchmark_id": args.market_benchmark_id or metrics.get("market_benchmark_id", ""),
        "market_beta": args.market_beta or metrics.get("market_beta", ""),
        "market_alpha": args.market_alpha or metrics.get("market_alpha", ""),
        "tracking_error": args.tracking_error or metrics.get("tracking_error", ""),
        "information_ratio": args.information_ratio or metrics.get("information_ratio", ""),
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
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=ledger_path.parent,
        prefix=f".{ledger_path.name}.",
        suffix=".tmp",
    ) as file:
        tmp_path = Path(file.name)
    try:
        write_csv(tmp_path, rows, LEDGER_FIELDS)
        os.replace(tmp_path, ledger_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


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
