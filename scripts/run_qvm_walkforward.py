from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
import math
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from build_factors import build_factors, factor_output_fields
from factor_expressions import (
    factor_definition_dependency_graph,
    factor_definition_fingerprints,
    factor_definition_names,
)
from build_scores import STRATEGY_VERSION_CHOICES, build_scores
from build_universe import build_universe_from_rows
from research_common import (
    append_manifest,
    checksum,
    load_yaml,
    month_key,
    parse_date,
    parse_float,
    parse_int,
    read_csv,
    write_csv,
    write_table,
)


@dataclass
class PricePoint:
    date: date
    unadjusted_open: float
    unadjusted_close: float
    adjusted_close: float
    trading_value: float | None
    price_limit_flag: bool


@dataclass
class MarketBenchmarkPoint:
    date: date
    value: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run QVM walk-forward rebalance loop.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="monthly")
    parser.add_argument("--rebalance", choices=["monthly", "quarterly"], help="Alias for --frequency.")
    parser.add_argument("--strategy-version", choices=STRATEGY_VERSION_CHOICES, default="qvm")
    parser.add_argument("--target-holdings", type=int, help="Override executable target holdings min/max.")
    parser.add_argument("--adv-cap", type=float, help="Override max order value as a fraction of median ADV.")
    parser.add_argument(
        "--execution-price",
        choices=["rebalance_close", "next_open", "next_close"],
        default="rebalance_close",
        help="Price timing used for simulated fills.",
    )
    parser.add_argument("--cost-scenario", choices=["optimistic", "base", "pessimistic"], default="base")
    parser.add_argument("--tax-rate", type=float, default=0.20315)
    parser.add_argument("--run-label", help="Optional token included in output filenames.")
    parser.add_argument("--capital-jpy", type=float, default=5_000_000)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/walkforward"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/walkforward"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument(
        "--market-benchmark-prices",
        type=Path,
        help="Optional market benchmark series with date and adjusted_close/close/value columns.",
    )
    parser.add_argument(
        "--market-benchmark-id",
        help="Optional benchmark ID selected from benchmark_id/index_code/code/id/ticker columns.",
    )
    parser.add_argument("--cache-dir", type=Path, help="Directory for reusable walk-forward Parquet cache.")
    parser.add_argument(
        "--cache-format",
        choices=["parquet"],
        help="Enable cache writes in this format. Defaults to parquet when --cache-dir is set.",
    )
    parser.add_argument("--force-rebuild", action="store_true", help="Rebuild cached inputs and rebalance stages.")
    parser.add_argument("--no-manifest", action="store_true")
    parser.add_argument("--skip-stage-manifest", action="store_true")
    parser.add_argument(
        "--allow-snapshot-listings",
        action="store_true",
        help="Allow listings without lifecycle dates. Use only for exploratory survivor-biased samples.",
    )
    return parser


def run(command: list[str]) -> None:
    print(" ".join(command))
    subprocess.run(command, check=True)


def parse_price_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def build_price_index(price_rows: list[dict[str, str]]) -> dict[str, list[PricePoint]]:
    raw_grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in price_rows:
        code = row.get("code", "")
        if code:
            raw_grouped[code].append(row)

    grouped: dict[str, list[PricePoint]] = defaultdict(list)
    for code, rows in raw_grouped.items():
        sorted_rows = sorted(
            rows,
            key=lambda item: parse_date(item.get("date"), field_name="prices.date") or date.min,
        )
        cumulative_adjustment = 1.0
        for row in sorted_rows:
            adjustment_factor = parse_float(row.get("adjustment_factor"), default=1.0) or 1.0
            if adjustment_factor > 0:
                cumulative_adjustment *= adjustment_factor
            row_date = parse_date(row.get("date"), field_name="prices.date")
            unadjusted_open = parse_float(row.get("unadjusted_open"))
            unadjusted = parse_float(row.get("unadjusted_close"))
            adjusted = parse_float(row.get("adjusted_close"))
            if adjusted is None and unadjusted is not None:
                adjusted = unadjusted / cumulative_adjustment
            if not code or row_date is None or unadjusted is None or adjusted is None:
                continue
            grouped[code].append(
                PricePoint(
                    date=row_date,
                    unadjusted_open=unadjusted_open if unadjusted_open is not None else unadjusted,
                    unadjusted_close=unadjusted,
                    adjusted_close=adjusted,
                    trading_value=parse_float(row.get("trading_value")),
                    price_limit_flag=parse_price_flag(row.get("price_limit_flag")),
                )
            )
    for values in grouped.values():
        values.sort(key=lambda point: point.date)
    return grouped


def market_benchmark_id_column(rows: list[dict[str, str]]) -> str | None:
    if not rows:
        return None
    columns = set(rows[0])
    for column in ["benchmark_id", "index_code", "code", "id", "ticker"]:
        if column in columns:
            return column
    return None


def market_benchmark_value(row: dict[str, str]) -> float | None:
    for column in ["adjusted_close", "close", "index_value", "value", "price", "unadjusted_close"]:
        value = parse_float(row.get(column))
        if value is not None and value > 0:
            return value
    return None


def build_market_benchmark_series(
    rows: list[dict[str, str]],
    benchmark_id: str | None = None,
) -> tuple[str, list[MarketBenchmarkPoint]]:
    if not rows:
        return benchmark_id or "", []
    id_column = market_benchmark_id_column(rows)
    available_ids = sorted({(row.get(id_column) or "").strip() for row in rows if id_column and row.get(id_column)})
    selected_id = benchmark_id or (available_ids[0] if len(available_ids) == 1 else "")
    if id_column and len(available_ids) > 1 and not benchmark_id:
        raise ValueError(f"Market benchmark file contains multiple IDs in {id_column}; pass --market-benchmark-id.")

    points: list[MarketBenchmarkPoint] = []
    for row in rows:
        if id_column and benchmark_id and (row.get(id_column) or "").strip() != benchmark_id:
            continue
        row_date = parse_date(row.get("date"), field_name="market_benchmark.date")
        value = market_benchmark_value(row)
        if row_date is None or value is None:
            continue
        points.append(MarketBenchmarkPoint(row_date, value))
    points.sort(key=lambda point: point.date)
    if benchmark_id and not points:
        raise ValueError(f"No market benchmark rows found for --market-benchmark-id {benchmark_id!r}.")
    return selected_id or benchmark_id or "market_benchmark", points


def market_benchmark_at(points: list[MarketBenchmarkPoint], as_of: date) -> MarketBenchmarkPoint | None:
    latest: MarketBenchmarkPoint | None = None
    for point in points:
        if point.date > as_of:
            break
        latest = point
    return latest


def market_benchmark_return(points: list[MarketBenchmarkPoint], start: date, end: date) -> float | None:
    start_point = market_benchmark_at(points, start)
    end_point = market_benchmark_at(points, end)
    if not start_point or not end_point or start_point.value <= 0:
        return None
    return end_point.value / start_point.value - 1.0


def all_price_dates(price_index: dict[str, list[PricePoint]]) -> list[date]:
    values = sorted({point.date for points in price_index.values() for point in points})
    return values


def rebalance_dates(price_dates: list[date], start_date: date, end_date: date, frequency: str) -> list[date]:
    by_month: dict[str, date] = {}
    for value in price_dates:
        if start_date <= value <= end_date:
            by_month[value.strftime("%Y-%m")] = value
    dates = [by_month[key] for key in sorted(by_month)]
    if frequency == "quarterly":
        dates = [value for value in dates if value.month in {3, 6, 9, 12}]
    return dates


def latest_price(points: list[PricePoint], as_of: date) -> PricePoint | None:
    latest: PricePoint | None = None
    for point in points:
        if point.date > as_of:
            break
        latest = point
    return latest


def price_at(price_index: dict[str, list[PricePoint]], code: str, as_of: date) -> PricePoint | None:
    return latest_price(price_index.get(code, []), as_of)


