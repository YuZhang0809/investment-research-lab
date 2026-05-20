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
from build_scores import STRATEGY_VERSION_CHOICES, build_scores, score_direct_fields, score_output_field
from build_universe import build_universe_from_rows
from external_factor_panels import external_factor_panel_fingerprints
from research_common import (
    append_manifest,
    checksum,
    load_yaml,
    month_key,
    parse_date,
    parse_bool,
    parse_float,
    parse_int,
    read_csv,
    median_or_none,
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


@dataclass(frozen=True)
class RawPriceStatus:
    date: date
    code: str
    tradable_flag: bool | None
    has_open: bool
    has_close: bool
    has_volume: bool
    has_trading_value: bool


@dataclass
class MarketBenchmarkPoint:
    date: date
    value: float


@dataclass
class SectorCapConfig:
    enabled: bool = False
    mode: str = "disabled"
    group_field: str = "sector"
    max_names_per_group: int | None = None
    max_sector_weight: float | None = None


@dataclass
class AffordableLotFilterConfig:
    enabled: bool = False
    max_single_lot_weight: float | None = None
    min_single_lot_weight: float | None = None
    cash_buffer_weight: float = 0.0


@dataclass
class ExecutionDiagnosticsConfig:
    enabled: bool = False
    high_cash_threshold: float = 0.30


@dataclass
class MarginConfig:
    enabled: bool = False
    account_type: str = "cash"
    target_gross_leverage: float = 1.0
    max_gross_leverage: float = 1.0
    annual_borrow_rate: float = 0.0
    initial_margin_requirement: float = 1.0
    maintenance_margin_requirement: float = 0.0
    minimum_required_equity: float = 0.0
    interest_day_count: int = 365
    margin_call_action: str = "flag_only"


@dataclass
class SectorCapBlockedCandidate:
    code: str
    group: str
    rank: int
    phase: str


@dataclass
class AffordabilityExcludedCandidate:
    code: str
    rank: int
    phase: str
    reason: str
    lot_size: int | None
    price: float | None
    single_lot_value: float | None
    single_lot_weight: float | None
    target_value: float | None


EXECUTION_PRICE_FAILURE_TYPES = {
    "missing_execution_price",
    "missing_execution_price_row",
    "execution_date_not_tradable",
    "execution_price_unavailable_on_execution_date",
}
SPECIFIC_EXECUTION_PRICE_FAILURE_TYPES = EXECUTION_PRICE_FAILURE_TYPES - {"missing_execution_price"}


@dataclass
class SelectionResult:
    selected_codes: list[str]
    research_codes: list[str]
    target_count: int
    sector_cap: SectorCapConfig
    affordable_lot_filter: AffordableLotFilterConfig
    blocked_candidates: list[SectorCapBlockedCandidate]
    affordability_excluded: list[AffordabilityExcludedCandidate]
    unfilled_slots: int
    selected_group_counts: dict[str, int]


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
    parser.add_argument("--sector-cap-mode", choices=["name_count", "target_weight"], help="Override portfolio sector cap mode.")
    parser.add_argument("--sector-cap-group-field", help="Override portfolio sector cap grouping field, default sector.")
    parser.add_argument("--max-names-per-sector", type=int, help="Override name-count sector cap limit.")
    parser.add_argument("--max-sector-weight", type=float, help="Override target-weight sector cap limit.")
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
    parser.add_argument("--margin-enabled", action="store_true", help="Enable generic long-margin accounting.")
    parser.add_argument("--target-gross-leverage", type=float, help="Override margin target gross leverage.")
    parser.add_argument("--max-gross-leverage", type=float, help="Override margin maximum gross leverage.")
    parser.add_argument("--annual-borrow-rate", type=float, help="Override annualized financing rate.")
    parser.add_argument("--initial-margin-requirement", type=float, help="Override initial margin requirement.")
    parser.add_argument("--maintenance-margin-requirement", type=float, help="Override maintenance margin requirement.")
    parser.add_argument("--minimum-required-equity", type=float, help="Override minimum required account equity.")
    parser.add_argument(
        "--margin-call-action",
        choices=["flag_only"],
        help="Margin call handling mode. Only flag_only is implemented.",
    )
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
        "--price-universe-panel",
        type=Path,
        help=(
            "Optional prebuilt DuckDB price/universe panel. When provided, the "
            "walk-forward uses its included/excluded rows for the universe stage "
            "and keeps the existing factor, score, and portfolio stages unchanged."
        ),
    )
    parser.add_argument(
        "--factor-score-panel",
        type=Path,
        help=(
            "Optional prebuilt factor/score panel. When provided, the walk-forward "
            "uses its included rows for universe, factor, and score stages while "
            "keeping portfolio construction and accounting unchanged."
        ),
    )
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


def validate_unique_key_rows(rows: list[dict[str, Any]], key_fields: list[str], label: str) -> None:
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(str(row.get(field, "")) for field in key_fields)
        if key in seen:
            details = ";".join(f"{field}={value}" for field, value in zip(key_fields, key))
            raise ValueError(f"Duplicate {label} rows for {details}.")
        seen.add(key)


def build_raw_price_status_index(price_rows: list[dict[str, str]]) -> dict[tuple[str, date], RawPriceStatus]:
    values: dict[tuple[str, date], RawPriceStatus] = {}
    for row in price_rows:
        code = (row.get("code") or "").strip()
        row_date = parse_date(row.get("date"), field_name="prices.date")
        if not code or row_date is None:
            continue
        values[(code, row_date)] = RawPriceStatus(
            date=row_date,
            code=code,
            tradable_flag=parse_bool(row.get("tradable_flag"), default=None),
            has_open=parse_float(row.get("unadjusted_open")) is not None,
            has_close=parse_float(row.get("unadjusted_close")) is not None,
            has_volume=parse_float(row.get("volume")) is not None,
            has_trading_value=parse_float(row.get("trading_value")) is not None,
        )
    return values


def all_raw_price_dates(price_rows: list[dict[str, str]]) -> list[date]:
    values: set[date] = set()
    for row in price_rows:
        row_date = parse_date(row.get("date"), field_name="prices.date")
        if row_date is not None:
            values.add(row_date)
    return sorted(values)


def build_price_index(price_rows: list[dict[str, str]]) -> dict[str, list[PricePoint]]:
    seen_price_keys: set[tuple[str, date]] = set()
    for row in price_rows:
        code = (row.get("code") or "").strip()
        row_date = parse_date(row.get("date"), field_name="prices.date")
        if not code or row_date is None:
            continue
        key = (code, row_date)
        if key in seen_price_keys:
            raise ValueError(f"Duplicate price rows for code={code};date={row_date}.")
        seen_price_keys.add(key)
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
            row_date = parse_date(row.get("date"), field_name="prices.date")
            unadjusted_open = parse_float(row.get("unadjusted_open"))
            unadjusted = parse_float(row.get("unadjusted_close"))
            adjusted = parse_float(row.get("adjusted_close"))
            adjustment_factor = parse_float(row.get("adjustment_factor"))
            if adjusted is None and unadjusted is not None and (adjustment_factor is None or adjustment_factor <= 0):
                raise ValueError(
                    "Missing adjusted_close requires positive adjustment_factor "
                    f"for code={code};date={row.get('date', '')}."
                )
            if adjustment_factor is not None and adjustment_factor > 0:
                cumulative_adjustment *= adjustment_factor
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
        last_trading_date = parse_date(row.get("last_trading_date"), field_name="listings.last_trading_date")
        delisted_date = parse_date(row.get("delisted_date"), field_name="listings.delisted_date")
        exit_date = last_trading_date or delisted_date
        if exit_date is not None:
            existing = values.get(code)
            if existing is not None and existing != exit_date:
                raise ValueError(
                    "Conflicting lifecycle exit dates "
                    f"for code={code};existing={existing};new={exit_date}."
                )
            values[code] = exit_date
    return values


def next_trading_date(calendar: list[date], after_date: date) -> date | None:
    for value in calendar:
        if value > after_date:
            return value
    return None


def execution_point(
    price_index: dict[str, list[PricePoint]],
    price_calendar: list[date],
    code: str,
    signal_date: date,
    mode: str,
) -> PricePoint | None:
    if mode == "rebalance_close":
        return price_on_date(price_index, code, signal_date)
    execution_date = next_trading_date(price_calendar, signal_date)
    if execution_date is None:
        return None
    return price_on_date(price_index, code, execution_date)


def execution_price(point: PricePoint, mode: str) -> float:
    if mode == "next_open":
        return point.unadjusted_open
    return point.unadjusted_close


def requested_execution_price_available(status: RawPriceStatus | None, mode: str) -> bool:
    if status is None:
        return False
    if mode == "next_open":
        return status.has_open
    return status.has_close


def raw_price_row_blank(status: RawPriceStatus) -> bool:
    return not (status.has_open or status.has_close or status.has_volume or status.has_trading_value)


def execution_price_failure_type(
    *,
    mode: str,
    intended_execution_date: date | None,
    raw_status: RawPriceStatus | None,
    fill_point: PricePoint | None,
) -> str | None:
    if intended_execution_date is None or raw_status is None:
        return "missing_execution_price_row"
    if raw_status.tradable_flag is False or raw_price_row_blank(raw_status):
        return "execution_date_not_tradable"
    if not requested_execution_price_available(raw_status, mode) or fill_point is None:
        return "execution_price_unavailable_on_execution_date"
    return None


def execution_price_failure_detail(
    *,
    mode: str,
    intended_execution_date: date | None,
    raw_status: RawPriceStatus | None,
) -> str:
    fields = {
        "execution_price": mode,
        "intended_execution_date": intended_execution_date or "",
        "has_price_row": raw_status is not None,
        "tradable_flag": "" if raw_status is None or raw_status.tradable_flag is None else raw_status.tradable_flag,
        "has_open": raw_status.has_open if raw_status else False,
        "has_close": raw_status.has_close if raw_status else False,
        "has_volume": raw_status.has_volume if raw_status else False,
        "has_trading_value": raw_status.has_trading_value if raw_status else False,
    }
    return ";".join(f"{key}={value}" for key, value in fields.items())


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


def retarget_actual_shares_for_fill(target_shares: int, signal_point: PricePoint, fill_point: PricePoint) -> int:
    target_adjusted_shares = adjusted_shares_for_trade(target_shares, signal_point)
    return int(round(actual_shares_from_adjusted(target_adjusted_shares, fill_point)))


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
    if any(marker.startswith("pit_inferred_lifecycle") for marker in lifecycle_markers):
        return "pit_inferred_lifecycle"
    if "pit_snapshot_panel_missing_lifecycle_dates" in lifecycle_markers:
        return "pit_snapshot_panel"
    if snapshot_only_listings(rows):
        return "snapshot_only"
    listed_dates = [(row.get("listed_date") or "").strip() for row in rows]
    if any(not value for value in listed_dates):
        return "partial_lifecycle"
    lifecycle_exit_dates = [
        (row.get("last_trading_date") or row.get("delisted_date") or "").strip()
        for row in rows
    ]
    if not any(lifecycle_exit_dates):
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
    lifecycle_exit_date = (delisting_dates or {}).get(code)
    if lifecycle_exit_date is not None and start <= lifecycle_exit_date <= end:
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


def sector_cap_config(config: dict[str, Any]) -> SectorCapConfig:
    raw = (config.get("portfolio", {}) or {}).get("sector_cap", {}) or {}
    enabled = bool(parse_bool(raw.get("enabled"), default=False))
    if not enabled:
        return SectorCapConfig()
    mode = str(raw.get("mode") or "name_count").strip()
    group_field = str(raw.get("group_field") or "sector").strip() or "sector"
    if mode == "name_count":
        max_names = parse_int(raw.get("max_names_per_group"))
        if max_names is None or max_names < 1:
            raise ValueError("portfolio.sector_cap.max_names_per_group must be a positive integer.")
        return SectorCapConfig(
            enabled=True,
            mode=mode,
            group_field=group_field,
            max_names_per_group=max_names,
        )
    if mode == "target_weight":
        max_weight = parse_float(raw.get("max_sector_weight"))
        if max_weight is None or max_weight <= 0 or max_weight > 1:
            raise ValueError("portfolio.sector_cap.max_sector_weight must be in (0, 1].")
        raise ValueError("portfolio.sector_cap mode target_weight is not implemented yet. Use mode: name_count.")
    raise ValueError(f"Unsupported portfolio.sector_cap.mode: {mode}")


