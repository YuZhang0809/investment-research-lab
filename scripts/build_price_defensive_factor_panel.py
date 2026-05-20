from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from research_common import append_manifest, parse_date, parse_float, read_csv, write_table


TRADING_DAYS_PER_YEAR = 252
WINDOWS = {
    "3m": 63,
    "6m": 126,
    "12m": 252,
}
FIELDNAMES = [
    "rebalance_date",
    "code",
    "latest_price_date",
    "latest_price_stale",
    "price_staleness_trading_days",
    "price_limit_flag",
    "realized_vol_3m",
    "realized_vol_6m",
    "realized_vol_12m",
    "downside_vol_6m",
    "downside_vol_12m",
    "max_drawdown_6m",
    "max_drawdown_12m",
    "beta_to_benchmark",
    "history_observations_3m",
    "history_observations_6m",
    "history_observations_12m",
    "benchmark_observations",
    "defensive_filter_reasons",
    "missing_flags",
]


@dataclass(frozen=True)
class PricePoint:
    date: date
    adjusted_close: float | None
    price_limit_flag: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build generic low-volatility defensive price factor panels.")
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--market-benchmark-prices", type=Path)
    parser.add_argument(
        "--rebalance-dates",
        type=Path,
        help="CSV/Parquet with rebalance_date or date column. Required unless --rebalance-date is repeated.",
    )
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values", help="YYYY-MM-DD; can be repeated.")
    parser.add_argument("--beta-window-days", type=int, default=252)
    parser.add_argument("--min-beta-observations", type=int, default=60)
    parser.add_argument("--stale-filter-days", type=int, help="Add stale_price to defensive_filter_reasons above this trading-day age.")
    parser.add_argument("--flag-price-limit", action="store_true", help="Add price_limit_hit to defensive_filter_reasons when latest row has price_limit_flag=true.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--run-label", default="price_defensive")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def parse_optional_date(value: Any, field_name: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    return parse_date(text, field_name=field_name)


def parse_bool(value: Any, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return default


def first_number(row: dict[str, Any], *fields: str) -> float | None:
    for field in fields:
        value = parse_float(row.get(field))
        if value is not None:
            return value
    return None


def price_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(row.get("date") or row.get("price_date") or row.get("Date"), "prices.date")


def price_code(row: dict[str, Any]) -> str:
    return str(row.get("code") or row.get("Code") or row.get("LocalCode") or "").strip()


def build_price_index(rows: list[dict[str, str]]) -> dict[str, list[PricePoint]]:
    grouped_raw: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, date]] = set()
    for row in rows:
        code = price_code(row)
        row_date = price_date(row)
        if not code or row_date is None:
            continue
        key = (code, row_date)
        if key in seen:
            raise ValueError(f"Duplicate price row for code={code};date={row_date.isoformat()}.")
        seen.add(key)
        grouped_raw[code].append(row)

    output: dict[str, list[PricePoint]] = {}
    for code, values in grouped_raw.items():
        values.sort(key=lambda row: price_date(row) or date.min)
        cumulative_adjustment = 1.0
        points: list[PricePoint] = []
        for row in values:
            adjustment = first_number(row, "adjustment_factor", "AdjustmentFactor")
            if adjustment is not None:
                if adjustment <= 0:
                    raise ValueError(f"adjustment_factor must be positive for code={code};date={price_date(row)}.")
                cumulative_adjustment *= adjustment
            adjusted = first_number(row, "adjusted_close", "AdjustedClose", "AdjC")
            unadjusted = first_number(row, "unadjusted_close", "close", "price", "Close", "C")
            effective = adjusted
            if effective is None and unadjusted is not None and cumulative_adjustment > 0:
                effective = unadjusted / cumulative_adjustment
            points.append(
                PricePoint(
                    date=price_date(row) or date.min,
                    adjusted_close=effective,
                    price_limit_flag=parse_bool(row.get("price_limit_flag") or row.get("PriceLimitFlag")),
                )
            )
        output[code] = points
    return output