def price_on_date(price_index: dict[str, list[PricePoint]], code: str, value: date) -> PricePoint | None:
    for point in price_index.get(code, []):
        if point.date == value:
            return point
        if point.date > value:
            return None
    return None


def terminal_before(price_index: dict[str, list[PricePoint]], code: str, as_of: date) -> PricePoint | None:
    points = price_index.get(code, [])
    if points and points[-1].date < as_of:
        return points[-1]
    return None


def build_delisting_index(listing_rows: list[dict[str, str]]) -> dict[str, date]:
    values: dict[str, date] = {}
    for row in listing_rows:
        code = (row.get("code") or "").strip()
        if not code:
            continue
        delisted_date = parse_date(row.get("delisted_date"), field_name="listings.delisted_date")
        if delisted_date is not None:
            values[code] = delisted_date
    return values


def next_price(points: list[PricePoint], after_date: date) -> PricePoint | None:
    for point in points:
        if point.date > after_date:
            return point
    return None


def execution_point(price_index: dict[str, list[PricePoint]], code: str, signal_date: date, mode: str) -> PricePoint | None:
    if mode == "rebalance_close":
        return price_on_date(price_index, code, signal_date)
    return next_price(price_index.get(code, []), signal_date)


def execution_price(point: PricePoint, mode: str) -> float:
    if mode == "next_open":
        return point.unadjusted_open
    return point.unadjusted_close


def adjustment_ratio(point: PricePoint) -> float:
    if point.adjusted_close <= 0:
        return 1.0
    return point.unadjusted_close / point.adjusted_close


def adjusted_shares_for_trade(actual_shares: float, point: PricePoint) -> float:
    return actual_shares * adjustment_ratio(point)


def actual_shares_from_adjusted(adjusted_shares: float, point: PricePoint) -> float:
    ratio = adjustment_ratio(point)
    if ratio <= 0:
        return adjusted_shares
    return adjusted_shares / ratio


def position_value(adjusted_shares: float, point: PricePoint) -> float:
    return adjusted_shares * point.adjusted_close


def display_shares(value: float) -> float | int:
    rounded = round(value)
    if abs(value - rounded) < 1e-6:
        return int(rounded)
    return value