def sector_cap_limit_value(cap: SectorCapConfig) -> str:
    if not cap.enabled:
        return ""
    if cap.mode == "name_count":
        return str(cap.max_names_per_group or "")
    if cap.mode == "target_weight":
        return str(cap.max_sector_weight or "")
    return ""


def affordable_lot_filter_config(config: dict[str, Any]) -> AffordableLotFilterConfig:
    raw = (config.get("portfolio", {}) or {}).get("affordable_lot_filter", {}) or {}
    enabled = bool(parse_bool(raw.get("enabled"), default=False))
    if not enabled:
        return AffordableLotFilterConfig()
    max_weight = parse_float(raw.get("max_single_lot_weight"))
    if max_weight is None or max_weight <= 0 or max_weight > 1:
        raise ValueError("portfolio.affordable_lot_filter.max_single_lot_weight must be in (0, 1].")
    min_weight = parse_float(raw.get("min_single_lot_weight"))
    if min_weight is not None and (min_weight < 0 or min_weight > 1):
        raise ValueError("portfolio.affordable_lot_filter.min_single_lot_weight must be in [0, 1].")
    if min_weight is not None and min_weight > max_weight:
        raise ValueError(
            "portfolio.affordable_lot_filter.min_single_lot_weight cannot exceed max_single_lot_weight."
        )
    cash_buffer = parse_float(raw.get("cash_buffer_weight"), default=0.0) or 0.0
    if cash_buffer < 0 or cash_buffer >= 1:
        raise ValueError("portfolio.affordable_lot_filter.cash_buffer_weight must be in [0, 1).")
    return AffordableLotFilterConfig(
        enabled=True,
        max_single_lot_weight=max_weight,
        min_single_lot_weight=min_weight,
        cash_buffer_weight=cash_buffer,
    )


def execution_diagnostics_config(config: dict[str, Any]) -> ExecutionDiagnosticsConfig:
    raw = ((config.get("reporting", {}) or {}).get("execution_diagnostics", {}) or {})
    enabled = bool(parse_bool(raw.get("enabled"), default=False))
    if not enabled:
        return ExecutionDiagnosticsConfig()
    threshold = parse_float(raw.get("high_cash_threshold"), default=0.30)
    if threshold is None or threshold < 0 or threshold > 1:
        raise ValueError("reporting.execution_diagnostics.high_cash_threshold must be in [0, 1].")
    return ExecutionDiagnosticsConfig(enabled=True, high_cash_threshold=threshold)


def margin_config(config: dict[str, Any]) -> MarginConfig:
    raw = (config.get("margin", {}) or {})
    enabled = bool(parse_bool(raw.get("enabled"), default=False))
    if not enabled:
        return MarginConfig()
    account_type = str(raw.get("account_type") or "margin_long").strip() or "margin_long"
    if account_type != "margin_long":
        raise ValueError("margin.account_type must be margin_long when margin.enabled is true.")
    target_gross_leverage = parse_float(raw.get("target_gross_leverage"), default=1.0)
    target_gross_leverage = 1.0 if target_gross_leverage is None else target_gross_leverage
    max_gross_leverage = parse_float(raw.get("max_gross_leverage"), default=target_gross_leverage)
    max_gross_leverage = target_gross_leverage if max_gross_leverage is None else max_gross_leverage
    annual_borrow_rate = parse_float(raw.get("annual_borrow_rate"), default=0.0)
    annual_borrow_rate = 0.0 if annual_borrow_rate is None else annual_borrow_rate
    initial_margin_requirement = parse_float(raw.get("initial_margin_requirement"), default=1.0)
    initial_margin_requirement = 1.0 if initial_margin_requirement is None else initial_margin_requirement
    maintenance_margin_requirement = parse_float(raw.get("maintenance_margin_requirement"), default=0.0)
    maintenance_margin_requirement = 0.0 if maintenance_margin_requirement is None else maintenance_margin_requirement
    minimum_required_equity = parse_float(raw.get("minimum_required_equity"), default=0.0)
    minimum_required_equity = 0.0 if minimum_required_equity is None else minimum_required_equity
    interest_day_count = parse_int(raw.get("interest_day_count"), default=365)
    interest_day_count = 365 if interest_day_count is None else interest_day_count
    margin_call_action = str(raw.get("margin_call_action") or "flag_only").strip() or "flag_only"
    if target_gross_leverage <= 0:
        raise ValueError("margin.target_gross_leverage must be positive.")
    if max_gross_leverage <= 0:
        raise ValueError("margin.max_gross_leverage must be positive.")
    if annual_borrow_rate < 0:
        raise ValueError("margin.annual_borrow_rate must be non-negative.")
    if initial_margin_requirement <= 0 or initial_margin_requirement > 1:
        raise ValueError("margin.initial_margin_requirement must be in (0, 1].")
    if maintenance_margin_requirement < 0 or maintenance_margin_requirement > 1:
        raise ValueError("margin.maintenance_margin_requirement must be in [0, 1].")
    if maintenance_margin_requirement > initial_margin_requirement:
        raise ValueError("margin.maintenance_margin_requirement cannot exceed initial_margin_requirement.")
    if minimum_required_equity < 0:
        raise ValueError("margin.minimum_required_equity must be non-negative.")
    if interest_day_count <= 0:
        raise ValueError("margin.interest_day_count must be positive.")
    if margin_call_action not in {"flag_only", "force_deleverage_to_target"}:
        raise ValueError("margin.margin_call_action must be flag_only or force_deleverage_to_target.")
    if margin_call_action != "flag_only":
        raise ValueError("margin.margin_call_action=force_deleverage_to_target is reserved for a later engine version.")
    return MarginConfig(
        enabled=True,
        account_type=account_type,
        target_gross_leverage=target_gross_leverage,
        max_gross_leverage=max_gross_leverage,
        annual_borrow_rate=annual_borrow_rate,
        initial_margin_requirement=initial_margin_requirement,
        maintenance_margin_requirement=maintenance_margin_requirement,
        minimum_required_equity=minimum_required_equity,
        interest_day_count=interest_day_count,
        margin_call_action=margin_call_action,
    )


def distribution_stats(values: list[float], prefix: str) -> dict[str, Any]:
    clean = [value for value in values if value is not None]
    if not clean:
        return {
            f"{prefix}_min": "",
            f"{prefix}_median": "",
            f"{prefix}_max": "",
        }
    return {
        f"{prefix}_min": min(clean),
        f"{prefix}_median": median_or_none(clean),
        f"{prefix}_max": max(clean),
    }


def single_lot_value_at(
    code: str,
    universe_by_code: dict[str, dict[str, Any]],
    price_index: dict[str, list[PricePoint]],
    valuation_date: date,
) -> float | None:
    point = price_at(price_index, code, valuation_date)
    if point is None:
        return None
    lot = parse_int(universe_by_code.get(code, {}).get("lot_size"), default=100) or 100
    return lot * point.unadjusted_close


def targetable_equity(equity: float, affordable_filter: AffordableLotFilterConfig) -> float:
    buffer_weight = affordable_filter.cash_buffer_weight if affordable_filter.enabled else 0.0
    return max(equity * (1.0 - buffer_weight), 0.0)


def max_allowed_gross_leverage(config: MarginConfig) -> float:
    if not config.enabled:
        return 1.0
    initial_cap = 1.0 / config.initial_margin_requirement if config.initial_margin_requirement > 0 else config.max_gross_leverage
    return min(config.max_gross_leverage, initial_cap)


def effective_target_gross_leverage(config: MarginConfig) -> float:
    if not config.enabled:
        return 1.0
    return min(config.target_gross_leverage, max_allowed_gross_leverage(config))


def target_gross_exposure(
    equity: float,
    affordable_filter: AffordableLotFilterConfig,
    margin: MarginConfig | None = None,
) -> float:
    return targetable_equity(equity, affordable_filter) * effective_target_gross_leverage(margin or MarginConfig())


def leverage_cap_was_reduced(config: MarginConfig) -> bool:
    return config.enabled and effective_target_gross_leverage(config) < config.target_gross_leverage


def financing_cost_for_period(borrowed_value: float, config: MarginConfig, days_held: int) -> float:
    if not config.enabled or borrowed_value <= 0 or days_held <= 0:
        return 0.0
    return borrowed_value * config.annual_borrow_rate * days_held / config.interest_day_count


def margin_available_buying_power(cash: float, gross_before_fill: float, config: MarginConfig) -> float:
    if not config.enabled:
        return cash
    account_equity = cash + gross_before_fill
    gross_limit = max_allowed_gross_leverage(config) * max(account_equity, 0.0)
    return max(gross_limit - gross_before_fill, 0.0)


def margin_snapshot(gross_exposure: float, account_equity: float, config: MarginConfig) -> dict[str, Any]:
    borrowed_value = max(gross_exposure - account_equity, 0.0)
    gross_leverage: float | str
    if account_equity > 0:
        gross_leverage = gross_exposure / account_equity
    elif gross_exposure > 0:
        gross_leverage = "unbounded"
    else:
        gross_leverage = 0.0
    margin_ratio = account_equity / gross_exposure if gross_exposure > 0 else 1.0
    return {
        "gross_exposure": gross_exposure,
        "borrowed_value": borrowed_value,
        "net_account_equity": account_equity,
        "gross_leverage": gross_leverage,
        "margin_ratio": margin_ratio,
        "maintenance_margin_breach": (
            bool(config.enabled and gross_exposure > 0 and margin_ratio < config.maintenance_margin_requirement)
        ),
        "minimum_equity_breach": bool(config.enabled and account_equity < config.minimum_required_equity),
    }


def margin_daily_rows_for_period(
    *,
    start_date: date,
    end_date: date,
    rebalance_date: date,
    holdings: dict[str, float],
    cash: float,
    price_index: dict[str, list[PricePoint]],
    price_calendar: list[date],
    config: MarginConfig,
) -> list[dict[str, Any]]:
    if not config.enabled or end_date <= start_date:
        return []
    rows: list[dict[str, Any]] = []
    for value_date in price_calendar:
        if value_date <= start_date:
            continue
        if value_date > end_date:
            break
        gross_exposure = 0.0
        for code, adjusted_shares in holdings.items():
            point = price_at(price_index, code, value_date)
            if point is None:
                continue
            gross_exposure += position_value(adjusted_shares, point)
        account_equity = cash + gross_exposure
        snapshot = margin_snapshot(gross_exposure, account_equity, config)
        rows.append(
            {
                "date": value_date,
                "rebalance_date": rebalance_date,
                "gross_exposure": snapshot["gross_exposure"],
                "borrowed_value": snapshot["borrowed_value"],
                "account_equity": snapshot["net_account_equity"],
                "gross_leverage": snapshot["gross_leverage"],
                "margin_ratio": snapshot["margin_ratio"],
                "maintenance_margin_breach": snapshot["maintenance_margin_breach"],
                "minimum_equity_breach": snapshot["minimum_equity_breach"],
            }
        )
    return rows


def margin_summary_stats(rows: list[dict[str, Any]], cumulative_financing_cost: float) -> dict[str, Any]:
    if not rows:
        return {
            "min_margin_ratio": "",
            "margin_breach_count": 0,
            "first_margin_breach_date": "",
            "minimum_equity_breach_count": 0,
            "max_margin_gross_leverage": "",
            "avg_margin_gross_leverage": "",
            "max_borrowed_value": "",
            "cumulative_financing_cost": cumulative_financing_cost,
        }
    ratios = [float(row["margin_ratio"]) for row in rows if parse_float(row.get("margin_ratio")) is not None]
    has_unbounded_leverage = any(str(row.get("gross_leverage", "")).lower() == "unbounded" for row in rows)
    leverages = [float(row["gross_leverage"]) for row in rows if parse_float(row.get("gross_leverage")) is not None]
    borrowed_values = [float(row["borrowed_value"]) for row in rows if parse_float(row.get("borrowed_value")) is not None]
    breach_rows = [row for row in rows if parse_bool(row.get("maintenance_margin_breach"), default=False)]
    min_equity_rows = [row for row in rows if parse_bool(row.get("minimum_equity_breach"), default=False)]
    return {
        "min_margin_ratio": min(ratios) if ratios else "",
        "margin_breach_count": len(breach_rows),
        "first_margin_breach_date": breach_rows[0]["date"] if breach_rows else "",
        "minimum_equity_breach_count": len(min_equity_rows),
        "max_margin_gross_leverage": "unbounded" if has_unbounded_leverage else (max(leverages) if leverages else ""),
        "avg_margin_gross_leverage": "unbounded" if has_unbounded_leverage else ((sum(leverages) / len(leverages)) if leverages else ""),
        "max_borrowed_value": max(borrowed_values) if borrowed_values else "",
        "cumulative_financing_cost": cumulative_financing_cost,
    }