def build_benchmark_returns(rows: list[dict[str, str]]) -> dict[date, float]:
    points: list[tuple[date, float]] = []
    for row in rows:
        row_date = parse_optional_date(row.get("date") or row.get("Date"), "benchmark.date")
        value = first_number(row, "adjusted_close", "close", "index_value", "value", "price", "Close", "C")
        if row_date is not None and value is not None and value > 0:
            points.append((row_date, value))
    points.sort(key=lambda item: item[0])
    returns: dict[date, float] = {}
    for index in range(1, len(points)):
        previous = points[index - 1][1]
        current = points[index][1]
        if previous > 0:
            returns[points[index][0]] = current / previous - 1.0
    return returns


def load_rebalance_dates(path: Path | None, values: list[str] | None) -> list[date]:
    dates: list[date] = []
    for value in values or []:
        parsed = parse_optional_date(value, "rebalance_date")
        if parsed is not None:
            dates.append(parsed)
    if path is not None:
        for row in read_csv(path):
            parsed = parse_optional_date(row.get("rebalance_date") or row.get("date"), "rebalance_dates.rebalance_date")
            if parsed is not None:
                dates.append(parsed)
    clean = sorted(set(dates))
    if not clean:
        raise ValueError("--rebalance-date or --rebalance-dates is required.")
    return clean


def latest_point(points: list[PricePoint], rebalance_date: date) -> tuple[int, PricePoint] | None:
    latest: tuple[int, PricePoint] | None = None
    for index, point in enumerate(points):
        if point.date <= rebalance_date:
            latest = (index, point)
        else:
            break
    return latest


def clean_window(points: list[PricePoint], latest_index: int, window_days: int) -> list[PricePoint]:
    start = max(0, latest_index - window_days)
    return [point for point in points[start : latest_index + 1] if point.adjusted_close is not None and point.adjusted_close > 0]


def returns_from_points(points: list[PricePoint]) -> list[float]:
    returns: list[float] = []
    for index in range(1, len(points)):
        previous = points[index - 1].adjusted_close
        current = points[index].adjusted_close
        if previous is not None and current is not None and previous > 0:
            returns.append(current / previous - 1.0)
    return returns


