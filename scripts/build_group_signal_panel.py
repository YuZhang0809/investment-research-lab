from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median, stdev
from typing import Any

from group_beta_common import fmt, load_dates, load_group_membership_panel, memberships_for_date, parse_optional_date
from research_common import append_manifest, date_range_from_rows, parse_float, read_table, write_table


BASE_FIELDS = ["rebalance_date", "group_type", "group_id", "group_name", "coverage", "constituent_count"]
TRAILING_FIELDS = ["missing_flags"]
AGGREGATION_METHODS = {"mean", "median", "weighted_mean", "coverage_rate"}


@dataclass(frozen=True)
class AggregationSpec:
    field: str
    method: str
    output: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build generic group-level signal panels.")
    parser.add_argument("--basket-returns", required=True, type=Path)
    parser.add_argument("--membership-panel", required=True, type=Path)
    parser.add_argument("--factor-panel", type=Path)
    parser.add_argument(
        "--factor-aggregation",
        action="append",
        default=[],
        help="FIELD:METHOD[:OUTPUT]. METHOD is mean, median, weighted_mean, coverage_rate, or pNN.",
    )
    parser.add_argument("--external-panel", type=Path, help="Optional group-level external signal panel.")
    parser.add_argument("--external-field", action="append", default=[], help="External group signal field to copy.")
    parser.add_argument("--external-asof-date-field", help="Use as-of join with this availability date field.")
    parser.add_argument("--market-benchmark", type=Path, help="Optional benchmark returns/value series for group beta.")
    parser.add_argument("--rebalance-dates", type=Path)
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values")
    parser.add_argument("--membership-date-field", default="auto")
    parser.add_argument("--membership-duplicate-policy", choices=["fail", "aggregate"], default="fail")
    parser.add_argument("--momentum-window", action="append", type=int, default=[], help="Trailing basket-return periods.")
    parser.add_argument("--risk-window", action="append", type=int, default=[], help="Trailing basket-return periods.")
    parser.add_argument("--beta-window", type=int, default=6)
    parser.add_argument("--annualization", type=float, default=12.0)
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-label", default="group_signals")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def parse_aggregation(value: str) -> AggregationSpec:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) not in {2, 3} or not parts[0] or not parts[1]:
        raise ValueError("--factor-aggregation must use FIELD:METHOD[:OUTPUT].")
    field, method = parts[0], parts[1]
    if method.startswith("p") and method[1:].isdigit():
        percentile_value = int(method[1:])
        if percentile_value < 0 or percentile_value > 100:
            raise ValueError(f"Percentile aggregation must be in [0, 100]: {method}")
    elif method not in AGGREGATION_METHODS:
        raise ValueError(f"Unsupported group factor aggregation method: {method}")
    output = parts[2] if len(parts) == 3 and parts[2] else f"{field}_{method}"
    return AggregationSpec(field=field, method=method, output=output)


def basket_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(row.get("date") or row.get("rebalance_date"), "basket_returns.date")


def group_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("group_type") or "").strip(), str(row.get("group_id") or "").strip()