def candidate_rebalance_price(
    code: str,
    row: dict[str, Any],
    universe: dict[str, Any],
    price_index: dict[str, list[PricePoint]] | None,
    rebalance_date: date | None,
) -> float | None:
    if price_index is not None and rebalance_date is not None:
        point = price_on_date(price_index, code, rebalance_date)
        if point is not None:
            return point.unadjusted_close
    return parse_float(row.get("latest_unadjusted_close")) or parse_float(universe.get("latest_unadjusted_close"))


def security_group(row: dict[str, Any] | None, group_field: str) -> str:
    value = (row or {}).get(group_field, "")
    text = str(value or "").strip()
    return text or "UNKNOWN"


def security_group_for_code(
    code: str,
    *,
    cap: SectorCapConfig,
    row_by_code: dict[str, dict[str, Any]],
    universe_by_code: dict[str, dict[str, Any]],
) -> str:
    universe_row = universe_by_code.get(code, {})
    if cap.group_field in universe_row:
        return security_group(universe_row, cap.group_field)
    return security_group(row_by_code.get(code), cap.group_field)


def select_codes_detailed(
    scores: list[dict[str, str]],
    holdings: dict[str, float],
    config: dict[str, Any],
    universe_by_code: dict[str, dict[str, Any]] | None = None,
    price_index: dict[str, list[PricePoint]] | None = None,
    rebalance_date: date | None = None,
    equity: float | None = None,
    margin: MarginConfig | None = None,
) -> SelectionResult:
    ranked = sorted(
        [row for row in scores if parse_int(row.get("rank")) is not None],
        key=lambda row: parse_int(row.get("rank"), default=999999) or 999999,
    )
    rank_by_code = {row["code"]: parse_int(row.get("rank"), default=999999) or 999999 for row in ranked}
    row_by_code = {row["code"]: row for row in ranked}
    universe_count = len(ranked)
    cap = sector_cap_config(config)
    affordable_filter = affordable_lot_filter_config(config)
    if not ranked:
        return SelectionResult([], [], 0, cap, affordable_filter, [], [], 0, {})

    executable_config = config["portfolio"].get("executable_portfolio", {})
    target_count = min(int(executable_config.get("target_holdings_max", 30)), universe_count)
    target_count = max(min(int(executable_config.get("target_holdings_min", 1)), universe_count), target_count)
    if affordable_filter.enabled and equity is None:
        raise ValueError("portfolio.affordable_lot_filter requires portfolio equity for selection.")
    target_value = (
        target_gross_exposure(float(equity or 0.0), affordable_filter, margin) / target_count
        if affordable_filter.enabled and target_count
        else None
    )

    buy_rule = config["portfolio"].get("buy_rule", {})
    hold_rule = config["portfolio"].get("hold_rule", {})
    buy_limit = rank_rule_limit(universe_count, buy_rule, default_pct=10.0, default_n=50)
    hold_limit = rank_rule_limit(universe_count, hold_rule, default_pct=20.0, default_n=100)

    research_codes = [row["code"] for row in ranked[:target_count]]
    kept = [code for code, shares in holdings.items() if shares > 0 and rank_by_code.get(code, 999999) <= hold_limit]
    kept.sort(key=lambda code: rank_by_code.get(code, 999999))
    universe_by_code = universe_by_code or {}
    affordability_excluded: list[AffordabilityExcludedCandidate] = []

    def affordable_exclusion(code: str, phase: str) -> AffordabilityExcludedCandidate | None:
        if not affordable_filter.enabled:
            return None
        universe = universe_by_code.get(code, {})
        row = row_by_code.get(code, {})
        lot = parse_int(universe.get("lot_size"), default=parse_int(executable_config.get("lot_size"), default=100))
        price = candidate_rebalance_price(code, row, universe, price_index, rebalance_date)
        if lot is None or lot <= 0 or price is None or price <= 0:
            return AffordabilityExcludedCandidate(
                code=code,
                rank=rank_by_code.get(code, 999999),
                phase=phase,
                reason="missing_lot_or_price",
                lot_size=lot,
                price=price,
                single_lot_value=None,
                single_lot_weight=None,
                target_value=target_value,
            )
        single_lot_value = lot * price
        single_lot_weight = single_lot_value / float(equity or 0.0) if equity and equity > 0 else None
        reason = ""
        if single_lot_weight is not None and affordable_filter.max_single_lot_weight is not None:
            if single_lot_weight > affordable_filter.max_single_lot_weight:
                reason = "above_max_single_lot_weight"
        if (
            not reason
            and single_lot_weight is not None
            and affordable_filter.min_single_lot_weight is not None
            and single_lot_weight < affordable_filter.min_single_lot_weight
        ):
            reason = "below_min_single_lot_weight"
        if not reason and target_value is not None and single_lot_value > target_value:
            reason = "zero_lot_avoided"
        if not reason:
            return None
        return AffordabilityExcludedCandidate(
            code=code,
            rank=rank_by_code.get(code, 999999),
            phase=phase,
            reason=reason,
            lot_size=lot,
            price=price,
            single_lot_value=single_lot_value,
            single_lot_weight=single_lot_weight,
            target_value=target_value,
        )

    def is_affordable(code: str, phase: str) -> bool:
        exclusion = affordable_exclusion(code, phase)
        if exclusion is None:
            return True
        affordability_excluded.append(exclusion)
        return False

    if not cap.enabled:
        selected: list[str] = []
        dropped_held_codes: set[str] = set()
        for code in kept:
            if len(selected) >= target_count:
                break
            if not is_affordable(code, "hold"):
                dropped_held_codes.add(code)
                continue
            selected.append(code)
        selected_set = set(selected)
        for row in ranked[:buy_limit]:
            code = row["code"]
            if code in dropped_held_codes:
                continue
            if code in selected_set:
                continue
            if not is_affordable(code, "buy"):
                continue
            selected.append(code)
            selected_set.add(code)
            if len(selected) >= target_count:
                break
        return SelectionResult(
            selected_codes=selected,
            research_codes=research_codes,
            target_count=target_count,
            sector_cap=cap,
            affordable_lot_filter=affordable_filter,
            blocked_candidates=[],
            affordability_excluded=affordability_excluded,
            unfilled_slots=max(target_count - len(selected), 0) if affordability_excluded else 0,
            selected_group_counts={},
        )

    selected: list[str] = []
    selected_set: set[str] = set()
    group_counts: dict[str, int] = defaultdict(int)
    blocked: list[SectorCapBlockedCandidate] = []
    dropped_held_codes: set[str] = set()

    def can_add(code: str) -> tuple[bool, str]:
        group = security_group_for_code(
            code,
            cap=cap,
            row_by_code=row_by_code,
            universe_by_code=universe_by_code,
        )
        if cap.mode == "name_count" and group_counts[group] >= int(cap.max_names_per_group or 0):
            return False, group
        return True, group

    def add_code(code: str, group: str) -> None:
        selected.append(code)
        selected_set.add(code)
        group_counts[group] += 1

    for code in kept:
        if len(selected) >= target_count:
            break
        if not is_affordable(code, "hold"):
            dropped_held_codes.add(code)
            continue
        allowed, group = can_add(code)
        if allowed:
            add_code(code, group)
        else:
            blocked.append(SectorCapBlockedCandidate(code, group, rank_by_code.get(code, 999999), "hold"))
            dropped_held_codes.add(code)

    for row in ranked[:buy_limit]:
        code = row["code"]
        if code in dropped_held_codes:
            continue
        if code in selected_set:
            continue
        if len(selected) >= target_count:
            break
        if not is_affordable(code, "buy"):
            continue
        allowed, group = can_add(code)
        if not allowed:
            blocked.append(SectorCapBlockedCandidate(code, group, rank_by_code.get(code, 999999), "buy"))
            continue
        add_code(code, group)
        if len(selected) >= target_count:
            break

    unfilled_slots = max(target_count - len(selected), 0) if blocked or affordability_excluded else 0
    return SelectionResult(
        selected_codes=selected,
        research_codes=research_codes,
        target_count=target_count,
        sector_cap=cap,
        affordable_lot_filter=affordable_filter,
        blocked_candidates=blocked,
        affordability_excluded=affordability_excluded,
        unfilled_slots=unfilled_slots,
        selected_group_counts=dict(group_counts),
    )


def select_codes(
    scores: list[dict[str, str]],
    holdings: dict[str, float],
    config: dict[str, Any],
    universe_by_code: dict[str, dict[str, Any]] | None = None,
    price_index: dict[str, list[PricePoint]] | None = None,
    rebalance_date: date | None = None,
    equity: float | None = None,
    margin: MarginConfig | None = None,
) -> tuple[list[str], list[str]]:
    result = select_codes_detailed(
        scores,
        holdings,
        config,
        universe_by_code,
        price_index=price_index,
        rebalance_date=rebalance_date,
        equity=equity,
        margin=margin,
    )
    return result.selected_codes, result.research_codes


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
    affordable_filter: AffordableLotFilterConfig | None = None,
    margin: MarginConfig | None = None,
) -> dict[str, int]:
    if not selected_codes:
        return {}
    target_equity = target_gross_exposure(equity, affordable_filter or AffordableLotFilterConfig(), margin)
    target_value = target_equity / len(selected_codes)
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


def sector_cap_failure_rows(rebalance_date: date, result: SelectionResult) -> list[dict[str, Any]]:
    if not result.sector_cap.enabled:
        return []
    rows: list[dict[str, Any]] = []
    for item in result.blocked_candidates:
        rows.append(
            {
                "date": rebalance_date,
                "code": item.code,
                "failure_type": "sector_cap_blocked_candidate",
                "detail": (
                    f"mode={result.sector_cap.mode};group_field={result.sector_cap.group_field};"
                    f"group={item.group};rank={item.rank};phase={item.phase};"
                    f"limit={sector_cap_limit_value(result.sector_cap)}"
                ),
                "value": item.rank,
            }
        )
    if result.unfilled_slots:
        rows.append(
            {
                "date": rebalance_date,
                "code": "",
                "failure_type": "sector_cap_unfilled_target",
                "detail": (
                    f"target_count={result.target_count};selected_count={len(result.selected_codes)};"
                    f"unfilled_slots={result.unfilled_slots};limit={sector_cap_limit_value(result.sector_cap)}"
                ),
                "value": result.unfilled_slots,
            }
        )
    return rows


def affordability_failure_detail(item: AffordabilityExcludedCandidate, result: SelectionResult) -> str:
    config = result.affordable_lot_filter
    return (
        f"reason={item.reason};rank={item.rank};phase={item.phase};"
        f"lot_size={item.lot_size if item.lot_size is not None else ''};"
        f"price={item.price if item.price is not None else ''};"
        f"single_lot_value={item.single_lot_value if item.single_lot_value is not None else ''};"
        f"single_lot_weight={item.single_lot_weight if item.single_lot_weight is not None else ''};"
        f"target_value={item.target_value if item.target_value is not None else ''};"
        f"max_single_lot_weight={config.max_single_lot_weight if config.max_single_lot_weight is not None else ''};"
        f"min_single_lot_weight={config.min_single_lot_weight if config.min_single_lot_weight is not None else ''};"
        f"cash_buffer_weight={config.cash_buffer_weight}"
    )