def snapshot_only_listings(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    if any((row.get("listing_lifecycle_status") or "").strip() == "snapshot_only_missing_lifecycle_dates" for row in rows):
        return True
    return not any((row.get("listed_date") or "").strip() for row in rows)


def lifecycle_data_status(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "unknown"
    lifecycle_markers = {(row.get("listing_lifecycle_status") or "").strip() for row in rows}
    if "pit_snapshot_panel_missing_lifecycle_dates" in lifecycle_markers:
        return "pit_snapshot_panel"
    if snapshot_only_listings(rows):
        return "snapshot_only"
    listed_dates = [(row.get("listed_date") or "").strip() for row in rows]
    if any(not value for value in listed_dates):
        return "partial_lifecycle"
    if not any((row.get("delisted_date") or "").strip() for row in rows):
        return "pit_no_delistings_observed"
    return "pit_with_delistings"


def performance_conclusion_allowed(status: str) -> bool:
    return status == "pit_with_delistings"


def missing_price_tail_policy(config: dict[str, Any]) -> tuple[str, int]:
    policy = config.get("missing_price_tail_policy", {}) or {}
    mode = str(policy.get("mode", "warn_only") or "warn_only").strip()
    allowed_modes = {"warn_only", "freeze_last_price", "assume_zero_after_n_trading_days"}
    if mode not in allowed_modes:
        raise ValueError(f"Unsupported missing_price_tail_policy.mode: {mode}")
    max_stale_days = parse_int(policy.get("max_stale_trading_days"), default=5)
    if max_stale_days is None or max_stale_days < 0:
        raise ValueError("missing_price_tail_policy.max_stale_trading_days must be non-negative.")
    return mode, max_stale_days


def trading_staleness_days(calendar: list[date], last_price_date: date, as_of: date) -> int:
    return sum(1 for value in calendar if last_price_date < value <= as_of)


def adjusted_return(
    price_index: dict[str, list[PricePoint]],
    code: str,
    start: date,
    end: date,
    delisting_dates: dict[str, date] | None = None,
) -> float | None:
    start_point = price_at(price_index, code, start)
    if not start_point or start_point.adjusted_close <= 0:
        return None
    delisted_date = (delisting_dates or {}).get(code)
    if delisted_date is not None and start < delisted_date <= end:
        return -1.0
    end_point = price_at(price_index, code, end)
    if not end_point:
        return None
    return end_point.adjusted_close / start_point.adjusted_close - 1.0


def mean_return(
    price_index: dict[str, list[PricePoint]],
    codes: list[str],
    start: date,
    end: date,
    delisting_dates: dict[str, date] | None = None,
) -> float | None:
    returns = [adjusted_return(price_index, code, start, end, delisting_dates) for code in codes]
    clean = [value for value in returns if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def tick_size(price: float) -> float:
    if price < 1000:
        return 0.1
    if price < 3000:
        return 0.5
    if price < 30000:
        return 1.0
    return 5.0


def cost_components(value: float, price: float, median_adv: float | None) -> tuple[float, float]:
    spread_cost = abs(value) * (tick_size(price) / price if price else 0)
    impact_cost = abs(value) * min(abs(value) / median_adv, 0.02) if median_adv else 0
    return spread_cost, impact_cost


def estimate_cost(value: float, price: float, median_adv: float | None, config: dict[str, Any], scenario: str) -> float:
    spread_cost, impact_cost = cost_components(value, price, median_adv)
    scenario_config = config["cost_model"]["scenarios"].get(scenario, {})
    spread_multiplier = float(scenario_config.get("spread_multiplier", 1.0))
    impact_multiplier = float(scenario_config.get("impact_multiplier", 1.0))
    return spread_multiplier * spread_cost + impact_multiplier * impact_cost


def floor_lot(value: float, price: float, lot: int) -> int:
    if price <= 0 or lot <= 0:
        return 0
    return int(value // (price * lot)) * lot


def max_drawdown(values: list[float]) -> float:
    peak = -math.inf
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = min(drawdown, value / peak - 1.0)
    return drawdown


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    if value is None:
        return ""
    return f"JPY {value:,.0f}"


def select_codes(
    scores: list[dict[str, str]],
    holdings: dict[str, float],
    config: dict[str, Any],
) -> tuple[list[str], list[str]]:
    ranked = sorted(
        [row for row in scores if parse_int(row.get("rank")) is not None],
        key=lambda row: parse_int(row.get("rank"), default=999999) or 999999,
    )
    rank_by_code = {row["code"]: parse_int(row.get("rank"), default=999999) or 999999 for row in ranked}
    universe_count = len(ranked)
    if not ranked:
        return [], []

    executable_config = config["portfolio"].get("executable_portfolio", {})
    target_count = min(int(executable_config.get("target_holdings_max", 30)), universe_count)
    target_count = max(min(int(executable_config.get("target_holdings_min", 1)), universe_count), target_count)

    buy_rule = config["portfolio"].get("buy_rule", {})
    hold_rule = config["portfolio"].get("hold_rule", {})
    buy_limit = rank_rule_limit(universe_count, buy_rule, default_pct=10.0, default_n=50)
    hold_limit = rank_rule_limit(universe_count, hold_rule, default_pct=20.0, default_n=100)

    kept = [code for code, shares in holdings.items() if shares > 0 and rank_by_code.get(code, 999999) <= hold_limit]
    kept.sort(key=lambda code: rank_by_code.get(code, 999999))
    selected = list(kept[:target_count])
    selected_set = set(selected)

    for row in ranked[:buy_limit]:
        code = row["code"]
        if code in selected_set:
            continue
        selected.append(code)
        selected_set.add(code)
        if len(selected) >= target_count:
            break

    research_codes = [row["code"] for row in ranked[:target_count]]
    return selected, research_codes


def rank_rule_limit(
    universe_count: int,
    rule: dict[str, Any],
    *,
    default_pct: float,
    default_n: int,
) -> int:
    pct_limit = math.ceil(universe_count * float(rule.get("rank_top_pct", default_pct)) / 100)
    n_limit = int(rule.get("rank_top_n", default_n))
    return min(universe_count, max(0, pct_limit, n_limit))


def build_targets(
    selected_codes: list[str],
    universe_by_code: dict[str, dict[str, str]],
    price_index: dict[str, list[PricePoint]],
    rebalance_date: date,
    equity: float,
) -> dict[str, int]:
    if not selected_codes:
        return {}
    target_value = equity / len(selected_codes)
    targets: dict[str, int] = {}
    for code in selected_codes:
        universe = universe_by_code.get(code, {})
        point = price_on_date(price_index, code, rebalance_date)
        if not point:
            targets[code] = 0
            continue
        lot = parse_int(universe.get("lot_size"), default=100) or 100
        targets[code] = floor_lot(target_value, point.unadjusted_close, lot)
    return targets


def consume_lots(lots: list[dict[str, float]], adjusted_shares_to_sell: float) -> float:
    remaining = adjusted_shares_to_sell
    basis = 0.0
    while remaining > 1e-9 and lots:
        lot = lots[0]
        take = min(remaining, float(lot["adjusted_shares"]))
        basis += take * float(lot["basis_per_adjusted_share"])
        lot["adjusted_shares"] -= take
        remaining -= take
        if lot["adjusted_shares"] <= 1e-9:
            lots.pop(0)
    return basis


def remaining_basis(lots: list[dict[str, float]]) -> float:
    return sum(float(lot["adjusted_shares"]) * float(lot["basis_per_adjusted_share"]) for lot in lots)


def add_reason(current: str, value: str) -> str:
    if not current:
        return value
    parts = current.split(";")
    if value in parts:
        return current
    return f"{current};{value}"


UNIVERSE_CACHE_FIELDS = [
    "rebalance_date",
    "code",
    "name",
    "market",
    "sector",
    "listed_date",
    "delisted_date",
    "security_type",
    "lot_size",
    "ipo_age_trading_days",
    "median_60d_trading_value",
    "latest_price_date",
    "latest_unadjusted_close",
    "rebalance_price_available",
    "latest_price_stale",
    "price_staleness_trading_days",
    "has_fundamentals",
    "tradable_flag",
    "price_limit_flag",
]
EXCLUSION_CACHE_FIELDS = ["rebalance_date", "code", "name", "reason", "detail"]
CANDIDATE_CACHE_FIELDS = [
    "rebalance_date",
    "code",
    "rank",
    "selected_flag",
    "research_flag",
    "target_shares",
    "latest_unadjusted_close",
]


def score_cache_fields(raw_factors: list[str]) -> list[str]:
    return [
        "rebalance_date",
        "rank",
        "code",
        "name",
        "sector",
        "latest_unadjusted_close",
        "quality_score",
        "value_score",
        "momentum_score",
        "composite_score",
        "qvm_score",
        "filter_status",
        "filter_reasons",
        "missing_score_components",
        *[f"{factor}_z" for factor in raw_factors],
    ]


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.rebalance:
        args.frequency = args.rebalance
    if args.cache_dir is None and args.cache_format is not None:
        args.cache_dir = Path("data/processed/cache")
    if args.cache_dir is not None and args.cache_format is None:
        args.cache_format = "parquet"
    return args


def apply_config_overrides(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    updated = deepcopy(config)
    if args.target_holdings is not None:
        executable = updated.setdefault("portfolio", {}).setdefault("executable_portfolio", {})
        executable["target_holdings_min"] = args.target_holdings
        executable["target_holdings_max"] = args.target_holdings
    if args.adv_cap is not None:
        updated.setdefault("execution", {})["max_order_to_median_trading_value"] = args.adv_cap
    return updated


def cache_enabled(args: argparse.Namespace) -> bool:
    return args.cache_dir is not None


def cache_digest(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def cache_config(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: config.get(key, {}) for key in keys}


def source_checksum(name: str) -> str:
    return checksum(Path(__file__).resolve().parent / name)


def compute_cache_fingerprints(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, str]:
    market_benchmark_prices = getattr(args, "market_benchmark_prices", None)
    market_benchmark_id = getattr(args, "market_benchmark_id", None)
    market_benchmark_checksum = checksum(market_benchmark_prices) if market_benchmark_prices else ""
    inputs_payload = {
        "schema_version": "walkforward_inputs_cache_v0_1",
        "inputs": {
            "listings": checksum(args.listings),
            "prices": checksum(args.prices),
            "fundamentals": checksum(args.fundamentals),
        },
    }
    inputs_fingerprint = cache_digest(inputs_payload)
    universe_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_universe_cache_v0_1",
            "inputs": inputs_fingerprint,
            "config": cache_config(config, "scope", "universe"),
            "source": {
                "build_universe.py": source_checksum("build_universe.py"),
                "research_common.py": source_checksum("research_common.py"),
            },
        }
    )
    factors_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_factors_cache_v0_2",
            "inputs": inputs_fingerprint,
            "universe": universe_fingerprint,
            "factor_engine": {
                "return_12_1": {"lookback_days": 252, "skip_days": 21},
                "return_6_1": {"lookback_days": 126, "skip_days": 21},
            },
            "factor_definitions": factor_definition_names(config),
            "factor_definition_fingerprints": factor_definition_fingerprints(config, functions={"ts_return"}),
            "factor_definition_dependency_graph": factor_definition_dependency_graph(
                config,
                functions={"ts_return"},
                validate_unknown=False,
            ),
            "factor_definition_config": ((config.get("factors", {}) or {}).get("definitions", []) or []),
            "source": {
                "build_factors.py": source_checksum("build_factors.py"),
                "factor_expressions.py": source_checksum("factor_expressions.py"),
                "research_common.py": source_checksum("research_common.py"),
            },
        }
    )
    scores_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_scores_cache_v0_1",
            "factors": factors_fingerprint,
            "strategy_version": args.strategy_version,
            "config": cache_config(config, "strategy", "factors"),
            "source": {
                "build_scores.py": source_checksum("build_scores.py"),
                "factor_expressions.py": source_checksum("factor_expressions.py"),
                "research_common.py": source_checksum("research_common.py"),
            },
        }
    )
    run_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_run_cache_v0_1",
            "scores": scores_fingerprint,
            "date_range": {"start": args.start_date, "end": args.end_date},
            "frequency": args.frequency,
            "portfolio": cache_config(config, "portfolio", "execution", "cost_model", "tax", "missing_price_tail_policy"),
            "execution_price": args.execution_price,
            "cost_scenario": args.cost_scenario,
            "capital_jpy": args.capital_jpy,
            "tax_rate": args.tax_rate,
            "market_benchmark_prices": market_benchmark_checksum,
            "market_benchmark_id": market_benchmark_id or "",
            "source": {
                "run_qvm_walkforward.py": source_checksum("run_qvm_walkforward.py"),
                "research_common.py": source_checksum("research_common.py"),
            },
        }
    )
    return {
        "inputs": inputs_fingerprint,
        "universe": universe_fingerprint,
        "factors": factors_fingerprint,
        "scores": scores_fingerprint,
        "run": run_fingerprint,
    }


def compute_cache_fingerprint(args: argparse.Namespace, config: dict[str, Any]) -> str:
    return compute_cache_fingerprints(args, config)["run"]


def cache_fingerprint(args: argparse.Namespace, layer: str) -> str:
    fingerprints = getattr(args, "_cache_fingerprints", {})
    if layer in fingerprints:
        return fingerprints[layer]
    return getattr(args, "_cache_fingerprint", "unfingerprinted")