def fmt(value: float | int | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def realized_volatility(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    return stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def downside_volatility(returns: list[float]) -> float | None:
    downside = [min(value, 0.0) for value in returns]
    if not downside:
        return None
    return math.sqrt(fmean(value * value for value in downside)) * math.sqrt(TRADING_DAYS_PER_YEAR)


def max_drawdown(points: list[PricePoint]) -> float | None:
    peak: float | None = None
    drawdown: float | None = None
    for point in points:
        value = point.adjusted_close
        if value is None or value <= 0:
            continue
        peak = value if peak is None else max(peak, value)
        current_drawdown = value / peak - 1.0
        drawdown = current_drawdown if drawdown is None else min(drawdown, current_drawdown)
    return drawdown


def dated_returns(points: list[PricePoint], latest_index: int, window_days: int) -> dict[date, float]:
    window = clean_window(points, latest_index, window_days)
    output: dict[date, float] = {}
    for index in range(1, len(window)):
        previous = window[index - 1].adjusted_close
        current = window[index].adjusted_close
        if previous is not None and current is not None and previous > 0:
            output[window[index].date] = current / previous - 1.0
    return output


def beta_to_benchmark(
    points: list[PricePoint],
    latest_index: int,
    benchmark_returns: dict[date, float],
    *,
    beta_window_days: int,
    min_observations: int,
) -> tuple[float | None, int]:
    if not benchmark_returns:
        return None, 0
    stock_returns = dated_returns(points, latest_index, beta_window_days)
    paired = [(stock_returns[day], benchmark_returns[day]) for day in sorted(stock_returns) if day in benchmark_returns]
    if len(paired) < min_observations:
        return None, len(paired)
    stock_mean = fmean(value[0] for value in paired)
    benchmark_mean = fmean(value[1] for value in paired)
    variance = sum((benchmark - benchmark_mean) ** 2 for _stock, benchmark in paired)
    if variance == 0:
        return None, len(paired)
    covariance = sum((stock - stock_mean) * (benchmark - benchmark_mean) for stock, benchmark in paired)
    return covariance / variance, len(paired)


def build_panel(
    price_rows: list[dict[str, str]],
    *,
    rebalance_dates: list[date],
    benchmark_rows: list[dict[str, str]] | None = None,
    beta_window_days: int = 252,
    min_beta_observations: int = 60,
    stale_filter_days: int | None = None,
    flag_price_limit: bool = False,
) -> list[dict[str, str]]:
    price_index = build_price_index(price_rows)
    all_price_dates = sorted({point.date for points in price_index.values() for point in points})
    benchmark_returns = build_benchmark_returns(benchmark_rows or [])
    output: list[dict[str, str]] = []
    for rebalance_date in rebalance_dates:
        for code in sorted(price_index):
            points = price_index[code]
            latest = latest_point(points, rebalance_date)
            if latest is None:
                continue
            latest_index, point = latest
            missing_flags: list[str] = []
            filter_reasons: list[str] = []
            staleness = sum(1 for value in all_price_dates if point.date < value <= rebalance_date)
            if staleness == 0 and point.date < rebalance_date:
                staleness = (rebalance_date - point.date).days
            if staleness > 0:
                missing_flags.append("latest_price_stale")
            if stale_filter_days is not None and staleness > stale_filter_days:
                filter_reasons.append("stale_price")
            if flag_price_limit and point.price_limit_flag:
                filter_reasons.append("price_limit_hit")

            row: dict[str, str] = {
                "rebalance_date": rebalance_date.isoformat(),
                "code": code,
                "latest_price_date": point.date.isoformat(),
                "latest_price_stale": "true" if staleness > 0 else "false",
                "price_staleness_trading_days": str(staleness),
                "price_limit_flag": "true" if point.price_limit_flag else "false",
                "defensive_filter_reasons": ";".join(filter_reasons),
            }
            for label, window_days in WINDOWS.items():
                window_points = clean_window(points, latest_index, window_days)
                returns = returns_from_points(window_points)
                row[f"history_observations_{label}"] = str(len(window_points))
                vol = realized_volatility(returns)
                row[f"realized_vol_{label}"] = fmt(vol)
                if vol is None or len(returns) < window_days:
                    missing_flags.append(f"insufficient_history_{label}")
                if label in {"6m", "12m"}:
                    row[f"downside_vol_{label}"] = fmt(downside_volatility(returns) if len(returns) >= window_days else None)
                    row[f"max_drawdown_{label}"] = fmt(max_drawdown(window_points) if len(returns) >= window_days else None)

            beta, beta_count = beta_to_benchmark(
                points,
                latest_index,
                benchmark_returns,
                beta_window_days=beta_window_days,
                min_observations=min_beta_observations,
            )
            row["beta_to_benchmark"] = fmt(beta)
            row["benchmark_observations"] = str(beta_count)
            if beta is None:
                missing_flags.append("missing_beta_to_benchmark")
            row["missing_flags"] = ";".join(dict.fromkeys(missing_flags))
            output.append(row)
    output.sort(key=lambda row: (row["rebalance_date"], row["code"]))
    return output


def output_date_range(rows: list[dict[str, str]]) -> str:
    values = sorted(row["rebalance_date"] for row in rows if row.get("rebalance_date"))
    if not values:
        return ""
    return f"{values[0]}..{values[-1]}"


def main() -> int:
    args = build_parser().parse_args()
    if args.beta_window_days <= 1:
        raise ValueError("--beta-window-days must be greater than 1.")
    if args.min_beta_observations <= 1:
        raise ValueError("--min-beta-observations must be greater than 1.")
    if args.stale_filter_days is not None and args.stale_filter_days < 0:
        raise ValueError("--stale-filter-days cannot be negative.")
    rows = build_panel(
        read_csv(args.prices),
        rebalance_dates=load_rebalance_dates(args.rebalance_dates, args.rebalance_date_values),
        benchmark_rows=read_csv(args.market_benchmark_prices) if args.market_benchmark_prices else None,
        beta_window_days=args.beta_window_days,
        min_beta_observations=args.min_beta_observations,
        stale_filter_days=args.stale_filter_days,
        flag_price_limit=args.flag_price_limit,
    )
    write_table(rows, args.out, format=args.output_format, fieldnames=FIELDNAMES)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="price_defensive_factor_panel",
            file_path=args.out,
            vendor="local",
            schema_version="price_defensive_factor_panel_v0_1",
            date_range=output_date_range(rows) or args.run_label,
            notes=f"{len(rows)} rows",
        )
    print(f"Wrote {len(rows)} price defensive factor rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