def affordable_lot_failure_rows(rebalance_date: date, result: SelectionResult) -> list[dict[str, Any]]:
    if not result.affordable_lot_filter.enabled:
        return []
    rows: list[dict[str, Any]] = []
    for item in result.affordability_excluded:
        rows.append(
            {
                "date": rebalance_date,
                "code": item.code,
                "failure_type": "affordability_excluded",
                "detail": affordability_failure_detail(item, result),
                "value": item.single_lot_value if item.single_lot_value is not None else "",
            }
        )
        if item.reason == "zero_lot_avoided":
            rows.append(
                {
                    "date": rebalance_date,
                    "code": item.code,
                    "failure_type": "zero_lot_avoided",
                    "detail": affordability_failure_detail(item, result),
                    "value": item.single_lot_value if item.single_lot_value is not None else "",
                }
            )
    if result.unfilled_slots and result.affordability_excluded:
        rows.append(
            {
                "date": rebalance_date,
                "code": "",
                "failure_type": "affordability_unfilled_target",
                "detail": (
                    f"target_count={result.target_count};selected_count={len(result.selected_codes)};"
                    f"unfilled_slots={result.unfilled_slots};"
                    f"cash_buffer_weight={result.affordable_lot_filter.cash_buffer_weight}"
                ),
                "value": result.unfilled_slots,
            }
        )
    return rows


def sector_exposure_rows(
    *,
    signal_date: date,
    valuation_date: date,
    result: SelectionResult,
    targets: dict[str, int],
    holdings: dict[str, float],
    universe_by_code: dict[str, dict[str, str]],
    price_index: dict[str, list[PricePoint]],
    pre_equity: float,
    after_equity: float,
) -> tuple[list[dict[str, Any]], float, float, int, list[dict[str, Any]]]:
    cap = result.sector_cap
    if not cap.enabled:
        return [], 0.0, 0.0, 0, []

    groups = sorted(
        {
            security_group(universe_by_code.get(code), cap.group_field)
            for code in set(result.selected_codes) | set(targets) | set(holdings)
        }
    )
    selected_count_by_group: dict[str, int] = defaultdict(int)
    target_value_by_group: dict[str, float] = defaultdict(float)
    actual_value_by_group: dict[str, float] = defaultdict(float)
    actual_count_by_group: dict[str, int] = defaultdict(int)

    for code in result.selected_codes:
        selected_count_by_group[security_group(universe_by_code.get(code), cap.group_field)] += 1
    for code, shares in targets.items():
        point = price_on_date(price_index, code, signal_date)
        if not point:
            continue
        target_value_by_group[security_group(universe_by_code.get(code), cap.group_field)] += shares * point.unadjusted_close
    for code, adjusted_shares in holdings.items():
        point = price_at(price_index, code, valuation_date)
        if not point:
            continue
        group = security_group(universe_by_code.get(code), cap.group_field)
        actual_value_by_group[group] += position_value(adjusted_shares, point)
        actual_count_by_group[group] += 1

    max_selected_weight = (
        max((count / len(result.selected_codes) for count in selected_count_by_group.values()), default=0.0)
        if result.selected_codes
        else 0.0
    )
    max_actual_weight = (
        max((value / after_equity for value in actual_value_by_group.values()), default=0.0)
        if after_equity
        else 0.0
    )
    cap_limit = parse_int(cap.max_names_per_group) if cap.mode == "name_count" else None
    violation_rows: list[dict[str, Any]] = []
    exposure_rows: list[dict[str, Any]] = []
    violation_count = 0
    for group in groups:
        selected_count = selected_count_by_group.get(group, 0)
        actual_count = actual_count_by_group.get(group, 0)
        violation = max(actual_count - int(cap_limit or 0), 0) if cap_limit else 0
        if violation:
            violation_count += 1
            violation_rows.append(
                {
                    "date": signal_date,
                    "code": "",
                    "failure_type": "sector_cap_actual_violation",
                    "detail": (
                        f"mode={cap.mode};group_field={cap.group_field};group={group};"
                        f"actual_count={actual_count};limit={cap_limit};violation={violation}"
                    ),
                    "value": violation,
                }
            )
        exposure_rows.append(
            {
                "date": valuation_date,
                "group": group,
                "selected_count": selected_count,
                "target_weight": target_value_by_group.get(group, 0.0) / pre_equity if pre_equity else 0,
                "actual_weight": actual_value_by_group.get(group, 0.0) / after_equity if after_equity else 0,
                "cap_limit": sector_cap_limit_value(cap),
                "violation": violation,
            }
        )
    return exposure_rows, max_selected_weight, max_actual_weight, violation_count, violation_rows


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
    "source_date",
    "source",
    "listing_lifecycle_status",
    "listed_date",
    "delisted_date",
    "last_trading_date",
    "lifecycle_exit_date",
    "delisting_reason",
    "successor_code",
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