def cache_path(args: argparse.Namespace, category: str, name: str, *, layer: str | None = None) -> Path:
    if args.cache_dir is None:
        raise ValueError("Cache path requested while cache is disabled")
    fingerprint = cache_fingerprint(args, layer or category)
    return args.cache_dir / category / fingerprint / f"{name}.{args.cache_format or 'parquet'}"


def cache_manifest_fingerprint(args: argparse.Namespace) -> str:
    return cache_fingerprint(args, "run")


def strategy_cache_token(args: argparse.Namespace) -> str:
    return args.strategy_version.replace("-", "_")


def parameter_cache_token(args: argparse.Namespace) -> str:
    target = f"target{args.target_holdings}" if args.target_holdings is not None else "target_config"
    adv = f"adv{str(args.adv_cap).replace('.', 'p')}" if args.adv_cap is not None else "adv_config"
    return f"{strategy_cache_token(args)}_{target}_{adv}"


def token_value(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    return (
        text.replace("-", "m")
        .replace(".", "p")
        .replace(":", "")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )


def run_dependent_candidate_token(args: argparse.Namespace) -> str:
    start = token_value(args.start_date.replace("-", ""))
    end = token_value(args.end_date.replace("-", ""))
    capital = token_value(args.capital_jpy)
    tax = token_value(args.tax_rate)
    return (
        f"{parameter_cache_token(args)}_{start}_{end}_{args.execution_price}_"
        f"{args.cost_scenario}_capital{capital}_tax{tax}"
    )


def read_or_build_input_cache(args: argparse.Namespace, source_path: Path, name: str) -> tuple[list[dict[str, str]], Path]:
    if not cache_enabled(args):
        return read_csv(source_path), source_path

    output_path = cache_path(args, "inputs", name)
    if output_path.exists() and not args.force_rebuild:
        return read_csv(output_path), output_path

    rows = read_csv(source_path)
    write_table(rows, output_path, format=args.cache_format or "parquet")
    return rows, output_path


def run_cached_stages(args: argparse.Namespace, rebalance_date: date) -> tuple[Path, Path, Path]:
    suffix = month_key(rebalance_date)
    strategy_token = strategy_cache_token(args)
    universe_path = cache_path(args, "universe", f"universe_{suffix}")
    exclusions_path = cache_path(args, "universe", f"excluded_{suffix}")
    factors_path = cache_path(args, "factors", f"factors_{suffix}")
    scores_path = cache_path(args, "scores", f"scores_{suffix}_{strategy_token}")

    config = args._config if hasattr(args, "_config") else load_yaml(args.config)
    listing_rows = args._listing_rows if hasattr(args, "_listing_rows") else read_csv(args.listings)
    price_rows = args._price_rows if hasattr(args, "_price_rows") else read_csv(args.prices)
    fundamental_rows = (
        args._fundamental_rows if hasattr(args, "_fundamental_rows") else read_csv(args.fundamentals)
    )

    if universe_path.exists() and not args.force_rebuild:
        universe_rows = read_csv(universe_path)
    else:
        universe_rows, exclusion_rows = build_universe_from_rows(
            config=config,
            rebalance_date=rebalance_date,
            listing_rows=listing_rows,
            price_rows=price_rows,
            fundamental_rows=fundamental_rows,
        )
        write_table(universe_rows, universe_path, format=args.cache_format or "parquet", fieldnames=UNIVERSE_CACHE_FIELDS)
        write_table(
            exclusion_rows,
            exclusions_path,
            format=args.cache_format or "parquet",
            fieldnames=EXCLUSION_CACHE_FIELDS,
        )

    if factors_path.exists() and not args.force_rebuild:
        factor_rows = read_csv(factors_path)
    else:
        factor_rows = build_factors(
            rebalance_date=rebalance_date,
            universe_rows=universe_rows,
            price_rows=price_rows,
            fundamental_rows=fundamental_rows,
            config=config,
        )
        write_table(factor_rows, factors_path, format=args.cache_format or "parquet", fieldnames=factor_output_fields(config))

    if scores_path.exists() and not args.force_rebuild:
        score_rows = read_csv(scores_path)
    else:
        score_rows, raw_factors = build_scores(
            config=config,
            factor_rows=factor_rows,
            strategy_version=args.strategy_version,
        )
        for row in score_rows:
            row["rebalance_date"] = row.get("rebalance_date") or rebalance_date
        write_table(
            score_rows,
            scores_path,
            format=args.cache_format or "parquet",
            fieldnames=score_cache_fields(raw_factors),
        )

    stage_rows = getattr(args, "_stage_rows", {})
    stage_rows[suffix] = {"universe": universe_rows, "scores": score_rows}
    args._stage_rows = stage_rows
    return universe_path, factors_path, scores_path


def write_rebalance_candidates_cache(
    args: argparse.Namespace,
    *,
    rebalance_date: date,
    scores: list[dict[str, str]],
    selected_codes: list[str],
    research_codes: list[str],
    targets: dict[str, int],
) -> None:
    if not cache_enabled(args):
        return
    selected_set = set(selected_codes)
    research_set = set(research_codes)
    rows = []
    for row in scores:
        code = row.get("code", "")
        if not code:
            continue
        if code not in selected_set and code not in research_set:
            continue
        rows.append(
            {
                "rebalance_date": rebalance_date,
                "code": code,
                "rank": row.get("rank", ""),
                "selected_flag": str(code in selected_set).lower(),
                "research_flag": str(code in research_set).lower(),
                "target_shares": targets.get(code, ""),
                "latest_unadjusted_close": row.get("latest_unadjusted_close", ""),
            }
        )
    suffix = month_key(rebalance_date)
    output_path = cache_path(
        args,
        "rebalance_candidates",
        f"rebalance_candidates_{suffix}_{run_dependent_candidate_token(args)}",
        layer="run",
    )
    write_table(rows, output_path, format=args.cache_format or "parquet", fieldnames=CANDIDATE_CACHE_FIELDS)


def run_stages(args: argparse.Namespace, rebalance_date: date) -> tuple[Path, Path, Path]:
    if cache_enabled(args):
        return run_cached_stages(args, rebalance_date)

    suffix = month_key(rebalance_date)
    py = sys.executable
    common_manifest_flag = ["--no-manifest"] if args.skip_stage_manifest else []
    run(
        [
            py,
            "scripts/build_universe.py",
            "--config",
            str(args.config),
            "--rebalance-date",
            rebalance_date.isoformat(),
            "--listings",
            str(args.listings),
            "--prices",
            str(args.prices),
            "--fundamentals",
            str(args.fundamentals),
            *common_manifest_flag,
        ]
    )
    universe_path = Path(f"data/processed/universe/universe_{suffix}.csv")
    run(
        [
            py,
            "scripts/build_factors.py",
            "--config",
            str(args.config),
            "--rebalance-date",
            rebalance_date.isoformat(),
            "--universe",
            str(universe_path),
            "--prices",
            str(args.prices),
            "--fundamentals",
            str(args.fundamentals),
            *common_manifest_flag,
        ]
    )
    factors_path = Path(f"data/processed/factors/factors_{suffix}.csv")
    run(
        [
            py,
            "scripts/build_scores.py",
            "--config",
            str(args.config),
            "--rebalance-date",
            rebalance_date.isoformat(),
            "--factors",
            str(factors_path),
            "--strategy-version",
            args.strategy_version,
            *common_manifest_flag,
        ]
    )
    scores_path = Path(f"data/processed/scores/scores_{suffix}.csv")
    return universe_path, factors_path, scores_path


def write_report(path: Path, summary_rows: list[dict[str, Any]], initial_capital: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not summary_rows:
        path.write_text("# QVM Walk-Forward Report\n\nNo rows.\n", encoding="utf-8")
        return
    final = summary_rows[-1]
    first = summary_rows[0]
    portfolio_values = [float(row["portfolio_equity_after_cost"]) for row in summary_rows]
    taxable_values = [float(row["after_tax_taxable_equity"]) for row in summary_rows]
    benchmark_values = [float(row["benchmark_equity"]) for row in summary_rows]
    research_values = [float(row["research_equity"]) for row in summary_rows]
    market_values = [
        value
        for value in (parse_float(row.get("market_benchmark_equity")) for row in summary_rows)
        if value is not None
    ]
    avg_cash_pct = sum(float(row["cash_pct"]) for row in summary_rows) / len(summary_rows)
    avg_turnover = sum(float(row["turnover"]) for row in summary_rows) / len(summary_rows)
    avg_holdings = sum(float(row["holdings_count"]) for row in summary_rows) / len(summary_rows)
    avg_zero_lot = sum(float(row["zero_lot_targets"]) for row in summary_rows) / len(summary_rows)
    avg_skipped = sum(float(row["skipped_orders"]) for row in summary_rows) / len(summary_rows)
    total_cost = sum(float(row["estimated_cost_base"]) for row in summary_rows)
    lines = [
        f"# QVM Walk-Forward Report {summary_rows[0]['rebalance_date']}..{final['rebalance_date']}",
        "",
    ]
    if str(final.get("performance_conclusion_allowed", "")).lower() != "true":
        lines.extend(
            [
                "## Lifecycle Warning",
                "",
                "NOT VALID FOR PERFORMANCE CONCLUSION: listing lifecycle coverage is not point-in-time complete.",
                "",
            ]
        )
    lines.extend(
        [
            "## Parameters",
            "",
            "| parameter | value |",
            "|---|---:|",
            f"| strategy version | {first.get('strategy_version', '')} |",
            f"| frequency | {first.get('frequency', '')} |",
            f"| execution price | {first.get('execution_price', '')} |",
            f"| cost scenario | {first.get('cost_scenario', '')} |",
            f"| capital JPY | {money(float(first.get('capital_jpy', initial_capital)))} |",
            f"| target holdings | {first.get('target_holdings', '')} |",
            f"| ADV cap | {first.get('adv_cap', '')} |",
            f"| tax rate | {first.get('tax_rate', '')} |",
            f"| cache fingerprint | {first.get('cache_fingerprint', '')} |",
            f"| lifecycle data status | {first.get('lifecycle_data_status', '')} |",
            f"| performance conclusion allowed | {first.get('performance_conclusion_allowed', '')} |",
            f"| strict rebalance price filter | {first.get('strict_rebalance_price_filter', '')} |",
            f"| missing price tail policy | {first.get('missing_price_tail_policy', '')} |",
            f"| missing price tail max stale days | {first.get('missing_price_tail_max_stale_days', '')} |",
            f"| market benchmark | {first.get('market_benchmark_id', '')} |",
            "",
        ]
    )
    lines.extend(
        [
        "## Summary",
        "",
        f"- months: {len(summary_rows)}",
        f"- portfolio return after cost: {pct(float(final['portfolio_equity_after_cost']) / initial_capital - 1)}",
        f"- after-tax taxable return: {pct(float(final['after_tax_taxable_equity']) / initial_capital - 1)}",
        f"- filtered-universe benchmark return: {pct(float(final['benchmark_equity']) / initial_capital - 1)}",
        f"- theoretical research basket return: {pct(float(final['research_equity']) / initial_capital - 1)}",
        *(
            [f"- market benchmark return: {pct(float(final['market_benchmark_equity']) / initial_capital - 1)}"]
            if final.get("market_benchmark_id")
            else []
        ),
        f"- portfolio max drawdown: {pct(max_drawdown(portfolio_values))}",
        f"- after-tax taxable max drawdown: {pct(max_drawdown(taxable_values))}",
        f"- benchmark max drawdown: {pct(max_drawdown(benchmark_values))}",
        f"- research basket max drawdown: {pct(max_drawdown(research_values))}",
        *(
            [f"- market benchmark max drawdown: {pct(max_drawdown(market_values))}"]
            if final.get("market_benchmark_id")
            else []
        ),
        f"- average holdings: {avg_holdings:.1f}",
        f"- average zero-lot targets: {avg_zero_lot:.1f}",
        f"- average cash: {pct(avg_cash_pct)}",
        f"- average turnover: {pct(avg_turnover)}",
        f"- average skipped orders: {avg_skipped:.1f}",
        f"- total estimated base cost: {money(total_cost)}",
        f"- final optimistic/base/pessimistic equity: {money(float(final['portfolio_equity_optimistic']))} / {money(float(final['portfolio_equity_base']))} / {money(float(final['portfolio_equity_pessimistic']))}",
        f"- cumulative realized gain: {money(float(final['cumulative_realized_gain']))}",
        f"- cumulative taxable tax: {money(float(final['cumulative_tax']))}",
        "",
        "## Final Month",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| date | {final['rebalance_date']} |",
        f"| universe count | {final['universe_count']} |",
        f"| selected count | {final['selected_count']} |",
        f"| zero-lot targets | {final['zero_lot_targets']} |",
        f"| holdings count | {final['holdings_count']} |",
        f"| cash | {money(float(final['cash']))} |",
        f"| cash pct | {pct(float(final['cash_pct']))} |",
        f"| after-tax taxable equity | {money(float(final['after_tax_taxable_equity']))} |",
        f"| buys | {final['buy_trades']} |",
        f"| sells | {final['sell_trades']} |",
        f"| skipped orders | {final['skipped_orders']} |",
        "",
        "## Caveat",
        "",
        "This is an engineering walk-forward run. It supports execution timing and rough FIFO realized-tax accounting, but still uses simplified fills, costs, and tax treatment.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = normalize_args(build_parser().parse_args())
    config = apply_config_overrides(load_yaml(args.config), args)
    args._config = config
    if cache_enabled(args):
        args._cache_fingerprints = compute_cache_fingerprints(args, config)
        args._cache_fingerprint = args._cache_fingerprints["run"]
    start_date = parse_date(args.start_date, field_name="start_date")
    end_date = parse_date(args.end_date, field_name="end_date")
    if start_date is None or end_date is None:
        raise ValueError("start-date and end-date are required")

    listing_rows_for_check, listings_path = read_or_build_input_cache(args, args.listings, "processed_listings")
    price_rows, prices_path = read_or_build_input_cache(args, args.prices, "processed_prices")
    fundamental_rows, fundamentals_path = read_or_build_input_cache(args, args.fundamentals, "processed_fundamentals")
    args._listing_rows = listing_rows_for_check
    args._price_rows = price_rows
    args._fundamental_rows = fundamental_rows
    args.listings = listings_path
    args.prices = prices_path
    args.fundamentals = fundamentals_path

    lifecycle_status = lifecycle_data_status(listing_rows_for_check)
    conclusion_allowed = performance_conclusion_allowed(lifecycle_status)
    if lifecycle_status == "snapshot_only" and not args.allow_snapshot_listings:
        raise ValueError(
            "Listings look snapshot-only: listed_date is missing or listing_lifecycle_status marks missing lifecycle dates. "
            "This creates survivorship bias in historical walk-forward runs. Provide PIT lifecycle listings or pass "
            "--allow-snapshot-listings for exploratory samples only."
        )

    delisting_dates = build_delisting_index(listing_rows_for_check)
    price_index = build_price_index(price_rows)
    price_calendar = all_price_dates(price_index)
    dates = rebalance_dates(price_calendar, start_date, end_date, args.frequency)
    if not dates:
        raise ValueError("No rebalance dates found in price file for the requested window.")
    market_benchmark_label = ""
    market_benchmark_points: list[MarketBenchmarkPoint] = []
    if args.market_benchmark_prices:
        market_benchmark_label, market_benchmark_points = build_market_benchmark_series(
            read_csv(args.market_benchmark_prices),
            args.market_benchmark_id,
        )

    max_order_to_adv = float(config["execution"].get("max_order_to_median_trading_value", 0.005))
    tail_gap_mode, tail_gap_max_stale_days = missing_price_tail_policy(config)
    # Holdings and tax lots are tracked in adjusted-share units so split events
    # do not mechanically distort portfolio equity. Order sizing still uses
    # actual unadjusted shares and prices.
    holdings: dict[str, float] = {}
    tax_lots: dict[str, list[dict[str, float]]] = defaultdict(list)
    cash = args.capital_jpy
    cumulative_realized_gain = 0.0
    cumulative_tax = 0.0
    cumulative_cost_by_scenario = {"optimistic": 0.0, "base": 0.0, "pessimistic": 0.0}
    previous_after_equity: float | None = None
    previous_date: date | None = None
    previous_benchmark_codes: list[str] = []
    previous_research_codes: list[str] = []
    benchmark_equity = args.capital_jpy
    research_equity = args.capital_jpy
    market_benchmark_equity = args.capital_jpy

    trade_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    holdings_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    warned_price_tail_gaps: set[tuple[str, date]] = set()

    for rebalance_date in dates:
        universe_path, _factors_path, scores_path = run_stages(args, rebalance_date)
        stage_rows = getattr(args, "_stage_rows", {}).get(month_key(rebalance_date), {})
        universe_rows = stage_rows.get("universe") or read_csv(universe_path)
        scores = stage_rows.get("scores") or read_csv(scores_path)
        universe_by_code = {row["code"]: row for row in universe_rows}

        if previous_date is not None:
            benchmark_return = mean_return(
                price_index,
                previous_benchmark_codes,
                previous_date,
                rebalance_date,
                delisting_dates,
            )
            research_return = mean_return(
                price_index,
                previous_research_codes,
                previous_date,
                rebalance_date,
                delisting_dates,
            )
            if benchmark_return is not None:
                benchmark_equity *= 1 + benchmark_return
            if research_return is not None:
                research_equity *= 1 + research_return
            market_period_return = (
                market_benchmark_return(market_benchmark_points, previous_date, rebalance_date)
                if market_benchmark_points
                else None
            )
            if market_period_return is not None:
                market_benchmark_equity *= 1 + market_period_return
        else:
            market_period_return = 0.0 if market_benchmark_points else None

        for code, adjusted_shares in list(holdings.items()):
            delisted_date = delisting_dates.get(code)
            terminal_point = price_at(price_index, code, delisted_date or rebalance_date)
            if delisted_date is None or delisted_date > rebalance_date:
                tail_point = terminal_before(price_index, code, rebalance_date)
                if tail_point is not None:
                    stale_days = trading_staleness_days(price_calendar, tail_point.date, rebalance_date)
                    if (code, tail_point.date) not in warned_price_tail_gaps:
                        warned_price_tail_gaps.add((code, tail_point.date))
                        failure_rows.append(
                            {
                                "date": rebalance_date,
                                "code": code,
                                "failure_type": "price_tail_gap",
                                "detail": (
                                    f"last_price_date={tail_point.date};stale_trading_days={stale_days};"
                                    f"policy={tail_gap_mode}; no delisted_date in listings"
                                ),
                                "value": position_value(adjusted_shares, tail_point),
                            }
                        )
                    if tail_gap_mode == "assume_zero_after_n_trading_days" and stale_days >= tail_gap_max_stale_days:
                        actual_shares = actual_shares_from_adjusted(adjusted_shares, tail_point)
                        basis = remaining_basis(tax_lots.get(code, []))
                        cumulative_realized_gain -= basis
                        holdings.pop(code, None)
                        tax_lots.pop(code, None)
                        failure_rows.append(
                            {
                                "date": rebalance_date,
                                "code": code,
                                "failure_type": "assumed_tail_gap_zero",
                                "detail": (
                                    f"last_price_date={tail_point.date};stale_trading_days={stale_days};"
                                    f"max_stale_trading_days={tail_gap_max_stale_days}"
                                ),
                                "value": 0,
                            }
                        )
                        trade_rows.append(
                            {
                                "signal_date": rebalance_date,
                                "execution_date": rebalance_date,
                                "code": code,
                                "side": "TAIL_GAP_ZERO",
                                "requested_shares": -display_shares(actual_shares),
                                "filled_shares": -display_shares(actual_shares),
                                "price": 0,
                                "value": 0,
                                "estimated_cost_optimistic": 0,
                                "estimated_cost_base": 0,
                                "estimated_cost_pessimistic": 0,
                                "selected_cost": 0,
                                "realized_gain": -basis,
                                "estimated_tax": 0,
                                "constraint_reason": "assumed_tail_gap_zero",
                            }
                        )
                continue
            actual_shares = actual_shares_from_adjusted(adjusted_shares, terminal_point) if terminal_point else adjusted_shares
            basis = remaining_basis(tax_lots.get(code, []))
            cumulative_realized_gain -= basis
            holdings.pop(code, None)
            tax_lots.pop(code, None)
            failure_rows.append(
                {
                    "date": rebalance_date,
                    "code": code,
                    "failure_type": "assumed_delisting_loss",
                    "detail": f"delisted_date={delisted_date}; recovery_price=0",
                    "value": 0,
                }
            )
            trade_rows.append(
                {
                    "signal_date": rebalance_date,
                    "execution_date": rebalance_date,
                    "code": code,
                    "side": "DELIST",
                    "requested_shares": -display_shares(actual_shares),
                    "filled_shares": -display_shares(actual_shares),
                    "price": 0,
                    "value": 0,
                    "estimated_cost_optimistic": 0,
                    "estimated_cost_base": 0,
                    "estimated_cost_pessimistic": 0,
                    "selected_cost": 0,
                    "realized_gain": -basis,
                    "estimated_tax": 0,
                    "constraint_reason": "assumed_delisting_loss",
                }
            )

        holdings_value = 0.0
        for code, adjusted_shares in list(holdings.items()):
            point = price_at(price_index, code, rebalance_date)
            if not point:
                continue
            holdings_value += position_value(adjusted_shares, point)
        pre_equity = cash + holdings_value

        selected_codes, research_codes = select_codes(scores, holdings, config)
        targets = build_targets(selected_codes, universe_by_code, price_index, rebalance_date, pre_equity)
        write_rebalance_candidates_cache(
            args,
            rebalance_date=rebalance_date,
            scores=scores,
            selected_codes=selected_codes,
            research_codes=research_codes,
            targets=targets,
        )
        zero_lot_targets = sum(1 for code in selected_codes if targets.get(code, 0) == 0)
        for code in selected_codes:
            if targets.get(code, 0) == 0:
                signal_point = price_on_date(price_index, code, rebalance_date)
                failure_rows.append(
                    {
                        "date": rebalance_date,
                        "code": code,
                        "failure_type": "zero_lot_target",
                        "detail": "target value cannot buy one lot",
                        "value": signal_point.unadjusted_close if signal_point else "",
                    }
                )
        all_codes = sorted(set(holdings) | set(targets))

        buy_trades = 0
        sell_trades = 0
        skipped_orders = 0
        turnover_value = 0.0
        estimated_cost_base = 0.0

        for code in all_codes:
            current_adjusted_shares = holdings.get(code, 0.0)
            target_shares = targets.get(code, 0)
            position_point = price_at(price_index, code, rebalance_date)
            signal_point = price_on_date(price_index, code, rebalance_date)
            fill_point = execution_point(price_index, code, rebalance_date, args.execution_price)
            current_shares = (
                int(round(actual_shares_from_adjusted(current_adjusted_shares, position_point)))
                if position_point
                else 0
            )
            desired_delta = target_shares - current_shares
            if desired_delta == 0:
                continue
            if not signal_point or not fill_point:
                skipped_orders += 1
                failure_reason = "missing_signal_price" if not signal_point else "missing_execution_price"
                failure_rows.append(
                    {
                        "date": rebalance_date,
                        "code": code,
                        "failure_type": failure_reason,
                        "detail": f"execution_price={args.execution_price}",
                        "value": 0,
                    }
                )
                trade_rows.append(
                    {
                        "signal_date": rebalance_date,
                        "execution_date": "",
                        "code": code,
                        "side": "SKIP",
                        "requested_shares": desired_delta,
                        "filled_shares": 0,
                        "price": "",
                        "value": 0,
                        "estimated_cost_optimistic": 0,
                        "estimated_cost_base": 0,
                        "estimated_cost_pessimistic": 0,
                        "selected_cost": 0,
                        "realized_gain": 0,
                        "estimated_tax": 0,
                        "constraint_reason": failure_reason,
                    }
                )
                continue

            universe = universe_by_code.get(code, {})
            lot = parse_int(universe.get("lot_size"), default=100) or 100
            median_adv = parse_float(universe.get("median_60d_trading_value"))
            fill_price = execution_price(fill_point, args.execution_price)
            requested_value = abs(desired_delta * fill_price)
            filled_delta = desired_delta
            reason = ""
            adv_cap_value = median_adv * max_order_to_adv if median_adv else None
            if adv_cap_value is not None and requested_value > adv_cap_value:
                filled_lots = int(adv_cap_value // (fill_price * lot)) * lot
                filled_delta = filled_lots if desired_delta > 0 else -filled_lots
                reason = "reduced_by_adv_cap"
                failure_rows.append(
                    {
                        "date": rebalance_date,
                        "code": code,
                        "failure_type": "adv_cap_reduction",
                        "detail": f"requested_shares={desired_delta};filled_shares={filled_delta}",
                        "value": requested_value,
                    }
                )
            if filled_delta == 0:
                reason = reason or "below_lot_size"
                failure_rows.append(
                    {
                        "date": rebalance_date,
                        "code": code,
                        "failure_type": reason,
                        "detail": f"requested_shares={desired_delta}",
                        "value": requested_value,
                    }
                )

            trade_value = abs(filled_delta * fill_price)
            scenario_costs = {
                scenario: estimate_cost(trade_value, fill_price, median_adv, config, scenario)
                for scenario in ["optimistic", "base", "pessimistic"]
            }
            cost = scenario_costs[args.cost_scenario]
            if filled_delta > 0 and trade_value + cost > cash:
                affordable_shares = floor_lot(max(cash, 0), fill_price, lot)
                filled_delta = min(filled_delta, affordable_shares)
                trade_value = abs(filled_delta * fill_price)
                scenario_costs = {
                    scenario: estimate_cost(trade_value, fill_price, median_adv, config, scenario)
                    for scenario in ["optimistic", "base", "pessimistic"]
                }
                cost = scenario_costs[args.cost_scenario]
                while filled_delta > 0 and trade_value + cost > cash:
                    filled_delta = max(0, filled_delta - lot)
                    trade_value = abs(filled_delta * fill_price)
                    scenario_costs = {
                        scenario: estimate_cost(trade_value, fill_price, median_adv, config, scenario)
                        for scenario in ["optimistic", "base", "pessimistic"]
                    }
                    cost = scenario_costs[args.cost_scenario]
                reason = add_reason(reason, "reduced_by_cash" if filled_delta else "insufficient_cash")
                failure_rows.append(
                    {
                        "date": rebalance_date,
                        "code": code,
                        "failure_type": reason,
                        "detail": f"cash={cash:.2f}",
                        "value": trade_value,
                    }
                )
            mark_price_limit = bool(config["execution"].get("mark_uncertain_fill_on_price_limit", False))
            if mark_price_limit and filled_delta != 0 and fill_point.price_limit_flag:
                reason = add_reason(reason, "price_limit_uncertain_fill")
                failure_rows.append(
                    {
                        "date": rebalance_date,
                        "code": code,
                        "failure_type": "price_limit_uncertain_fill",
                        "detail": f"execution_date={fill_point.date};execution_price={args.execution_price}",
                        "value": trade_value,
                    }
                )
            if filled_delta == 0:
                skipped_orders += 1

            side = "BUY" if filled_delta > 0 else "SELL" if filled_delta < 0 else "SKIP"
            realized_gain = 0.0
            estimated_tax = 0.0
            if filled_delta > 0:
                cash -= trade_value + cost
                if filled_delta:
                    adjusted_delta = adjusted_shares_for_trade(filled_delta, fill_point)
                    tax_lots[code].append(
                        {
                            "adjusted_shares": adjusted_delta,
                            "basis_per_adjusted_share": (trade_value + cost) / adjusted_delta,
                        }
                    )
                buy_trades += 1
            elif filled_delta < 0:
                cash += trade_value - cost
                adjusted_sold_shares = adjusted_shares_for_trade(abs(filled_delta), fill_point)
                basis = consume_lots(tax_lots[code], adjusted_sold_shares)
                realized_gain = trade_value - cost - basis
                if realized_gain > 0:
                    estimated_tax = realized_gain * args.tax_rate
                    cumulative_tax += estimated_tax
                cumulative_realized_gain += realized_gain
                sell_trades += 1
            holdings[code] = current_adjusted_shares + adjusted_shares_for_trade(filled_delta, fill_point)
            if abs(holdings.get(code, 0.0)) <= 1e-9:
                holdings.pop(code, None)
            turnover_value += trade_value
            for scenario, scenario_cost in scenario_costs.items():
                cumulative_cost_by_scenario[scenario] += scenario_cost
            estimated_cost_base += scenario_costs["base"]
            trade_rows.append(
                {
                    "signal_date": rebalance_date,
                    "execution_date": fill_point.date,
                    "code": code,
                    "side": side,
                    "requested_shares": desired_delta,
                    "filled_shares": filled_delta,
                    "price": fill_price,
                    "value": trade_value,
                    "estimated_cost_optimistic": scenario_costs["optimistic"],
                    "estimated_cost_base": scenario_costs["base"],
                    "estimated_cost_pessimistic": scenario_costs["pessimistic"],
                    "selected_cost": cost,
                    "realized_gain": realized_gain,
                    "estimated_tax": estimated_tax,
                    "constraint_reason": reason,
                }
            )

        post_holdings_value = 0.0
        for code, adjusted_shares in sorted(holdings.items()):
            point = price_at(price_index, code, rebalance_date)
            if not point:
                continue
            value = position_value(adjusted_shares, point)
            post_holdings_value += value
        after_equity = cash + post_holdings_value
        scenario_equity = {
            scenario: after_equity
            + cumulative_cost_by_scenario[args.cost_scenario]
            - cumulative_cost_by_scenario[scenario]
            for scenario in ["optimistic", "base", "pessimistic"]
        }
        after_tax_taxable_equity = after_equity - cumulative_tax
        for code, adjusted_shares in sorted(holdings.items()):
            point = price_at(price_index, code, rebalance_date)
            if not point:
                continue
            actual_shares = actual_shares_from_adjusted(adjusted_shares, point)
            value = position_value(adjusted_shares, point)
            holdings_rows.append(
                {
                    "date": rebalance_date,
                    "code": code,
                    "shares": display_shares(actual_shares),
                    "price": point.unadjusted_close,
                    "value": value,
                    "weight": value / after_equity if after_equity else 0,
                }
            )

        portfolio_return = 0.0 if previous_after_equity is None else after_equity / previous_after_equity - 1.0
        cash_pct = cash / after_equity if after_equity else 0
        if cash_pct > 0.2:
            failure_rows.append(
                {
                    "date": rebalance_date,
                    "code": "",
                    "failure_type": "cash_drag",
                    "detail": f"cash_pct={cash_pct:.4f}",
                    "value": cash,
                }
            )
        row = {
            "rebalance_date": rebalance_date,
            "strategy_version": args.strategy_version,
            "frequency": args.frequency,
            "execution_price": args.execution_price,
            "cost_scenario": args.cost_scenario,
            "capital_jpy": args.capital_jpy,
            "target_holdings": args.target_holdings or config["portfolio"]["executable_portfolio"].get("target_holdings_max", ""),
            "adv_cap": max_order_to_adv,
            "tax_rate": args.tax_rate,
            "cache_fingerprint": cache_manifest_fingerprint(args) if cache_enabled(args) else "",
            "lifecycle_data_status": lifecycle_status,
            "performance_conclusion_allowed": conclusion_allowed,
            "strict_rebalance_price_filter": config["universe"].get("strict_rebalance_price_filter", False),
            "missing_price_tail_policy": tail_gap_mode,
            "missing_price_tail_max_stale_days": tail_gap_max_stale_days,
            "universe_count": len(universe_rows),
            "selected_count": len(selected_codes),
            "zero_lot_targets": zero_lot_targets,
            "holdings_count": len(holdings),
            "portfolio_equity_pre": pre_equity,
            "portfolio_equity_after_cost": after_equity,
            "portfolio_equity_optimistic": scenario_equity["optimistic"],
            "portfolio_equity_base": scenario_equity["base"],
            "portfolio_equity_pessimistic": scenario_equity["pessimistic"],
            "after_tax_taxable_equity": after_tax_taxable_equity,
            "after_tax_nisa_like_equity": after_equity,
            "portfolio_return_after_cost": portfolio_return,
            "benchmark_equity": benchmark_equity,
            "research_equity": research_equity,
            "market_benchmark_id": market_benchmark_label,
            "market_benchmark_equity": market_benchmark_equity if market_benchmark_points else "",
            "market_benchmark_return": market_period_return if market_period_return is not None else "",
            "cash": cash,
            "cash_pct": cash_pct,
            "turnover": turnover_value / pre_equity if pre_equity else 0,
            "estimated_cost_base": estimated_cost_base,
            "cumulative_cost_optimistic": cumulative_cost_by_scenario["optimistic"],
            "cumulative_cost_base": cumulative_cost_by_scenario["base"],
            "cumulative_cost_pessimistic": cumulative_cost_by_scenario["pessimistic"],
            "cumulative_realized_gain": cumulative_realized_gain,
            "cumulative_tax": cumulative_tax,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "skipped_orders": skipped_orders,
        }
        summary_rows.append(row)
        equity_rows.append(
            {
                "date": rebalance_date,
                "portfolio_equity_after_cost": after_equity,
                "portfolio_equity_optimistic": scenario_equity["optimistic"],
                "portfolio_equity_base": scenario_equity["base"],
                "portfolio_equity_pessimistic": scenario_equity["pessimistic"],
                "after_tax_taxable_equity": after_tax_taxable_equity,
                "after_tax_nisa_like_equity": after_equity,
                "benchmark_equity": benchmark_equity,
                "research_equity": research_equity,
                "market_benchmark_id": market_benchmark_label,
                "market_benchmark_equity": market_benchmark_equity if market_benchmark_points else "",
                "market_benchmark_return": market_period_return if market_period_return is not None else "",
                "cash": cash,
            }
        )
        previous_after_equity = after_equity
        previous_date = rebalance_date
        previous_benchmark_codes = [row["code"] for row in universe_rows]
        previous_research_codes = research_codes

    start_suffix = month_key(dates[0])
    end_suffix = month_key(dates[-1])
    parameter_label = []
    if args.target_holdings is not None:
        parameter_label.append(f"target{args.target_holdings}")
    if args.adv_cap is not None:
        parameter_label.append(f"adv{str(args.adv_cap).replace('.', 'p')}")
    default_label_parts = [
        args.strategy_version,
        args.frequency,
        args.execution_price,
        args.cost_scenario,
        *parameter_label,
    ]
    label = args.run_label or "_".join(default_label_parts)
    token = f"{label}_{start_suffix}_{end_suffix}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.out_dir / f"qvm_walkforward_summary_{token}.csv"
    trades_path = args.out_dir / f"qvm_walkforward_trades_{token}.csv"
    holdings_path = args.out_dir / f"qvm_walkforward_holdings_{token}.csv"
    equity_path = args.out_dir / f"qvm_walkforward_equity_{token}.csv"
    failures_path = args.out_dir / f"qvm_walkforward_failure_cases_{token}.csv"
    report_path = args.report_dir / f"qvm_walkforward_{token}.md"

    write_csv(
        summary_path,
        summary_rows,
        [
            "rebalance_date",
            "strategy_version",
            "frequency",
            "execution_price",
            "cost_scenario",
            "capital_jpy",
            "target_holdings",
            "adv_cap",
            "tax_rate",
            "cache_fingerprint",
            "lifecycle_data_status",
            "performance_conclusion_allowed",
            "strict_rebalance_price_filter",
            "missing_price_tail_policy",
            "missing_price_tail_max_stale_days",
            "universe_count",
            "selected_count",
            "zero_lot_targets",
            "holdings_count",
            "portfolio_equity_pre",
            "portfolio_equity_after_cost",
            "portfolio_equity_optimistic",
            "portfolio_equity_base",
            "portfolio_equity_pessimistic",
            "after_tax_taxable_equity",
            "after_tax_nisa_like_equity",
            "portfolio_return_after_cost",
            "benchmark_equity",
            "research_equity",
            "market_benchmark_id",
            "market_benchmark_equity",
            "market_benchmark_return",
            "cash",
            "cash_pct",
            "turnover",
            "estimated_cost_base",
            "cumulative_cost_optimistic",
            "cumulative_cost_base",
            "cumulative_cost_pessimistic",
            "cumulative_realized_gain",
            "cumulative_tax",
            "buy_trades",
            "sell_trades",
            "skipped_orders",
        ],
    )
    write_csv(
        trades_path,
        trade_rows,
        [
            "signal_date",
            "execution_date",
            "code",
            "side",
            "requested_shares",
            "filled_shares",
            "price",
            "value",
            "estimated_cost_optimistic",
            "estimated_cost_base",
            "estimated_cost_pessimistic",
            "selected_cost",
            "realized_gain",
            "estimated_tax",
            "constraint_reason",
        ],
    )
    write_csv(holdings_path, holdings_rows, ["date", "code", "shares", "price", "value", "weight"])
    write_csv(
        equity_path,
        equity_rows,
        [
            "date",
            "portfolio_equity_after_cost",
            "portfolio_equity_optimistic",
            "portfolio_equity_base",
            "portfolio_equity_pessimistic",
            "after_tax_taxable_equity",
            "after_tax_nisa_like_equity",
            "benchmark_equity",
            "research_equity",
            "market_benchmark_id",
            "market_benchmark_equity",
            "market_benchmark_return",
            "cash",
        ],
    )
    write_csv(failures_path, failure_rows, ["date", "code", "failure_type", "detail", "value"])
    write_report(report_path, summary_rows, args.capital_jpy)

    if not args.no_manifest:
        date_range = f"{dates[0].isoformat()}..{dates[-1].isoformat()}"
        for source, path, schema, row_count in [
            ("derived_walkforward_summary", summary_path, "walkforward_summary_v0_1", len(summary_rows)),
            ("derived_walkforward_trades", trades_path, "walkforward_trades_v0_1", len(trade_rows)),
            ("derived_walkforward_holdings", holdings_path, "walkforward_holdings_v0_1", len(holdings_rows)),
            ("derived_walkforward_equity", equity_path, "walkforward_equity_v0_1", len(equity_rows)),
            ("derived_walkforward_failure_cases", failures_path, "walkforward_failure_cases_v0_1", len(failure_rows)),
            ("derived_walkforward_report", report_path, "walkforward_report_v0_1", 1),
        ]:
            append_manifest(
                args.manifest,
                source=source,
                file_path=path,
                vendor="local",
                schema_version=schema,
                date_range=date_range,
                notes=f"{row_count} rows",
            )

    print(f"Wrote walk-forward summary to {summary_path}")
    print(f"Wrote walk-forward failure cases to {failures_path}")
    print(f"Wrote walk-forward report to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
