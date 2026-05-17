from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from research_common import (
    append_manifest,
    parse_date,
    parse_float,
    read_csv,
    trading_calendar_from_rows,
    trading_day_offset,
    write_csv,
)


DEFAULT_FACTORS = [
    "operating_profit_to_total_assets",
    "equity_to_assets",
    "earnings_yield",
    "book_to_market",
    "return_12_1",
    "return_6_1",
]


@dataclass
class PricePoint:
    date: date
    adjusted_close: float


@dataclass
class ForwardReturnResult:
    value: float | None
    status: str


@dataclass
class FactorObservation:
    code: str
    name: str
    sector: str
    factor_value: float
    forward_return: float
    factor_quantile: int | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze simple factor forward returns from QVM factor files.")
    parser.add_argument("--factors-dir", type=Path, default=Path("data/processed/factors"))
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--holding-days", type=int, default=63)
    parser.add_argument("--factor", action="append", dest="factors", help="Factor column to analyze. Can be repeated.")
    parser.add_argument("--top-frac", type=float, default=0.2)
    parser.add_argument("--quantiles", type=int, default=5)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/factor_analysis"))
    parser.add_argument("--alphalens-out", type=Path, default=None)
    parser.add_argument("--report-dir", type=Path, default=Path("reports/factor_analysis"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def build_price_index(rows: list[dict[str, str]]) -> dict[str, list[PricePoint]]:
    grouped: dict[str, list[PricePoint]] = defaultdict(list)
    for row in rows:
        code = (row.get("code") or "").strip()
        row_date = parse_date(row.get("date"), field_name="prices.date")
        price = parse_float(row.get("adjusted_close") or row.get("unadjusted_close"))
        if code and row_date and price and price > 0:
            grouped[code].append(PricePoint(date=row_date, adjusted_close=price))
    for values in grouped.values():
        values.sort(key=lambda item: item.date)
    return grouped


def price_on_date(points: list[PricePoint], target: date) -> PricePoint | None:
    for point in points:
        if point.date == target:
            return point
        if point.date > target:
            return None
    return None


def has_price_after(points: list[PricePoint], target: date) -> bool:
    return any(point.date > target for point in points)


def future_return(
    points: list[PricePoint],
    calendar: list[date],
    rebalance_date: date,
    holding_days: int,
) -> ForwardReturnResult:
    if not points:
        return ForwardReturnResult(None, "missing_price_history")
    entry_date = trading_day_offset(calendar, rebalance_date, 0, mode="on_or_after")
    if entry_date is None:
        return ForwardReturnResult(None, "missing_start_price")
    exit_date = trading_day_offset(calendar, entry_date, holding_days, mode="on_or_after")
    if exit_date is None:
        return ForwardReturnResult(None, "insufficient_forward_window")
    start = price_on_date(points, entry_date)
    if start is None:
        return ForwardReturnResult(None, "missing_start_price")
    end = price_on_date(points, exit_date)
    if end is None:
        if not has_price_after(points, exit_date) and points[-1].date < exit_date:
            return ForwardReturnResult(None, "price_tail_gap")
        return ForwardReturnResult(None, "missing_exit_price")
    start_price = start.adjusted_close
    end_price = end.adjusted_close
    if start_price <= 0:
        return ForwardReturnResult(None, "invalid_start_price")
    return ForwardReturnResult(end_price / start_price - 1.0, "ok")


def factor_files(path: Path, start: date, end: date) -> list[Path]:
    by_month: dict[str, Path] = {}
    candidates = [*path.glob("factors_*.csv"), *path.glob("factors_*.parquet")]
    for file_path in sorted(candidates):
        suffix = file_path.stem.replace("factors_", "")
        if len(suffix) != 6 or not suffix.isdigit():
            continue
        month_date = parse_date(f"{suffix[:4]}-{suffix[4:]}-01", field_name="factor_file_month")
        if month_date and start.replace(day=1) <= month_date <= end.replace(day=1):
            existing = by_month.get(suffix)
            if existing is None or file_path.suffix.lower() == ".parquet":
                by_month[suffix] = file_path
    return [by_month[key] for key in sorted(by_month)]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    output = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        for original_index, _value in indexed[index:end]:
            output[original_index] = average_rank
        index = end
    return output


def rank_ic(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return pearson(ranks(xs), ranks(ys))


def assign_quantiles(observations: list[FactorObservation], requested_quantiles: int) -> int:
    if requested_quantiles < 2:
        raise ValueError("quantiles must be at least 2.")
    if not observations:
        return 0
    quantile_count = min(requested_quantiles, len(observations))
    ordered = sorted(observations, key=lambda item: (item.factor_value, item.code))
    for index, item in enumerate(ordered):
        item.factor_quantile = min(quantile_count, int(index * quantile_count / len(ordered)) + 1)
    return quantile_count


def factor_rank_map(observations: list[FactorObservation]) -> dict[str, float]:
    values = [item.factor_value for item in observations]
    return {
        item.code: rank
        for item, rank in zip(observations, ranks(values))
    }


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    center = sum(values) / len(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / (len(values) - 1))


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def number(value: float | None, digits: int = 4) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def write_report(path: Path, rows: list[dict[str, Any]], holding_days: int, quantiles: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Factor Forward Return Report",
        "",
        f"- holding days: {holding_days}",
        f"- quantiles: {quantiles} (1 = lowest factor values; higher = stronger factor values)",
        f"- rows: {len(rows)}",
        "",
        "## Summary",
        "",
        "| factor | months | observations | avg rank IC | avg pearson IC | IC std | IC IR | positive IC months | top quantile | bottom quantile | top-bottom | top turnover | rank autocorr | coverage | missing factor | missing forward |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    by_factor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_factor[str(row["factor"])].append(row)
    for factor, values in sorted(by_factor.items()):
        observations = sum(int(row["observations"]) for row in values)
        rank_ics = [float(row["rank_ic"]) for row in values if row.get("rank_ic") not in (None, "")]
        pearson_ics = [float(row["pearson_ic"]) for row in values if row.get("pearson_ic") not in (None, "")]
        avg_ic = average(rank_ics)
        ic_std = sample_std(rank_ics)
        ic_ir = avg_ic / ic_std if avg_ic is not None and ic_std and ic_std > 0 else None
        positive_ic_months = sum(1 for value in rank_ics if value > 0)
        avg_top = average([float(row["top_quantile_return"]) for row in values if row.get("top_quantile_return") not in (None, "")])
        avg_bottom = average(
            [float(row["bottom_quantile_return"]) for row in values if row.get("bottom_quantile_return") not in (None, "")]
        )
        avg_spread = average(
            [float(row["top_bottom_quantile_spread"]) for row in values if row.get("top_bottom_quantile_spread") not in (None, "")]
        )
        avg_turnover = average(
            [float(row["top_quantile_turnover"]) for row in values if row.get("top_quantile_turnover") not in (None, "")]
        )
        avg_rank_autocorr = average(
            [float(row["rank_autocorr"]) for row in values if row.get("rank_autocorr") not in (None, "")]
        )
        avg_coverage = average([float(row["coverage"]) for row in values if row.get("coverage") not in (None, "")])
        missing_factor = sum(int(row["missing_factor"]) for row in values)
        missing_forward = sum(int(row["missing_forward_return"]) for row in values)
        lines.append(
            f"| {factor} | {len(values)} | {observations} | "
            f"{number(avg_ic)} | {number(average(pearson_ics))} | {number(ic_std)} | {number(ic_ir)} | "
            f"{positive_ic_months}/{len(rank_ics)} | {pct(avg_top)} | {pct(avg_bottom)} | "
            f"{pct(avg_spread)} | {pct(avg_turnover)} | {number(avg_rank_autocorr)} | "
            f"{pct(avg_coverage)} | {missing_factor} | {missing_forward} |"
        )
    lines.extend(["", "## Quantile Returns", ""])
    quantile_headers = " | ".join(f"Q{index}" for index in range(1, quantiles + 1))
    quantile_separators = "|".join(["---", *["---:" for _index in range(quantiles)]])
    lines.append(f"| factor | {quantile_headers} |")
    lines.append(f"|{quantile_separators}|")
    for factor, values in sorted(by_factor.items()):
        quantile_cells = []
        for index in range(1, quantiles + 1):
            column = f"quantile_{index}_return"
            quantile_cells.append(pct(average([float(row[column]) for row in values if row.get(column) not in (None, "")])))
        lines.append(f"| {factor} | {' | '.join(quantile_cells)} |")
    lines.extend(
        [
            "",
            "## Monthly Diagnostics",
            "",
            "| date | factor | observations | rank IC | top-bottom | turnover | rank autocorr | bucket status | forward missing |",
            "|---|---|---:|---:|---:|---:|---:|---|---:|",
        ]
    )
    for row in sorted(rows, key=lambda item: (str(item["factor"]), str(item["rebalance_date"]))):
        lines.append(
            f"| {row['rebalance_date']} | {row['factor']} | {row['observations']} | "
            f"{number(row.get('rank_ic'))} | {pct(row.get('top_bottom_quantile_spread'))} | "
            f"{pct(row.get('top_quantile_turnover'))} | {number(row.get('rank_autocorr'))} | "
            f"{row.get('bucket_status', '')} | {row.get('missing_forward_return', '')} |"
        )
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "This is an Alphalens-style file-first diagnostic, not a full Alphalens tear sheet. It reports IC, quantile returns, turnover, rank autocorrelation, and coverage, but does not neutralize by sector or size and does not include transaction costs.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    start_date = parse_date(args.start_date, field_name="start_date")
    end_date = parse_date(args.end_date, field_name="end_date")
    if start_date is None or end_date is None:
        raise ValueError("start-date and end-date are required")
    if args.quantiles < 2:
        raise ValueError("quantiles must be at least 2.")
    factors = args.factors or DEFAULT_FACTORS
    price_rows = read_csv(args.prices)
    price_index = build_price_index(price_rows)
    calendar = trading_calendar_from_rows(price_rows)

    output_rows: list[dict[str, Any]] = []
    factor_data_rows: list[dict[str, Any]] = []
    previous_top_assets: dict[str, set[str]] = {}
    previous_factor_ranks: dict[str, dict[str, float]] = {}
    for file_path in factor_files(args.factors_dir, start_date, end_date):
        factor_rows = read_csv(file_path)
        if not factor_rows:
            continue
        rebalance_date = parse_date(factor_rows[0].get("rebalance_date"), field_name="factors.rebalance_date")
        if rebalance_date is None:
            continue
        for factor in factors:
            observations: list[FactorObservation] = []
            total_rows = 0
            missing_factor = 0
            missing_price_history = 0
            missing_start_price = 0
            missing_exit_price = 0
            insufficient_forward_window = 0
            invalid_start_price = 0
            price_tail_gap = 0
            for row in factor_rows:
                total_rows += 1
                factor_value = parse_float(row.get(factor))
                code = row.get("code", "")
                if factor_value is None:
                    missing_factor += 1
                    continue
                forward = future_return(price_index.get(code, []), calendar, rebalance_date, args.holding_days)
                if forward.status == "ok" and forward.value is not None:
                    observations.append(
                        FactorObservation(
                            code=code,
                            name=row.get("name", ""),
                            sector=row.get("sector", ""),
                            factor_value=factor_value,
                            forward_return=forward.value,
                        )
                    )
                elif forward.status == "price_tail_gap":
                    price_tail_gap += 1
                elif forward.status == "missing_price_history":
                    missing_price_history += 1
                elif forward.status == "missing_start_price":
                    missing_start_price += 1
                elif forward.status == "missing_exit_price":
                    missing_exit_price += 1
                elif forward.status == "insufficient_forward_window":
                    insufficient_forward_window += 1
                elif forward.status == "invalid_start_price":
                    invalid_start_price += 1
            observations.sort(key=lambda item: item.factor_value, reverse=True)
            bucket_size = max(1, int(math.ceil(len(observations) * args.top_frac))) if observations else 0
            bucket_status = "ok"
            if not bucket_size or len(observations) < bucket_size * 2:
                bucket_status = "insufficient_non_overlapping_observations"
                top: list[FactorObservation] = []
                bottom: list[FactorObservation] = []
            else:
                top = observations[:bucket_size]
                bottom = observations[-bucket_size:]
            top_return = average([item.forward_return for item in top])
            bottom_return = average([item.forward_return for item in bottom])
            factor_values = [item.factor_value for item in observations]
            forward_values = [item.forward_return for item in observations]
            quantile_count = assign_quantiles(observations, args.quantiles)
            quantile_returns = {
                index: average([item.forward_return for item in observations if item.factor_quantile == index])
                for index in range(1, quantile_count + 1)
            }
            bottom_quantile_return = quantile_returns.get(1)
            top_quantile_return = quantile_returns.get(quantile_count)
            top_bottom_quantile_spread = (
                top_quantile_return - bottom_quantile_return
                if top_quantile_return is not None and bottom_quantile_return is not None
                else None
            )
            top_quantile_assets = {
                item.code
                for item in observations
                if quantile_count and item.factor_quantile == quantile_count
            }
            previous_assets = previous_top_assets.get(factor)
            top_quantile_turnover = None
            if previous_assets is not None and top_quantile_assets:
                top_quantile_turnover = 1.0 - len(top_quantile_assets & previous_assets) / len(top_quantile_assets)
            current_ranks = factor_rank_map(observations)
            previous_ranks = previous_factor_ranks.get(factor)
            rank_autocorr = None
            if previous_ranks is not None:
                overlap = sorted(set(previous_ranks) & set(current_ranks))
                if len(overlap) >= 2:
                    rank_autocorr = pearson(
                        [previous_ranks[code] for code in overlap],
                        [current_ranks[code] for code in overlap],
                    )
            previous_top_assets[factor] = top_quantile_assets
            previous_factor_ranks[factor] = current_ranks
            missing_forward = (
                missing_price_history
                + missing_start_price
                + missing_exit_price
                + price_tail_gap
                + insufficient_forward_window
                + invalid_start_price
            )
            output_rows.append(
                {
                    "rebalance_date": rebalance_date,
                    "factor": factor,
                    "rows": total_rows,
                    "observations": len(observations),
                    "coverage": len(observations) / total_rows if total_rows else 0,
                    "pearson_ic": pearson(factor_values, forward_values),
                    "rank_ic": rank_ic(factor_values, forward_values),
                    "top_count": len(top),
                    "bottom_count": len(bottom),
                    "bucket_status": bucket_status,
                    "top_return": top_return,
                    "bottom_return": bottom_return,
                    "top_bottom_spread": top_return - bottom_return if top_return is not None and bottom_return is not None else None,
                    "quantile_count": quantile_count,
                    "quantile_status": "ok" if quantile_count >= 2 else "insufficient_quantile_observations",
                    "top_quantile_return": top_quantile_return,
                    "bottom_quantile_return": bottom_quantile_return,
                    "top_bottom_quantile_spread": top_bottom_quantile_spread,
                    "top_quantile_turnover": top_quantile_turnover,
                    "rank_autocorr": rank_autocorr,
                    **{f"quantile_{index}_return": quantile_returns.get(index) for index in range(1, args.quantiles + 1)},
                    "missing_factor": missing_factor,
                    "missing_forward_return": missing_forward,
                    "missing_price_history": missing_price_history,
                    "missing_start_price": missing_start_price,
                    "missing_exit_price": missing_exit_price,
                    "price_tail_gap": price_tail_gap,
                    "insufficient_forward_window": insufficient_forward_window,
                    "invalid_start_price": invalid_start_price,
                }
            )
            forward_column = f"forward_return_{args.holding_days}d"
            for item in sorted(observations, key=lambda value: (value.factor_quantile or 0, value.code)):
                factor_data_rows.append(
                    {
                        "date": rebalance_date,
                        "asset": item.code,
                        "factor": factor,
                        "factor_value": item.factor_value,
                        forward_column: item.forward_return,
                        "factor_quantile": item.factor_quantile,
                        "group": item.sector,
                        "sector": item.sector,
                        "name": item.name,
                        "forward_status": "ok",
                    }
                )

    token = f"{start_date.strftime('%Y%m')}_{end_date.strftime('%Y%m')}_{args.holding_days}d"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.out_dir / f"factor_forward_returns_{token}.csv"
    alphalens_path = args.alphalens_out or args.out_dir / f"alphalens_factor_data_{token}.csv"
    report_path = args.report_dir / f"factor_forward_returns_{token}.md"
    fields = [
        "rebalance_date",
        "factor",
        "rows",
        "observations",
        "coverage",
        "pearson_ic",
        "rank_ic",
        "top_count",
        "bottom_count",
        "bucket_status",
        "top_return",
        "bottom_return",
        "top_bottom_spread",
        "quantile_count",
        "quantile_status",
        "top_quantile_return",
        "bottom_quantile_return",
        "top_bottom_quantile_spread",
        "top_quantile_turnover",
        "rank_autocorr",
        *[f"quantile_{index}_return" for index in range(1, args.quantiles + 1)],
        "missing_factor",
        "missing_forward_return",
        "missing_price_history",
        "missing_start_price",
        "missing_exit_price",
        "price_tail_gap",
        "insufficient_forward_window",
        "invalid_start_price",
    ]
    write_csv(output_path, [{key: fmt(value) for key, value in row.items()} for row in output_rows], fields)
    factor_data_fields = [
        "date",
        "asset",
        "factor",
        "factor_value",
        f"forward_return_{args.holding_days}d",
        "factor_quantile",
        "group",
        "sector",
        "name",
        "forward_status",
    ]
    write_csv(alphalens_path, [{key: fmt(value) for key, value in row.items()} for row in factor_data_rows], factor_data_fields)
    write_report(report_path, output_rows, args.holding_days, args.quantiles)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_factor_forward_returns",
            file_path=output_path,
            vendor="local",
            schema_version="factor_forward_returns_v0_3",
            date_range=f"{start_date.isoformat()}..{end_date.isoformat()}",
            notes=f"{len(output_rows)} rows; holding_days={args.holding_days}",
        )
        append_manifest(
            args.manifest,
            source="derived_alphalens_factor_data",
            file_path=alphalens_path,
            vendor="local",
            schema_version="alphalens_factor_data_v0_1",
            date_range=f"{start_date.isoformat()}..{end_date.isoformat()}",
            notes=f"{len(factor_data_rows)} rows; holding_days={args.holding_days}; quantiles={args.quantiles}",
        )
        append_manifest(
            args.manifest,
            source="derived_factor_forward_returns_report",
            file_path=report_path,
            vendor="local",
            schema_version="factor_forward_returns_report_v0_2",
            date_range=f"{start_date.isoformat()}..{end_date.isoformat()}",
            notes=f"holding_days={args.holding_days}",
        )
    print(f"Wrote factor forward returns to {output_path}")
    print(f"Wrote Alphalens-style factor data to {alphalens_path}")
    print(f"Wrote factor forward return report to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