def load_basket_rows(path: Path, input_format: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        row_date = basket_date(row)
        key = group_key(row)
        if row_date is None or not key[0] or not key[1]:
            continue
        normalized = dict(row)
        normalized["_date"] = row_date
        grouped[key].append(normalized)
    for values in grouped.values():
        values.sort(key=lambda item: item["_date"])
    return grouped


def benchmark_returns(path: Path | None, input_format: str) -> dict[date, float]:
    if path is None:
        return {}
    rows = read_table(path, format=input_format).to_dict(orient="records")
    direct: dict[date, float] = {}
    values: list[tuple[date, float]] = []
    for row in rows:
        row_date = parse_optional_date(row.get("date") or row.get("Date"), "benchmark.date")
        if row_date is None:
            continue
        return_value = None
        for field in ("return", "benchmark_return"):
            value = row.get(field)
            if field in row and value is not None and str(value).strip() != "":
                return_value = parse_float(value)
                break
        if return_value is not None:
            direct[row_date] = return_value
            continue
        value = parse_float(row.get("close") or row.get("value") or row.get("index_value") or row.get("benchmark_value"))
        if value is not None and value > 0:
            values.append((row_date, value))
    values.sort(key=lambda item: item[0])
    for index in range(1, len(values)):
        previous = values[index - 1][1]
        current = values[index][1]
        if previous > 0:
            direct[values[index][0]] = current / previous - 1.0
    return direct


def trailing_rows(rows: list[dict[str, Any]], target_date: date, window: int) -> list[dict[str, Any]]:
    eligible = [row for row in rows if row["_date"] <= target_date]
    return eligible[-window:]


def clean_returns(rows: list[dict[str, Any]]) -> list[float]:
    return [value for value in (parse_float(row.get("basket_return")) for row in rows) if value is not None]


def cumulative_return(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    for value in values:
        total *= 1 + value
    return total - 1.0


def downside_vol(values: list[float], annualization: float) -> float | None:
    if not values:
        return None
    downside = [min(value, 0.0) for value in values]
    return math.sqrt(sum(value * value for value in downside) / len(downside)) * math.sqrt(annualization)


def max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1.0)
    return drawdown


def beta_to_benchmark(rows: list[dict[str, Any]], benchmark_by_date: dict[date, float]) -> float | None:
    paired: list[tuple[float, float]] = []
    for row in rows:
        group_return = parse_float(row.get("basket_return"))
        benchmark_return = benchmark_by_date.get(row["_date"])
        if group_return is not None and benchmark_return is not None:
            paired.append((group_return, benchmark_return))
    if len(paired) < 2:
        return None
    group_mean = sum(item[0] for item in paired) / len(paired)
    benchmark_mean = sum(item[1] for item in paired) / len(paired)
    covariance = sum((item[0] - group_mean) * (item[1] - benchmark_mean) for item in paired)
    variance = sum((item[1] - benchmark_mean) ** 2 for item in paired)
    return covariance / variance if variance > 0 else None


def factor_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(row.get("rebalance_date") or row.get("date") or row.get("Date"), "factor_panel.rebalance_date")


def latest_factor_rows(path: Path | None, input_format: str) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        code = str(row.get("code") or row.get("Code") or "").strip()
        row_date = factor_date(row)
        if code and row_date is not None:
            normalized = dict(row)
            normalized["_date"] = row_date
            grouped[code].append(normalized)
    for values in grouped.values():
        values.sort(key=lambda item: item["_date"])
    return grouped


def factor_row_for_code(grouped: dict[str, list[dict[str, Any]]], code: str, target_date: date) -> dict[str, Any] | None:
    values = grouped.get(code, [])
    selected: dict[str, Any] | None = None
    for row in values:
        if row["_date"] <= target_date:
            selected = row
        else:
            break
    return selected


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    clean = sorted(values)
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[int(position)]
    return clean[lower] * (upper - position) + clean[upper] * (position - lower)


def aggregate_values(values: list[tuple[float | None, float]], method: str) -> float | None:
    clean = [(value, weight) for value, weight in values if value is not None]
    if method == "coverage_rate":
        return len(clean) / len(values) if values else None
    numbers = [value for value, _weight in clean]
    if not numbers:
        return None
    if method == "mean":
        return sum(numbers) / len(numbers)
    if method == "median":
        return float(median(numbers))
    if method == "weighted_mean":
        total_weight = sum(weight for _value, weight in clean if weight > 0)
        if total_weight <= 0:
            return None
        return sum((value or 0.0) * weight for value, weight in clean if weight > 0) / total_weight
    if method.startswith("p") and method[1:].isdigit():
        return percentile(numbers, int(method[1:]) / 100)
    raise ValueError(f"Unsupported aggregation method: {method}")


def factor_aggregates_for_group(
    memberships: list[Any],
    factor_rows: dict[str, list[dict[str, Any]]],
    target_date: date,
    specs: list[AggregationSpec],
) -> dict[str, float | None]:
    output: dict[str, float | None] = {}
    for spec in specs:
        values: list[tuple[float | None, float]] = []
        for membership in memberships:
            factor_row = factor_row_for_code(factor_rows, membership.code, target_date)
            value = parse_float(factor_row.get(spec.field)) if factor_row is not None else None
            values.append((value, membership.membership_weight))
        output[spec.output] = aggregate_values(values, spec.method)
    return output


def external_rows_by_group(
    path: Path | None,
    input_format: str,
    fields: list[str],
    asof_date_field: str | None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if path is None:
        return {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        key = group_key(row)
        if not key[0] or not key[1]:
            continue
        date_value = row.get(asof_date_field) if asof_date_field else row.get("rebalance_date") or row.get("date")
        row_date = parse_optional_date(date_value, "external_group_signal.date")
        if row_date is None:
            continue
        missing = [field for field in fields if field not in row]
        if missing:
            raise ValueError(f"External group panel missing field(s): {', '.join(missing)}")
        normalized = dict(row)
        normalized["_date"] = row_date
        grouped[key].append(normalized)
    for values in grouped.values():
        values.sort(key=lambda item: item["_date"])
    return grouped


def external_row_for_group(rows: dict[tuple[str, str], list[dict[str, Any]]], key: tuple[str, str], target_date: date, asof: bool):
    if not asof:
        for row in rows.get(key, []):
            if row["_date"] == target_date:
                return row
        return None
    selected = None
    for row in rows.get(key, []):
        if row["_date"] <= target_date:
            selected = row
        else:
            break
    return selected


def build_panel(
    basket_rows_by_group: dict[tuple[str, str], list[dict[str, Any]]],
    membership_panel_path: Path,
    *,
    rebalance_dates: list[date],
    input_format: str = "auto",
    membership_date_field: str = "auto",
    membership_duplicate_policy: str = "fail",
    factor_rows: dict[str, list[dict[str, Any]]] | None = None,
    aggregation_specs: list[AggregationSpec] | None = None,
    external_rows: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    external_fields: list[str] | None = None,
    external_asof: bool = False,
    benchmark_by_date: dict[date, float] | None = None,
    momentum_windows: list[int] | None = None,
    risk_windows: list[int] | None = None,
    beta_window: int = 6,
    annualization: float = 12.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    membership_panel = load_group_membership_panel(
        membership_panel_path,
        input_format=input_format,
        date_field=membership_date_field,
        duplicate_policy=membership_duplicate_policy,
    )
    if not rebalance_dates:
        rebalance_dates = sorted({row["_date"] for rows in basket_rows_by_group.values() for row in rows})
    factor_rows = factor_rows or {}
    aggregation_specs = aggregation_specs or []
    external_rows = external_rows or {}
    external_fields = external_fields or []
    benchmark_by_date = benchmark_by_date or {}
    momentum_windows = momentum_windows or [3, 6]
    risk_windows = risk_windows or [6]

    signal_fields = [f"group_return_{window}p" for window in momentum_windows]
    for window in risk_windows:
        signal_fields.extend([f"group_vol_{window}p", f"group_downside_vol_{window}p", f"group_max_drawdown_{window}p"])
    signal_fields.append("group_beta_to_benchmark")
    aggregate_fields = [spec.output for spec in aggregation_specs]
    fieldnames = [*BASE_FIELDS, *signal_fields, *aggregate_fields, *external_fields, *TRAILING_FIELDS]

    output: list[dict[str, Any]] = []
    for rebalance_date in sorted(rebalance_dates):
        memberships = memberships_for_date(membership_panel, rebalance_date)
        membership_by_group: dict[tuple[str, str], list[Any]] = defaultdict(list)
        names: dict[tuple[str, str], str] = {}
        for membership in memberships:
            key = (membership.group_type, membership.group_id)
            membership_by_group[key].append(membership)
            names.setdefault(key, membership.group_name)
        group_keys = sorted(set(basket_rows_by_group) | set(membership_by_group))
        for key in group_keys:
            rows = basket_rows_by_group.get(key, [])
            missing_flags: list[str] = []
            row: dict[str, Any] = {
                "rebalance_date": rebalance_date,
                "group_type": key[0],
                "group_id": key[1],
                "group_name": names.get(key) or (rows[-1].get("group_name") if rows else ""),
                "coverage": "",
                "constituent_count": len(membership_by_group.get(key, [])),
            }
            current_basket = next((item for item in reversed(rows) if item["_date"] <= rebalance_date), None)
            if current_basket is not None:
                row["coverage"] = current_basket.get("coverage", "")
                row["constituent_count"] = current_basket.get("constituent_count", row["constituent_count"])
            else:
                missing_flags.append("basket_return_history")
            for window in momentum_windows:
                returns = clean_returns(trailing_rows(rows, rebalance_date, window))
                value = cumulative_return(returns) if len(returns) >= window else None
                row[f"group_return_{window}p"] = value
                if value is None:
                    missing_flags.append(f"group_return_{window}p")
            for window in risk_windows:
                returns = clean_returns(trailing_rows(rows, rebalance_date, window))
                if len(returns) >= max(window, 2):
                    row[f"group_vol_{window}p"] = stdev(returns) * math.sqrt(annualization)
                    row[f"group_downside_vol_{window}p"] = downside_vol(returns, annualization)
                    row[f"group_max_drawdown_{window}p"] = max_drawdown(returns)
                else:
                    row[f"group_vol_{window}p"] = None
                    row[f"group_downside_vol_{window}p"] = None
                    row[f"group_max_drawdown_{window}p"] = None
                    missing_flags.extend([f"group_vol_{window}p", f"group_downside_vol_{window}p", f"group_max_drawdown_{window}p"])
            beta_rows = trailing_rows(rows, rebalance_date, beta_window)
            row["group_beta_to_benchmark"] = beta_to_benchmark(beta_rows, benchmark_by_date)
            if row["group_beta_to_benchmark"] is None:
                missing_flags.append("group_beta_to_benchmark")
            aggregates = factor_aggregates_for_group(
                membership_by_group.get(key, []),
                factor_rows,
                rebalance_date,
                aggregation_specs,
            )
            for field, value in aggregates.items():
                row[field] = value
                if value is None:
                    missing_flags.append(field)
            external_row = external_row_for_group(external_rows, key, rebalance_date, external_asof)
            for field in external_fields:
                value = external_row.get(field) if external_row is not None else None
                row[field] = value
                if value in {None, ""}:
                    missing_flags.append(field)
            row["missing_flags"] = ";".join(dict.fromkeys(missing_flags))
            output.append(row)
    return output, fieldnames


def main() -> int:
    args = build_parser().parse_args()
    basket_rows = load_basket_rows(args.basket_returns, args.input_format)
    factor_rows = latest_factor_rows(args.factor_panel, args.input_format)
    aggregation_specs = [parse_aggregation(value) for value in args.factor_aggregation]
    external_rows = external_rows_by_group(
        args.external_panel,
        args.input_format,
        args.external_field,
        args.external_asof_date_field,
    )
    rows, fieldnames = build_panel(
        basket_rows,
        args.membership_panel,
        rebalance_dates=load_dates(args.rebalance_dates, args.rebalance_date_values),
        input_format=args.input_format,
        membership_date_field=args.membership_date_field,
        membership_duplicate_policy=args.membership_duplicate_policy,
        factor_rows=factor_rows,
        aggregation_specs=aggregation_specs,
        external_rows=external_rows,
        external_fields=args.external_field,
        external_asof=bool(args.external_asof_date_field),
        benchmark_by_date=benchmark_returns(args.market_benchmark, args.input_format),
        momentum_windows=args.momentum_window or [3, 6],
        risk_windows=args.risk_window or [6],
        beta_window=args.beta_window,
        annualization=args.annualization,
    )
    serializable = [{key: fmt(value) for key, value in row.items()} for row in rows]
    write_table(serializable, args.out, format=args.output_format, fieldnames=fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_group_signals",
            file_path=args.out,
            vendor="internal",
            schema_version="group_signals_v0_1",
            date_range=date_range_from_rows(serializable, "rebalance_date"),
            notes=f"run_label={args.run_label};rows={len(rows)}",
        )
    print(f"Wrote {len(rows)} group signal rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