def score_cache_fields(raw_factors: list[str], *, direct_fields: set[str] | None = None) -> list[str]:
    return list(dict.fromkeys([
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
        *[score_output_field(factor, direct_fields=direct_fields) for factor in raw_factors],
    ]))


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
    if (
        args.sector_cap_mode is not None
        or args.sector_cap_group_field is not None
        or args.max_names_per_sector is not None
        or args.max_sector_weight is not None
    ):
        sector_cap = updated.setdefault("portfolio", {}).setdefault("sector_cap", {})
        sector_cap["enabled"] = True
        if args.sector_cap_mode is not None:
            sector_cap["mode"] = args.sector_cap_mode
        if args.sector_cap_group_field is not None:
            sector_cap["group_field"] = args.sector_cap_group_field
        if args.max_names_per_sector is not None:
            if args.sector_cap_mode is None:
                sector_cap["mode"] = "name_count"
            sector_cap["max_names_per_group"] = args.max_names_per_sector
        if args.max_sector_weight is not None:
            if args.sector_cap_mode is None:
                sector_cap["mode"] = "target_weight"
            sector_cap["max_sector_weight"] = args.max_sector_weight
    if (
        args.margin_enabled
        or args.target_gross_leverage is not None
        or args.max_gross_leverage is not None
        or args.annual_borrow_rate is not None
        or args.initial_margin_requirement is not None
        or args.maintenance_margin_requirement is not None
        or args.minimum_required_equity is not None
        or args.margin_call_action is not None
    ):
        margin = updated.setdefault("margin", {})
        if args.margin_enabled:
            margin["enabled"] = True
            margin.setdefault("account_type", "margin_long")
        if args.target_gross_leverage is not None:
            margin["target_gross_leverage"] = args.target_gross_leverage
        if args.max_gross_leverage is not None:
            margin["max_gross_leverage"] = args.max_gross_leverage
        if args.annual_borrow_rate is not None:
            margin["annual_borrow_rate"] = args.annual_borrow_rate
        if args.initial_margin_requirement is not None:
            margin["initial_margin_requirement"] = args.initial_margin_requirement
        if args.maintenance_margin_requirement is not None:
            margin["maintenance_margin_requirement"] = args.maintenance_margin_requirement
        if args.minimum_required_equity is not None:
            margin["minimum_required_equity"] = args.minimum_required_equity
        if args.margin_call_action is not None:
            margin["margin_call_action"] = args.margin_call_action
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
    price_universe_panel = getattr(args, "price_universe_panel", None)
    price_universe_panel_checksum = checksum(price_universe_panel) if price_universe_panel else ""
    factor_score_panel = getattr(args, "factor_score_panel", None)
    factor_score_panel_checksum = checksum(factor_score_panel) if factor_score_panel else ""
    inputs_payload = {
        "schema_version": "walkforward_inputs_cache_v0_1",
        "inputs": {
            "listings": checksum(args.listings),
            "prices": checksum(args.prices),
            "fundamentals": checksum(args.fundamentals),
        },
    }
    inputs_fingerprint = cache_digest(inputs_payload)
    universe_source = {
        "research_common.py": source_checksum("research_common.py"),
    }
    if factor_score_panel:
        universe_source["run_qvm_walkforward.py"] = source_checksum("run_qvm_walkforward.py")
    elif price_universe_panel:
        universe_source["run_qvm_walkforward.py"] = source_checksum("run_qvm_walkforward.py")
    else:
        universe_source["build_universe.py"] = source_checksum("build_universe.py")
    universe_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_universe_cache_v0_3",
            "inputs": inputs_fingerprint,
            "price_universe_panel": price_universe_panel_checksum,
            "factor_score_panel": factor_score_panel_checksum,
            "config": cache_config(config, "scope", "universe"),
            "source": universe_source,
        }
    )
    factor_source = {
        "research_common.py": source_checksum("research_common.py"),
    }
    if factor_score_panel:
        factor_source["run_qvm_walkforward.py"] = source_checksum("run_qvm_walkforward.py")
    else:
        factor_source["build_factors.py"] = source_checksum("build_factors.py")
        factor_source["factor_expressions.py"] = source_checksum("factor_expressions.py")
        factor_source["external_factor_panels.py"] = source_checksum("external_factor_panels.py")
    factors_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_factors_cache_v0_2",
            "inputs": inputs_fingerprint,
            "universe": universe_fingerprint,
            "factor_score_panel": factor_score_panel_checksum,
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
            "external_factor_panels": external_factor_panel_fingerprints(config),
            "source": factor_source,
        }
    )
    score_source = {
        "research_common.py": source_checksum("research_common.py"),
    }
    if factor_score_panel:
        score_source["run_qvm_walkforward.py"] = source_checksum("run_qvm_walkforward.py")
    else:
        score_source["build_scores.py"] = source_checksum("build_scores.py")
        score_source["factor_expressions.py"] = source_checksum("factor_expressions.py")
    scores_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_scores_cache_v0_1",
            "factors": factors_fingerprint,
            "factor_score_panel": factor_score_panel_checksum,
            "strategy_version": args.strategy_version,
            "config": cache_config(config, "strategy", "factors"),
            "source": score_source,
        }
    )
    run_fingerprint = cache_digest(
        {
            "schema_version": "walkforward_run_cache_v0_1",
            "scores": scores_fingerprint,
            "date_range": {"start": args.start_date, "end": args.end_date},
            "frequency": args.frequency,
            "portfolio": cache_config(
                config,
                "portfolio",
                "execution",
                "cost_model",
                "tax",
                "margin",
                "missing_price_tail_policy",
                "reporting",
            ),
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


def read_price_universe_panel_rows(args: argparse.Namespace) -> list[dict[str, str]]:
    rows = getattr(args, "_price_universe_panel_rows", None)
    if rows is not None:
        return rows
    panel_path = getattr(args, "price_universe_panel", None)
    if not panel_path:
        raise ValueError("price/universe panel requested but --price-universe-panel is not set")
    rows = read_csv(panel_path)
    validate_unique_key_rows(rows, ["rebalance_date", "code"], "price/universe panel")
    args._price_universe_panel_rows = rows
    return rows


def panel_row_included(row: dict[str, str], *, panel_path: Path) -> bool:
    included = parse_bool(row.get("included_flag"), default=None)
    if included is None:
        raise ValueError(f"Invalid included_flag in {panel_path}: {row.get('included_flag')!r}")
    return included


def price_universe_panel_stage_rows(
    args: argparse.Namespace,
    rebalance_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    panel_path = getattr(args, "price_universe_panel", None)
    if panel_path is None:
        raise ValueError("--price-universe-panel is required for fast panel universe stages")
    rows = [
        row
        for row in read_price_universe_panel_rows(args)
        if parse_date(row.get("rebalance_date"), field_name="price_universe_panel.rebalance_date") == rebalance_date
    ]
    if not rows:
        raise ValueError(f"No --price-universe-panel rows found for rebalance date {rebalance_date}.")

    universe_rows: list[dict[str, Any]] = []
    exclusion_rows: list[dict[str, Any]] = []
    for row in rows:
        if panel_row_included(row, panel_path=panel_path):
            universe_rows.append({field: row.get(field, "") for field in UNIVERSE_CACHE_FIELDS})
        else:
            exclusion_rows.append(
                {
                    "rebalance_date": row.get("rebalance_date", rebalance_date),
                    "code": row.get("code", ""),
                    "name": row.get("name", ""),
                    "reason": row.get("exclusion_reason", ""),
                    "detail": "",
                }
            )
    return universe_rows, exclusion_rows


def read_factor_score_panel_rows(args: argparse.Namespace) -> list[dict[str, str]]:
    rows = getattr(args, "_factor_score_panel_rows", None)
    if rows is not None:
        return rows
    panel_path = getattr(args, "factor_score_panel", None)
    if not panel_path:
        raise ValueError("factor/score panel requested but --factor-score-panel is not set")
    rows = read_csv(panel_path)
    validate_unique_key_rows(rows, ["rebalance_date", "code"], "factor/score panel")
    args._factor_score_panel_rows = rows
    return rows


def panel_zscore_factors(rows: list[dict[str, str]], config: dict[str, Any]) -> list[str]:
    factors: list[str] = []
    direct_fields = score_direct_fields(config)
    for row in rows:
        for field in row:
            if field in direct_fields and field not in factors:
                factors.append(field)
            elif field.endswith("_z") and field[:-2] not in factors:
                factors.append(field[:-2])
    return factors


def normalize_factor_score_panel_slice(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized = [dict(row) for row in rows]
    included_rows = [row for row in normalized if parse_bool(row.get("included_flag"), default=None)]
    if not included_rows:
        return normalized

    for row in included_rows:
        if not row.get("rank") and row.get("candidate_rank"):
            row["rank"] = row.get("candidate_rank", "")
        if not row.get("composite_score") and row.get("rank_score"):
            row["composite_score"] = row.get("rank_score", "")
        if not row.get("qvm_score") and row.get("composite_score"):
            row["qvm_score"] = row.get("composite_score", "")
        if not row.get("filter_status") and row.get("rank"):
            row["filter_status"] = "pass"
        if "filter_reasons" not in row:
            row["filter_reasons"] = ""
        if "missing_score_components" not in row:
            row["missing_score_components"] = ""

    if all(parse_int(row.get("rank")) is None for row in included_rows):
        score_fields = ["rank_score", "composite_score", "qvm_score"]
        score_field = next(
            (
                field
                for field in score_fields
                if any(parse_float(row.get(field)) is not None for row in included_rows)
            ),
            "",
        )
        if not score_field:
            raise ValueError(
                "factor/score panel requires rank or candidate_rank, or a numeric rank_score/composite_score/qvm_score."
            )
        ranked = sorted(
            [
                row
                for row in included_rows
                if parse_float(row.get(score_field)) is not None
                and str(row.get("filter_status", "pass") or "pass") == "pass"
            ],
            key=lambda row: (-(parse_float(row.get(score_field)) or 0.0), str(row.get("code", ""))),
        )
        for rank, row in enumerate(ranked, start=1):
            row["rank"] = str(rank)
            row.setdefault("filter_status", "pass")
    return normalized


def factor_score_panel_stage_rows(
    args: argparse.Namespace,
    rebalance_date: date,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    panel_path = getattr(args, "factor_score_panel", None)
    if panel_path is None:
        raise ValueError("--factor-score-panel is required for factor/score panel stages")
    rows = [
        row
        for row in read_factor_score_panel_rows(args)
        if parse_date(row.get("rebalance_date"), field_name="factor_score_panel.rebalance_date") == rebalance_date
    ]
    if not rows:
        raise ValueError(f"No --factor-score-panel rows found for rebalance date {rebalance_date}.")
    rows = normalize_factor_score_panel_slice(rows)

    universe_rows: list[dict[str, Any]] = []
    factor_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    factor_fields = factor_output_fields(config)
    raw_factors = panel_zscore_factors(rows, config)
    score_fields = score_cache_fields(raw_factors, direct_fields=score_direct_fields(config))
    for row in rows:
        included = parse_bool(row.get("included_flag"), default=None)
        if included is None:
            raise ValueError(f"Invalid included_flag in {panel_path}: {row.get('included_flag')!r}")
        if not included:
            continue
        universe_rows.append({field: row.get(field, "") for field in UNIVERSE_CACHE_FIELDS})
        factor_rows.append({field: row.get(field, "") for field in factor_fields})
        score_rows.append({field: row.get(field, "") for field in score_fields})
    return universe_rows, factor_rows, score_rows, raw_factors


def lifecycle_source_rows(args: argparse.Namespace, fallback_listing_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if getattr(args, "factor_score_panel", None):
        return read_factor_score_panel_rows(args)
    if getattr(args, "price_universe_panel", None):
        return read_price_universe_panel_rows(args)
    return fallback_listing_rows


def run_cached_stages(args: argparse.Namespace, rebalance_date: date) -> tuple[Path, Path, Path]:
    suffix = month_key(rebalance_date)
    strategy_token = strategy_cache_token(args)
    universe_path = cache_path(args, "universe", f"universe_{suffix}")
    exclusions_path = cache_path(args, "universe", f"excluded_{suffix}")
    factors_path = cache_path(args, "factors", f"factors_{suffix}")
    scores_path = cache_path(args, "scores", f"scores_{suffix}_{strategy_token}")

    config = args._config if hasattr(args, "_config") else load_yaml(args.config)
    if getattr(args, "factor_score_panel", None):
        if (
            universe_path.exists()
            and factors_path.exists()
            and scores_path.exists()
            and not args.force_rebuild
        ):
            universe_rows = read_csv(universe_path)
            score_rows = read_csv(scores_path)
        else:
            universe_rows, factor_rows, score_rows, raw_factors = factor_score_panel_stage_rows(
                args,
                rebalance_date,
                config,
            )
            write_table(universe_rows, universe_path, format=args.cache_format or "parquet", fieldnames=UNIVERSE_CACHE_FIELDS)
            write_table([], exclusions_path, format=args.cache_format or "parquet", fieldnames=EXCLUSION_CACHE_FIELDS)
            write_table(factor_rows, factors_path, format=args.cache_format or "parquet", fieldnames=factor_output_fields(config))
            write_table(
                score_rows,
                scores_path,
                format=args.cache_format or "parquet",
                fieldnames=score_cache_fields(raw_factors, direct_fields=score_direct_fields(config)),
            )
        stage_rows = getattr(args, "_stage_rows", {})
        stage_rows[suffix] = {"universe": universe_rows, "scores": score_rows}
        args._stage_rows = stage_rows
        return universe_path, factors_path, scores_path

    listing_rows = args._listing_rows if hasattr(args, "_listing_rows") else read_csv(args.listings)
    price_rows = args._price_rows if hasattr(args, "_price_rows") else read_csv(args.prices)
    fundamental_rows = (
        args._fundamental_rows if hasattr(args, "_fundamental_rows") else read_csv(args.fundamentals)
    )

    if universe_path.exists() and not args.force_rebuild:
        universe_rows = read_csv(universe_path)
    else:
        if getattr(args, "price_universe_panel", None):
            universe_rows, exclusion_rows = price_universe_panel_stage_rows(args, rebalance_date)
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
            fieldnames=score_cache_fields(raw_factors, direct_fields=score_direct_fields(config)),
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
    universe_path = Path(f"data/processed/universe/universe_{suffix}.csv")
    exclusions_path = Path(f"data/processed/universe/excluded_{suffix}.csv")
    if getattr(args, "factor_score_panel", None):
        config = args._config if hasattr(args, "_config") else load_yaml(args.config)
        universe_rows, factor_rows, score_rows, raw_factors = factor_score_panel_stage_rows(
            args,
            rebalance_date,
            config,
        )
        factors_path = Path(f"data/processed/factors/factors_{suffix}.csv")
        scores_path = Path(f"data/processed/scores/scores_{suffix}.csv")
        write_csv(universe_path, universe_rows, UNIVERSE_CACHE_FIELDS)
        write_csv(exclusions_path, [], EXCLUSION_CACHE_FIELDS)
        write_csv(factors_path, factor_rows, factor_output_fields(config))
        write_csv(scores_path, score_rows, score_cache_fields(raw_factors, direct_fields=score_direct_fields(config)))
        return universe_path, factors_path, scores_path
    if getattr(args, "price_universe_panel", None):
        universe_rows, exclusion_rows = price_universe_panel_stage_rows(args, rebalance_date)
        write_csv(universe_path, universe_rows, UNIVERSE_CACHE_FIELDS)
        write_csv(exclusions_path, exclusion_rows, EXCLUSION_CACHE_FIELDS)
    else:
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
            f"| last execution date | {first.get('last_execution_date', '')} |",
            f"| execution lag days | {first.get('execution_lag_days', '')} |",
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
            f"| sector cap enabled | {first.get('sector_cap_enabled', '')} |",
            f"| sector cap mode | {first.get('sector_cap_mode', '')} |",
            f"| sector cap group field | {first.get('sector_cap_group_field', '')} |",
            f"| sector cap limit | {first.get('sector_cap_limit', '')} |",
            f"| affordable lot filter enabled | {first.get('affordable_lot_filter_enabled', '')} |",
            f"| max single lot weight | {first.get('max_single_lot_weight', '')} |",
            f"| cash buffer weight | {first.get('cash_buffer_weight', '')} |",
            f"| margin enabled | {first.get('margin_enabled', '')} |",
            f"| margin account type | {first.get('margin_account_type', '')} |",
            f"| target gross leverage | {first.get('target_gross_leverage', '')} |",
            f"| effective target gross leverage | {first.get('effective_target_gross_leverage', '')} |",
            f"| max gross leverage | {first.get('max_gross_leverage_config', '')} |",
            f"| annual borrow rate | {first.get('annual_borrow_rate', '')} |",
            f"| margin call action | {first.get('margin_call_action', '')} |",
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
        *(
            [
                f"- cumulative financing cost: {money(float(final.get('financing_cost_cumulative') or 0))}",
                f"- min margin ratio: {pct(parse_float(final.get('min_margin_ratio'), default=0) or 0)}",
                f"- margin breach count: {final.get('margin_breach_count', '')}",
            ]
            if str(final.get("margin_enabled", "")).lower() == "true"
            else []
        ),
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
        f"| last execution date | {final.get('last_execution_date', '')} |",
        f"| pending orders | {final.get('pending_order_count', '')} |",
        f"| filled orders | {final.get('filled_order_count', '')} |",
        f"| unexecuted orders | {final.get('unexecuted_order_count', '')} |",
        f"| missing execution price | {final.get('missing_execution_price_count', '')} |",
        f"| missing execution price row | {final.get('missing_execution_price_row_count', '')} |",
        f"| execution date not tradable | {final.get('execution_date_not_tradable_count', '')} |",
        f"| execution price unavailable | {final.get('execution_price_unavailable_on_execution_date_count', '')} |",
        f"| sector cap blocked candidates | {final.get('sector_cap_blocked_candidates', '')} |",
        f"| sector cap unfilled slots | {final.get('sector_cap_unfilled_slots', '')} |",
        f"| max selected sector weight | {pct(parse_float(final.get('max_sector_weight_selected'), default=0) or 0)} |",
        f"| max actual sector weight | {pct(parse_float(final.get('max_sector_weight_actual'), default=0) or 0)} |",
        f"| sector cap violations | {final.get('sector_cap_violation_count', '')} |",
        f"| affordability excluded | {final.get('affordability_excluded', '')} |",
        f"| zero-lot avoided | {final.get('zero_lot_avoided', '')} |",
        f"| small-account path dependency | {final.get('small_account_path_dependency_flag', '')} |",
        f"| zero-lot targets | {final['zero_lot_targets']} |",
        f"| holdings count | {final['holdings_count']} |",
        f"| cash | {money(float(final['cash']))} |",
        f"| cash pct | {pct(float(final['cash_pct']))} |",
        f"| gross exposure | {money(float(final.get('gross_exposure') or 0))} |",
        f"| borrowed value | {money(float(final.get('borrowed_value') or 0))} |",
        f"| gross leverage | {final.get('gross_leverage', '')} |",
        f"| financing cost period | {money(float(final.get('financing_cost_period') or 0))} |",
        f"| after-tax taxable equity | {money(float(final['after_tax_taxable_equity']))} |",
        f"| buys | {final['buy_trades']} |",
        f"| sells | {final['sell_trades']} |",
        f"| skipped orders | {final['skipped_orders']} |",
        "",
        *(
            [
                "## Small-Account Path Dependency",
                "",
                "Lot size, cash, costs, taxes, ADV caps, and affordability filters can change future holdings and cash. Cost scenarios should be read as path-dependent simulations, not as monotonic one-line cost sensitivities.",
                "",
            ]
            if any(str(row.get("small_account_path_dependency_flag", "")).lower() == "true" for row in summary_rows)
            else []
        ),
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

    lifecycle_rows_for_check = lifecycle_source_rows(args, listing_rows_for_check)
    lifecycle_status = lifecycle_data_status(lifecycle_rows_for_check)
    conclusion_allowed = performance_conclusion_allowed(lifecycle_status)
    if lifecycle_status == "snapshot_only" and not args.allow_snapshot_listings:
        raise ValueError(
            "Listings look snapshot-only: listed_date is missing or listing_lifecycle_status marks missing lifecycle dates. "
            "This creates survivorship bias in historical walk-forward runs. Provide PIT lifecycle listings or pass "
            "--allow-snapshot-listings for exploratory samples only."
        )

    delisting_dates = build_delisting_index(lifecycle_rows_for_check)
    raw_price_status_index = build_raw_price_status_index(price_rows)
    execution_calendar = all_raw_price_dates(price_rows)
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
    execution_diagnostics = execution_diagnostics_config(config)
    margin = margin_config(config)
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
    previous_valuation_date: date | None = None
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
    sector_exposure_output_rows: list[dict[str, Any]] = []
    execution_diagnostics_rows: list[dict[str, Any]] = []
    margin_daily_rows: list[dict[str, Any]] = []
    cumulative_financing_cost = 0.0
    warned_price_tail_gaps: set[tuple[str, date]] = set()

    for rebalance_date in dates:
        financing_cost_period = 0.0
        universe_path, _factors_path, scores_path = run_stages(args, rebalance_date)
        stage_rows = getattr(args, "_stage_rows", {}).get(month_key(rebalance_date), {})
        universe_rows = stage_rows.get("universe") or read_csv(universe_path)
        scores = stage_rows.get("scores") or read_csv(scores_path)
        universe_by_code = {row["code"]: row for row in universe_rows}

        if previous_valuation_date is not None:
            period_margin_rows = margin_daily_rows_for_period(
                start_date=previous_valuation_date,
                end_date=rebalance_date,
                rebalance_date=rebalance_date,
                holdings=holdings,
                cash=cash,
                price_index=price_index,
                price_calendar=price_calendar,
                config=margin,
            )
            margin_daily_rows.extend(period_margin_rows)
            period_borrowed_value = max(
                (parse_float(row.get("borrowed_value"), default=0.0) or 0.0 for row in period_margin_rows),
                default=0.0,
            )
            days_held = max((rebalance_date - previous_valuation_date).days, 0)
            financing_cost_period = financing_cost_for_period(period_borrowed_value, margin, days_held)
            if financing_cost_period:
                cash -= financing_cost_period
                cumulative_financing_cost += financing_cost_period
                failure_rows.append(
                    {
                        "date": rebalance_date,
                        "code": "",
                        "failure_type": "financing_cost_drag",
                        "detail": (
                            f"borrowed_value={period_borrowed_value};"
                            f"annual_borrow_rate={margin.annual_borrow_rate};"
                            f"days_held={days_held};"
                            f"interest_day_count={margin.interest_day_count}"
                        ),
                        "value": financing_cost_period,
                    }
                )
            for item in period_margin_rows:
                if parse_bool(item.get("maintenance_margin_breach"), default=False):
                    failure_rows.append(
                        {
                            "date": item["date"],
                            "code": "",
                            "failure_type": "margin_call_flag",
                            "detail": (
                                f"margin_ratio={item['margin_ratio']};"
                                f"maintenance_margin_requirement={margin.maintenance_margin_requirement};"
                                "margin_call_action=flag_only"
                            ),
                            "value": item["gross_exposure"],
                        }
                    )
                if parse_bool(item.get("minimum_equity_breach"), default=False):
                    failure_rows.append(
                        {
                            "date": item["date"],
                            "code": "",
                            "failure_type": "minimum_equity_breach",
                            "detail": (
                                f"account_equity={item['account_equity']};"
                                f"minimum_required_equity={margin.minimum_required_equity}"
                            ),
                            "value": item["account_equity"],
                        }
                    )

        for code, adjusted_shares in list(holdings.items()):
            lifecycle_exit_date = delisting_dates.get(code)
            terminal_point = price_at(price_index, code, lifecycle_exit_date or rebalance_date)
            if lifecycle_exit_date is None or lifecycle_exit_date > rebalance_date:
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
                                    f"policy={tail_gap_mode}; no lifecycle_exit_date in listings"
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
                    "detail": f"lifecycle_exit_date={lifecycle_exit_date}; recovery_price=0",
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

        selection = select_codes_detailed(
            scores,
            holdings,
            config,
            universe_by_code,
            price_index=price_index,
            rebalance_date=rebalance_date,
            equity=pre_equity,
            margin=margin,
        )
        selected_codes = selection.selected_codes
        research_codes = selection.research_codes
        failure_rows.extend(sector_cap_failure_rows(rebalance_date, selection))
        failure_rows.extend(affordable_lot_failure_rows(rebalance_date, selection))
        targets = build_targets(
            selected_codes,
            universe_by_code,
            price_index,
            rebalance_date,
            pre_equity,
            selection.affordable_lot_filter,
            margin,
        )
        if leverage_cap_was_reduced(margin):
            failure_rows.append(
                {
                    "date": rebalance_date,
                    "code": "",
                    "failure_type": "leverage_cap_reduction",
                    "detail": (
                        f"target_gross_leverage={margin.target_gross_leverage};"
                        f"effective_target_gross_leverage={effective_target_gross_leverage(margin)};"
                        f"max_gross_leverage={margin.max_gross_leverage};"
                        f"initial_margin_requirement={margin.initial_margin_requirement}"
                    ),
                    "value": effective_target_gross_leverage(margin),
                }
            )
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
        pending_order_count = 0
        filled_order_count = 0
        missing_execution_price_count = 0
        missing_execution_price_row_count = 0
        execution_date_not_tradable_count = 0
        execution_price_unavailable_on_execution_date_count = 0
        turnover_value = 0.0
        buy_turnover_value = 0.0
        sell_turnover_value = 0.0
        estimated_cost_base = 0.0
        period_estimated_tax = 0.0
        adv_cap_reduction_count = 0
        last_execution_date: date | None = None
        intended_execution_date = (
            rebalance_date
            if args.execution_price == "rebalance_close"
            else next_trading_date(execution_calendar, rebalance_date)
        )

        for code in all_codes:
            current_adjusted_shares = holdings.get(code, 0.0)
            signal_target_shares = targets.get(code, 0)
            position_point = price_at(price_index, code, rebalance_date)
            signal_point = price_on_date(price_index, code, rebalance_date)
            fill_point = execution_point(price_index, execution_calendar, code, rebalance_date, args.execution_price)
            raw_execution_status = (
                raw_price_status_index.get((code, intended_execution_date))
                if intended_execution_date is not None
                else None
            )
            execution_failure_type = (
                execution_price_failure_type(
                    mode=args.execution_price,
                    intended_execution_date=intended_execution_date,
                    raw_status=raw_execution_status,
                    fill_point=fill_point,
                )
                if args.execution_price != "rebalance_close"
                else None
            )
            signal_current_shares = (
                int(round(actual_shares_from_adjusted(current_adjusted_shares, position_point)))
                if position_point
                else 0
            )
            signal_desired_delta = signal_target_shares - signal_current_shares
            if signal_desired_delta == 0:
                continue
            if args.execution_price != "rebalance_close":
                pending_order_count += 1
            if not signal_point or execution_failure_type is not None or not fill_point:
                skipped_orders += 1
                failure_reason = (
                    "missing_signal_price"
                    if not signal_point
                    else execution_failure_type or "missing_execution_price"
                )
                if failure_reason in EXECUTION_PRICE_FAILURE_TYPES:
                    missing_execution_price_count += 1
                if failure_reason == "missing_execution_price_row":
                    missing_execution_price_row_count += 1
                if failure_reason == "execution_date_not_tradable":
                    execution_date_not_tradable_count += 1
                if failure_reason == "execution_price_unavailable_on_execution_date":
                    execution_price_unavailable_on_execution_date_count += 1
                failure_date = (
                    intended_execution_date
                    if failure_reason in EXECUTION_PRICE_FAILURE_TYPES and intended_execution_date is not None
                    else rebalance_date
                )
                failure_rows.append(
                    {
                        "date": failure_date,
                        "code": code,
                        "failure_type": failure_reason,
                        "detail": (
                            execution_price_failure_detail(
                                mode=args.execution_price,
                                intended_execution_date=intended_execution_date,
                                raw_status=raw_execution_status,
                            )
                            if failure_reason in SPECIFIC_EXECUTION_PRICE_FAILURE_TYPES
                            else (
                                f"execution_price={args.execution_price};"
                                f"intended_execution_date={intended_execution_date or ''}"
                            )
                        ),
                        "value": 0,
                    }
                )
                trade_rows.append(
                    {
                        "signal_date": rebalance_date,
                        "execution_date": "",
                        "code": code,
                        "side": "SKIP",
                        "requested_shares": signal_desired_delta,
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
            current_shares = int(round(actual_shares_from_adjusted(current_adjusted_shares, fill_point)))
            target_shares = retarget_actual_shares_for_fill(signal_target_shares, signal_point, fill_point)
            desired_delta = target_shares - current_shares
            if desired_delta == 0:
                continue
            requested_value = abs(desired_delta * fill_price)
            filled_delta = desired_delta
            reason = ""
            adv_cap_value = median_adv * max_order_to_adv if median_adv else None
            if adv_cap_value is not None and requested_value > adv_cap_value:
                adv_cap_reduction_count += 1
                filled_lots = int(adv_cap_value // (fill_price * lot)) * lot
                filled_delta = filled_lots if desired_delta > 0 else -filled_lots
                reason = "reduced_by_adv_cap"
                failure_rows.append(
                    {
                        "date": fill_point.date,
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
                        "date": fill_point.date,
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
            available_buying_power = cash
            if margin.enabled and filled_delta > 0:
                gross_before_fill = 0.0
                for held_code, adjusted_shares in holdings.items():
                    mark_point = price_at(price_index, held_code, fill_point.date)
                    if mark_point is None:
                        continue
                    gross_before_fill += position_value(adjusted_shares, mark_point)
                available_buying_power = margin_available_buying_power(cash, gross_before_fill, margin)
            if filled_delta > 0 and trade_value + cost > available_buying_power:
                affordable_shares = floor_lot(max(available_buying_power, 0), fill_price, lot)
                filled_delta = min(filled_delta, affordable_shares)
                trade_value = abs(filled_delta * fill_price)
                scenario_costs = {
                    scenario: estimate_cost(trade_value, fill_price, median_adv, config, scenario)
                    for scenario in ["optimistic", "base", "pessimistic"]
                }
                cost = scenario_costs[args.cost_scenario]
                while filled_delta > 0 and trade_value + cost > available_buying_power:
                    filled_delta = max(0, filled_delta - lot)
                    trade_value = abs(filled_delta * fill_price)
                    scenario_costs = {
                        scenario: estimate_cost(trade_value, fill_price, median_adv, config, scenario)
                        for scenario in ["optimistic", "base", "pessimistic"]
                    }
                    cost = scenario_costs[args.cost_scenario]
                reason = add_reason(
                    reason,
                    "reduced_by_leverage_cap" if margin.enabled and filled_delta else (
                        "insufficient_leverage_capacity" if margin.enabled else (
                            "reduced_by_cash" if filled_delta else "insufficient_cash"
                        )
                    ),
                )
                failure_rows.append(
                    {
                        "date": fill_point.date,
                        "code": code,
                        "failure_type": reason,
                        "detail": (
                            f"cash={cash:.2f};available_buying_power={available_buying_power:.2f};"
                            f"margin_enabled={margin.enabled}"
                        ),
                        "value": trade_value,
                    }
                )
            mark_price_limit = bool(config["execution"].get("mark_uncertain_fill_on_price_limit", False))
            if mark_price_limit and filled_delta != 0 and fill_point.price_limit_flag:
                reason = add_reason(reason, "price_limit_uncertain_fill")
                failure_rows.append(
                    {
                        "date": fill_point.date,
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
                    period_estimated_tax += estimated_tax
                cumulative_realized_gain += realized_gain
                sell_trades += 1
            holdings[code] = current_adjusted_shares + adjusted_shares_for_trade(filled_delta, fill_point)
            if abs(holdings.get(code, 0.0)) <= 1e-9:
                holdings.pop(code, None)
            if filled_delta != 0:
                filled_order_count += 1
                last_execution_date = max(last_execution_date or fill_point.date, fill_point.date)
                if filled_delta > 0:
                    buy_turnover_value += trade_value
                elif filled_delta < 0:
                    sell_turnover_value += trade_value
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
        valuation_date = last_execution_date or rebalance_date
        for code, adjusted_shares in sorted(holdings.items()):
            point = price_at(price_index, code, valuation_date)
            if not point:
                continue
            value = position_value(adjusted_shares, point)
            post_holdings_value += value
        after_equity = cash + post_holdings_value
        current_margin_snapshot = margin_snapshot(post_holdings_value, after_equity, margin)
        if margin.enabled and (previous_valuation_date is None or rebalance_date == dates[-1]):
            margin_daily_rows.append(
                {
                    "date": valuation_date,
                    "rebalance_date": rebalance_date,
                    "gross_exposure": current_margin_snapshot["gross_exposure"],
                    "borrowed_value": current_margin_snapshot["borrowed_value"],
                    "account_equity": current_margin_snapshot["net_account_equity"],
                    "gross_leverage": current_margin_snapshot["gross_leverage"],
                    "margin_ratio": current_margin_snapshot["margin_ratio"],
                    "maintenance_margin_breach": current_margin_snapshot["maintenance_margin_breach"],
                    "minimum_equity_breach": current_margin_snapshot["minimum_equity_breach"],
                }
            )
        if margin.enabled and current_margin_snapshot["maintenance_margin_breach"]:
            failure_rows.append(
                {
                    "date": valuation_date,
                    "code": "",
                    "failure_type": "margin_call_flag",
                    "detail": (
                        f"margin_ratio={current_margin_snapshot['margin_ratio']};"
                        f"maintenance_margin_requirement={margin.maintenance_margin_requirement};"
                        "margin_call_action=flag_only"
                    ),
                    "value": current_margin_snapshot["gross_exposure"],
                }
            )
        if margin.enabled and current_margin_snapshot["minimum_equity_breach"]:
            failure_rows.append(
                {
                    "date": valuation_date,
                    "code": "",
                    "failure_type": "minimum_equity_breach",
                    "detail": (
                        f"account_equity={current_margin_snapshot['net_account_equity']};"
                        f"minimum_required_equity={margin.minimum_required_equity}"
                    ),
                    "value": current_margin_snapshot["net_account_equity"],
                }
            )
        scenario_equity = {
            scenario: after_equity
            + cumulative_cost_by_scenario[args.cost_scenario]
            - cumulative_cost_by_scenario[scenario]
            for scenario in ["optimistic", "base", "pessimistic"]
        }
        after_tax_taxable_equity = after_equity - cumulative_tax
        if previous_valuation_date is not None:
            benchmark_return = mean_return(
                price_index,
                previous_benchmark_codes,
                previous_valuation_date,
                valuation_date,
                delisting_dates,
            )
            research_return = mean_return(
                price_index,
                previous_research_codes,
                previous_valuation_date,
                valuation_date,
                delisting_dates,
            )
            if benchmark_return is not None:
                benchmark_equity *= 1 + benchmark_return
            if research_return is not None:
                research_equity *= 1 + research_return
            market_period_return = (
                market_benchmark_return(market_benchmark_points, previous_valuation_date, valuation_date)
                if market_benchmark_points
                else None
            )
            if market_period_return is not None:
                market_benchmark_equity *= 1 + market_period_return
        else:
            market_period_return = 0.0 if market_benchmark_points else None
        (
            current_sector_exposure_rows,
            max_sector_weight_selected,
            max_sector_weight_actual,
            sector_cap_violation_count,
            sector_cap_violation_rows,
        ) = sector_exposure_rows(
            signal_date=rebalance_date,
            valuation_date=valuation_date,
            result=selection,
            targets=targets,
            holdings=holdings,
            universe_by_code=universe_by_code,
            price_index=price_index,
            pre_equity=pre_equity,
            after_equity=after_equity,
        )
        sector_exposure_output_rows.extend(current_sector_exposure_rows)
        failure_rows.extend(sector_cap_violation_rows)
        for code, adjusted_shares in sorted(holdings.items()):
            point = price_at(price_index, code, valuation_date)
            if not point:
                continue
            actual_shares = actual_shares_from_adjusted(adjusted_shares, point)
            value = position_value(adjusted_shares, point)
            holdings_rows.append(
                {
                    "date": valuation_date,
                    "code": code,
                    "shares": display_shares(actual_shares),
                    "price": point.unadjusted_close,
                    "value": value,
                    "weight": value / after_equity if after_equity else 0,
                }
            )

        portfolio_return = 0.0 if previous_after_equity is None else after_equity / previous_after_equity - 1.0
        cash_pct = cash / after_equity if after_equity else 0
        target_slots_filled_ratio = len(selected_codes) / selection.target_count if selection.target_count else 0
        selected_but_untradeable_count = sum(
            1
            for code in selected_codes
            if parse_bool(universe_by_code.get(code, {}).get("tradable_flag"), default=True) is False
        )
        selected_lot_values = [
            value
            for value in [
                single_lot_value_at(code, universe_by_code, price_index, rebalance_date)
                for code in selected_codes
            ]
            if value is not None
        ]
        skipped_lot_values = [
            item.single_lot_value
            for item in selection.affordability_excluded
            if item.single_lot_value is not None
        ]
        selected_lot_stats = distribution_stats(selected_lot_values, "selected_lot_value")
        skipped_lot_stats = distribution_stats(skipped_lot_values, "skipped_lot_value")
        period_cost_drag = estimated_cost_base / pre_equity if pre_equity else 0
        period_tax_drag = period_estimated_tax / pre_equity if pre_equity else 0
        high_cash_flag = cash_pct > execution_diagnostics.high_cash_threshold
        small_account_path_dependency_flag = bool(
            selection.affordable_lot_filter.enabled
            or zero_lot_targets
            or selection.affordability_excluded
            or adv_cap_reduction_count
        )
        small_account_path_dependency_detail = (
            "lot_size_cost_cash_path_can_change_future_affordability_and_holdings"
            if small_account_path_dependency_flag
            else ""
        )
        if cash_pct > 0.2:
            failure_rows.append(
                {
                    "date": valuation_date,
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
            "last_execution_date": last_execution_date or "",
            "execution_lag_days": (
                trading_staleness_days(price_calendar, rebalance_date, last_execution_date)
                if last_execution_date is not None and last_execution_date >= rebalance_date
                else 0
            ),
            "pending_order_count": pending_order_count,
            "filled_order_count": filled_order_count,
            "unexecuted_order_count": skipped_orders,
            "missing_execution_price_count": missing_execution_price_count,
            "missing_execution_price_row_count": missing_execution_price_row_count,
            "execution_date_not_tradable_count": execution_date_not_tradable_count,
            "execution_price_unavailable_on_execution_date_count": execution_price_unavailable_on_execution_date_count,
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
            "margin_enabled": margin.enabled,
            "margin_account_type": margin.account_type if margin.enabled else "",
            "target_gross_leverage": margin.target_gross_leverage if margin.enabled else "",
            "effective_target_gross_leverage": effective_target_gross_leverage(margin) if margin.enabled else "",
            "max_gross_leverage_config": margin.max_gross_leverage if margin.enabled else "",
            "annual_borrow_rate": margin.annual_borrow_rate if margin.enabled else "",
            "initial_margin_requirement": margin.initial_margin_requirement if margin.enabled else "",
            "maintenance_margin_requirement": margin.maintenance_margin_requirement if margin.enabled else "",
            "minimum_required_equity": margin.minimum_required_equity if margin.enabled else "",
            "margin_call_action": margin.margin_call_action if margin.enabled else "",
            "gross_exposure": current_margin_snapshot["gross_exposure"] if margin.enabled else 0,
            "borrowed_value": current_margin_snapshot["borrowed_value"] if margin.enabled else 0,
            "net_account_equity": current_margin_snapshot["net_account_equity"] if margin.enabled else after_equity,
            "gross_leverage": current_margin_snapshot["gross_leverage"] if margin.enabled else 1.0,
            "margin_ratio": current_margin_snapshot["margin_ratio"] if margin.enabled else "",
            "financing_cost_period": financing_cost_period if margin.enabled else 0,
            "financing_cost_cumulative": cumulative_financing_cost if margin.enabled else 0,
            "portfolio_equity_after_cost_after_financing": after_equity,
            "min_margin_ratio": "",
            "margin_breach_count": "",
            "first_margin_breach_date": "",
            "minimum_equity_breach_count": "",
            "max_margin_gross_leverage": "",
            "avg_margin_gross_leverage": "",
            "max_borrowed_value": "",
            "sector_cap_enabled": selection.sector_cap.enabled,
            "sector_cap_mode": selection.sector_cap.mode if selection.sector_cap.enabled else "",
            "sector_cap_group_field": selection.sector_cap.group_field if selection.sector_cap.enabled else "",
            "sector_cap_limit": sector_cap_limit_value(selection.sector_cap),
            "sector_cap_blocked_candidates": len(selection.blocked_candidates),
            "sector_cap_unfilled_slots": selection.unfilled_slots,
            "max_sector_weight_selected": max_sector_weight_selected,
            "max_sector_weight_actual": max_sector_weight_actual,
            "sector_cap_violation_count": sector_cap_violation_count,
            "affordable_lot_filter_enabled": selection.affordable_lot_filter.enabled,
            "max_single_lot_weight": (
                selection.affordable_lot_filter.max_single_lot_weight
                if selection.affordable_lot_filter.enabled
                else ""
            ),
            "min_single_lot_weight": (
                selection.affordable_lot_filter.min_single_lot_weight
                if selection.affordable_lot_filter.enabled
                and selection.affordable_lot_filter.min_single_lot_weight is not None
                else ""
            ),
            "cash_buffer_weight": (
                selection.affordable_lot_filter.cash_buffer_weight
                if selection.affordable_lot_filter.enabled
                else ""
            ),
            "affordability_excluded": len(selection.affordability_excluded),
            "zero_lot_avoided": sum(1 for item in selection.affordability_excluded if item.reason == "zero_lot_avoided"),
            "execution_diagnostics_enabled": execution_diagnostics.enabled,
            "high_cash_threshold": execution_diagnostics.high_cash_threshold if execution_diagnostics.enabled else "",
            "high_cash_flag": high_cash_flag if execution_diagnostics.enabled else "",
            "average_cash_weight": "",
            "max_cash_weight": "",
            "periods_with_cash_weight_above_threshold": "",
            "target_slots_filled_ratio": target_slots_filled_ratio,
            "selected_but_untradeable_count": selected_but_untradeable_count,
            "selected_but_unaffordable_count": len(selection.affordability_excluded),
            "skipped_due_to_affordable_lot_count": len(selection.affordability_excluded),
            "skipped_due_to_adv_cap_count": adv_cap_reduction_count,
            "small_account_path_dependency_flag": small_account_path_dependency_flag,
            "small_account_path_dependency_detail": small_account_path_dependency_detail,
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
            "buy_turnover": buy_turnover_value / pre_equity if pre_equity else 0,
            "sell_turnover": sell_turnover_value / pre_equity if pre_equity else 0,
            "estimated_cost_base": estimated_cost_base,
            "period_cost_drag": period_cost_drag,
            "period_tax_drag": period_tax_drag,
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
        if execution_diagnostics.enabled:
            execution_diagnostics_rows.append(
                {
                    "rebalance_date": rebalance_date,
                    "valuation_date": valuation_date,
                    "execution_price": args.execution_price,
                    "cash_weight": cash_pct,
                    "high_cash_threshold": execution_diagnostics.high_cash_threshold,
                    "high_cash_flag": high_cash_flag,
                    "selected_count": len(selected_codes),
                    "target_holdings": row["target_holdings"],
                    "holdings_count": len(holdings),
                    "target_slots_filled_ratio": target_slots_filled_ratio,
                    "selected_but_untradeable_count": selected_but_untradeable_count,
                    "selected_but_unaffordable_count": len(selection.affordability_excluded),
                    "skipped_due_to_affordable_lot_count": len(selection.affordability_excluded),
                    "skipped_due_to_adv_cap_count": adv_cap_reduction_count,
                    "small_account_path_dependency_flag": small_account_path_dependency_flag,
                    "small_account_path_dependency_detail": small_account_path_dependency_detail,
                    "pending_order_count": pending_order_count,
                    "filled_order_count": filled_order_count,
                    "skipped_orders": skipped_orders,
                    "buy_turnover": row["buy_turnover"],
                    "sell_turnover": row["sell_turnover"],
                    "turnover": row["turnover"],
                    "estimated_cost_base": estimated_cost_base,
                    "period_cost_drag": period_cost_drag,
                    "period_tax_drag": period_tax_drag,
                    "cash_drag": cash_pct,
                    **selected_lot_stats,
                    **skipped_lot_stats,
                    "average_cash_weight": "",
                    "max_cash_weight": "",
                    "periods_with_cash_weight_above_threshold": "",
                    "realized_holdings_count_avg": "",
                    "realized_holdings_count_min": "",
                    "realized_holdings_count_max": "",
                }
            )
        equity_rows.append(
            {
                "date": valuation_date,
                "rebalance_date": rebalance_date,
                "last_execution_date": last_execution_date or "",
                "portfolio_equity_after_cost": after_equity,
                "portfolio_equity_after_cost_after_financing": after_equity,
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
        previous_valuation_date = valuation_date
        previous_benchmark_codes = [row["code"] for row in universe_rows]
        previous_research_codes = research_codes

    if execution_diagnostics.enabled and summary_rows:
        cash_weights = [float(row.get("cash_pct", 0) or 0) for row in summary_rows]
        holding_counts = [int(row.get("holdings_count", 0) or 0) for row in summary_rows]
        average_cash_weight = sum(cash_weights) / len(cash_weights)
        max_cash_weight = max(cash_weights)
        high_cash_periods = sum(1 for value in cash_weights if value > execution_diagnostics.high_cash_threshold)
        realized_holdings_count_avg = sum(holding_counts) / len(holding_counts)
        realized_holdings_count_min = min(holding_counts)
        realized_holdings_count_max = max(holding_counts)
        for row in summary_rows:
            row["average_cash_weight"] = average_cash_weight
            row["max_cash_weight"] = max_cash_weight
            row["periods_with_cash_weight_above_threshold"] = high_cash_periods
        for row in execution_diagnostics_rows:
            row["average_cash_weight"] = average_cash_weight
            row["max_cash_weight"] = max_cash_weight
            row["periods_with_cash_weight_above_threshold"] = high_cash_periods
            row["realized_holdings_count_avg"] = realized_holdings_count_avg
            row["realized_holdings_count_min"] = realized_holdings_count_min
            row["realized_holdings_count_max"] = realized_holdings_count_max

    margin_summary_rows: list[dict[str, Any]] = []
    if margin.enabled and summary_rows:
        stats = margin_summary_stats(margin_daily_rows, cumulative_financing_cost)
        for row in summary_rows:
            row.update(stats)
        margin_summary_rows.append(
            {
                "start_date": dates[0],
                "end_date": dates[-1],
                "account_type": margin.account_type,
                "target_gross_leverage": margin.target_gross_leverage,
                "effective_target_gross_leverage": effective_target_gross_leverage(margin),
                "max_gross_leverage_config": margin.max_gross_leverage,
                "annual_borrow_rate": margin.annual_borrow_rate,
                "initial_margin_requirement": margin.initial_margin_requirement,
                "maintenance_margin_requirement": margin.maintenance_margin_requirement,
                "minimum_required_equity": margin.minimum_required_equity,
                "margin_call_action": margin.margin_call_action,
                **stats,
            }
        )

    start_suffix = month_key(dates[0])
    end_suffix = month_key(dates[-1])
    parameter_label = []
    if args.target_holdings is not None:
        parameter_label.append(f"target{args.target_holdings}")
    if args.adv_cap is not None:
        parameter_label.append(f"adv{str(args.adv_cap).replace('.', 'p')}")
    label_sector_cap = sector_cap_config(config)
    if label_sector_cap.enabled and label_sector_cap.mode == "name_count":
        parameter_label.append(
            f"sectorcap{label_sector_cap.group_field}{label_sector_cap.max_names_per_group}"
        )
    label_affordable_filter = affordable_lot_filter_config(config)
    if label_affordable_filter.enabled:
        parameter_label.append(
            f"afflot{str(label_affordable_filter.max_single_lot_weight).replace('.', 'p')}"
        )
    label_margin = margin_config(config)
    if label_margin.enabled:
        parameter_label.append(f"margin{str(effective_target_gross_leverage(label_margin)).replace('.', 'p')}")
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
    sector_exposure_path = args.out_dir / f"qvm_walkforward_sector_exposure_{token}.csv"
    execution_diagnostics_path = args.out_dir / f"qvm_walkforward_execution_diagnostics_{token}.csv"
    margin_daily_path = args.out_dir / f"qvm_walkforward_margin_daily_{token}.csv"
    margin_summary_path = args.out_dir / f"qvm_walkforward_margin_summary_{token}.csv"
    report_path = args.report_dir / f"qvm_walkforward_{token}.md"

    write_csv(
        summary_path,
        summary_rows,
        [
            "rebalance_date",
            "strategy_version",
            "frequency",
            "execution_price",
            "last_execution_date",
            "execution_lag_days",
            "pending_order_count",
            "filled_order_count",
            "unexecuted_order_count",
            "missing_execution_price_count",
            "missing_execution_price_row_count",
            "execution_date_not_tradable_count",
            "execution_price_unavailable_on_execution_date_count",
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
            "margin_enabled",
            "margin_account_type",
            "target_gross_leverage",
            "effective_target_gross_leverage",
            "max_gross_leverage_config",
            "annual_borrow_rate",
            "initial_margin_requirement",
            "maintenance_margin_requirement",
            "minimum_required_equity",
            "margin_call_action",
            "gross_exposure",
            "borrowed_value",
            "net_account_equity",
            "gross_leverage",
            "margin_ratio",
            "financing_cost_period",
            "financing_cost_cumulative",
            "portfolio_equity_after_cost_after_financing",
            "min_margin_ratio",
            "margin_breach_count",
            "first_margin_breach_date",
            "minimum_equity_breach_count",
            "max_margin_gross_leverage",
            "avg_margin_gross_leverage",
            "max_borrowed_value",
            "sector_cap_enabled",
            "sector_cap_mode",
            "sector_cap_group_field",
            "sector_cap_limit",
            "sector_cap_blocked_candidates",
            "sector_cap_unfilled_slots",
            "max_sector_weight_selected",
            "max_sector_weight_actual",
            "sector_cap_violation_count",
            "affordable_lot_filter_enabled",
            "max_single_lot_weight",
            "min_single_lot_weight",
            "cash_buffer_weight",
            "affordability_excluded",
            "zero_lot_avoided",
            "execution_diagnostics_enabled",
            "high_cash_threshold",
            "high_cash_flag",
            "average_cash_weight",
            "max_cash_weight",
            "periods_with_cash_weight_above_threshold",
            "target_slots_filled_ratio",
            "selected_but_untradeable_count",
            "selected_but_unaffordable_count",
            "skipped_due_to_affordable_lot_count",
            "skipped_due_to_adv_cap_count",
            "small_account_path_dependency_flag",
            "small_account_path_dependency_detail",
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
            "buy_turnover",
            "sell_turnover",
            "estimated_cost_base",
            "period_cost_drag",
            "period_tax_drag",
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
            "rebalance_date",
            "last_execution_date",
            "portfolio_equity_after_cost",
            "portfolio_equity_after_cost_after_financing",
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
    if sector_exposure_output_rows:
        write_csv(
            sector_exposure_path,
            sector_exposure_output_rows,
            ["date", "group", "selected_count", "target_weight", "actual_weight", "cap_limit", "violation"],
        )
    if execution_diagnostics_rows:
        write_csv(
            execution_diagnostics_path,
            execution_diagnostics_rows,
            [
                "rebalance_date",
                "valuation_date",
                "execution_price",
                "cash_weight",
                "high_cash_threshold",
                "high_cash_flag",
                "selected_count",
                "target_holdings",
                "holdings_count",
                "target_slots_filled_ratio",
                "selected_but_untradeable_count",
                "selected_but_unaffordable_count",
                "skipped_due_to_affordable_lot_count",
                "skipped_due_to_adv_cap_count",
                "small_account_path_dependency_flag",
                "small_account_path_dependency_detail",
                "pending_order_count",
                "filled_order_count",
                "skipped_orders",
                "buy_turnover",
                "sell_turnover",
                "turnover",
                "estimated_cost_base",
                "period_cost_drag",
                "period_tax_drag",
                "cash_drag",
                "selected_lot_value_min",
                "selected_lot_value_median",
                "selected_lot_value_max",
                "skipped_lot_value_min",
                "skipped_lot_value_median",
                "skipped_lot_value_max",
                "average_cash_weight",
                "max_cash_weight",
                "periods_with_cash_weight_above_threshold",
                "realized_holdings_count_avg",
                "realized_holdings_count_min",
                "realized_holdings_count_max",
            ],
        )
    if margin.enabled:
        write_csv(
            margin_daily_path,
            margin_daily_rows,
            [
                "date",
                "rebalance_date",
                "gross_exposure",
                "borrowed_value",
                "account_equity",
                "gross_leverage",
                "margin_ratio",
                "maintenance_margin_breach",
                "minimum_equity_breach",
            ],
        )
        write_csv(
            margin_summary_path,
            margin_summary_rows,
            [
                "start_date",
                "end_date",
                "account_type",
                "target_gross_leverage",
                "effective_target_gross_leverage",
                "max_gross_leverage_config",
                "annual_borrow_rate",
                "initial_margin_requirement",
                "maintenance_margin_requirement",
                "minimum_required_equity",
                "margin_call_action",
                "min_margin_ratio",
                "margin_breach_count",
                "first_margin_breach_date",
                "minimum_equity_breach_count",
                "max_margin_gross_leverage",
                "avg_margin_gross_leverage",
                "max_borrowed_value",
                "cumulative_financing_cost",
            ],
        )
    write_report(report_path, summary_rows, args.capital_jpy)

    if not args.no_manifest:
        date_range = f"{dates[0].isoformat()}..{dates[-1].isoformat()}"
        artifacts = [
            ("derived_walkforward_summary", summary_path, "walkforward_summary_v0_1", len(summary_rows)),
            ("derived_walkforward_trades", trades_path, "walkforward_trades_v0_1", len(trade_rows)),
            ("derived_walkforward_holdings", holdings_path, "walkforward_holdings_v0_1", len(holdings_rows)),
            ("derived_walkforward_equity", equity_path, "walkforward_equity_v0_1", len(equity_rows)),
            ("derived_walkforward_failure_cases", failures_path, "walkforward_failure_cases_v0_1", len(failure_rows)),
            ("derived_walkforward_report", report_path, "walkforward_report_v0_1", 1),
        ]
        if sector_exposure_output_rows:
            artifacts.append(
                (
                    "derived_walkforward_sector_exposure",
                    sector_exposure_path,
                    "walkforward_sector_exposure_v0_1",
                    len(sector_exposure_output_rows),
                )
            )
        if execution_diagnostics_rows:
            artifacts.append(
                (
                    "derived_walkforward_execution_diagnostics",
                    execution_diagnostics_path,
                    "walkforward_execution_diagnostics_v0_1",
                    len(execution_diagnostics_rows),
                )
            )
        if margin.enabled:
            artifacts.extend(
                [
                    (
                        "derived_walkforward_margin_daily",
                        margin_daily_path,
                        "walkforward_margin_daily_v0_1",
                        len(margin_daily_rows),
                    ),
                    (
                        "derived_walkforward_margin_summary",
                        margin_summary_path,
                        "walkforward_margin_summary_v0_1",
                        len(margin_summary_rows),
                    ),
                ]
            )
        for source, path, schema, row_count in artifacts:
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
    if sector_exposure_output_rows:
        print(f"Wrote walk-forward sector exposure to {sector_exposure_path}")
    if execution_diagnostics_rows:
        print(f"Wrote walk-forward execution diagnostics to {execution_diagnostics_path}")
    if margin.enabled:
        print(f"Wrote walk-forward margin daily diagnostics to {margin_daily_path}")
        print(f"Wrote walk-forward margin summary to {margin_summary_path}")
    print(f"Wrote walk-forward report to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
