from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from factor_expressions import (
    evaluate_factor_expression,
    factor_definition_names,
    ordered_factor_definitions,
)
from research_common import (
    append_manifest,
    load_yaml,
    month_key,
    parse_date,
    parse_float,
    read_csv,
    trading_calendar_from_rows,
    trading_day_offset,
    write_csv,
)


TRADING_DAYS_1M = 21
TRADING_DAYS_6M = 126
TRADING_DAYS_12M = 252
BASE_FACTOR_FIELDS = [
    "operating_profit_to_total_assets",
    "equity_to_assets",
    "earnings_yield",
    "book_to_market",
    "return_12_1",
    "return_6_1",
]
FACTOR_METADATA_FIELDS = [
    "rebalance_date",
    "code",
    "name",
    "market",
    "sector",
    "price_date",
    "latest_unadjusted_close",
    "fundamentals_available_date",
    "fundamentals_available_time",
    "document_type",
    "period_end",
    "disclosure_number",
    "market_cap",
    "operating_profit",
    "net_profit",
    "equity",
    "total_assets",
    "shares",
]
FACTOR_EXPRESSION_BASE_FIELDS = frozenset(
    {
        "latest_unadjusted_close",
        "market_cap",
        "operating_profit",
        "net_profit",
        "equity",
        "total_assets",
        "shares",
        *BASE_FACTOR_FIELDS,
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build QVM raw factor CSV for a rebalance date.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/factors"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def group_by_code(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        code = (row.get("code") or "").strip()
        if code:
            grouped[code].append(row)
    return grouped


def price_date(row: dict[str, str]) -> date | None:
    return parse_date(row.get("date"), field_name="prices.date")


def validate_unique_price_rows(rows: list[dict[str, str]]) -> None:
    seen: set[tuple[str, date]] = set()
    for row in rows:
        code = (row.get("code") or "").strip()
        row_date = price_date(row)
        if not code or row_date is None:
            continue
        key = (code, row_date)
        if key in seen:
            raise ValueError(f"Duplicate price rows for code={code};date={row_date}.")
        seen.add(key)


def validate_unique_fundamental_rows(rows: list[dict[str, str]]) -> None:
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        code = (row.get("code") or "").strip()
        if not code:
            continue
        key = (
            code,
            fundamental_available_date(row),
            row.get("available_time", ""),
            row.get("period_end", ""),
            row.get("disclosure_number", ""),
        )
        if key in seen:
            raise ValueError(
                "Duplicate fundamentals rows for "
                f"code={key[0]};available_date={key[1]};available_time={key[2]};"
                f"period_end={key[3]};disclosure_number={key[4]}."
            )
        seen.add(key)


def rows_until_rebalance(rows: list[dict[str, str]], rebalance_date: date) -> list[dict[str, str]]:
    clean = [row for row in rows if price_date(row) and price_date(row) <= rebalance_date]
    return rows_with_effective_adjusted_close(sorted(clean, key=lambda row: price_date(row) or date.min))


def rows_with_effective_adjusted_close(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    cumulative_adjustment = 1.0
    adjusted_rows: list[dict[str, str]] = []
    for row in rows:
        copied = dict(row)
        adjusted = parse_float(copied.get("adjusted_close"))
        unadjusted = parse_float(copied.get("unadjusted_close"))
        raw_adjustment_factor = copied.get("adjustment_factor")
        adjustment_factor = parse_float(raw_adjustment_factor)
        if adjusted is None and unadjusted is not None and (adjustment_factor is None or adjustment_factor <= 0):
            raise ValueError(
                "Missing adjusted_close requires positive adjustment_factor "
                f"for code={copied.get('code', '')};date={copied.get('date', '')}."
            )
        if adjustment_factor is not None and adjustment_factor > 0:
            cumulative_adjustment *= adjustment_factor
        if adjusted is None and unadjusted is not None:
            copied["_effective_adjusted_close"] = str(unadjusted / cumulative_adjustment)
        adjusted_rows.append(copied)
    return adjusted_rows


def adjusted_close(row: dict[str, str] | None) -> float | None:
    if row is None:
        return None
    return parse_float(row.get("_effective_adjusted_close") or row.get("adjusted_close"))


def price_on_or_before(rows: list[dict[str, str]], target: date) -> dict[str, str] | None:
    latest: dict[str, str] | None = None
    for row in rows:
        row_date = price_date(row)
        if row_date is None:
            continue
        if row_date > target:
            break
        latest = row
    return latest


def return_with_skip(
    rows: list[dict[str, str]],
    calendar: list[date],
    rebalance_date: date,
    lookback_days: int,
    skip_days: int,
) -> float | None:
    end_date = trading_day_offset(calendar, rebalance_date, -skip_days, mode="on_or_before")
    start_date = trading_day_offset(calendar, rebalance_date, -lookback_days, mode="on_or_before")
    if start_date is None or end_date is None or start_date >= end_date:
        return None
    start_row = price_on_or_before(rows, start_date)
    end_row = price_on_or_before(rows, end_date)
    if start_row is None or end_row is None:
        return None
    end_price = adjusted_close(end_row)
    start_price = adjusted_close(start_row)
    if end_price is None or start_price is None or start_price <= 0:
        return None
    return end_price / start_price - 1.0


def latest_fundamental(rows: list[dict[str, str]], rebalance_date: date) -> dict[str, str] | None:
    candidates = []
    for row in rows:
        available_date = parse_date(fundamental_available_date(row), field_name="fundamentals.available_date")
        if available_date and available_date <= rebalance_date:
            candidates.append(row)
    if not candidates:
        return None
    useful = [row for row in candidates if row_has_factor_values(row)]
    selected = useful or candidates
    return sorted(selected, key=fundamental_sort_key)[-1]


def row_has_factor_values(row: dict[str, str]) -> bool:
    return any(
        parse_float(row.get(column)) is not None
        for column in [
            "operating_profit",
            "net_profit",
            "equity",
            "total_assets",
            "shares_outstanding",
            "avg_shares",
        ]
    )


def fundamental_available_date(row: dict[str, str]) -> str:
    return row.get("available_date") or row.get("disclosure_date") or ""


def fundamental_sort_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        fundamental_available_date(row),
        row.get("available_time", ""),
        row.get("period_end", ""),
        row.get("disclosure_number", ""),
    )


def first_number(*values: Any) -> float | None:
    for value in values:
        parsed = parse_float(value)
        if parsed is not None:
            return parsed
    return None


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def factor_output_fields(config: dict[str, Any] | None = None) -> list[str]:
    custom_fields = factor_definition_names(config)
    return [
        *FACTOR_METADATA_FIELDS,
        *BASE_FACTOR_FIELDS,
        *[field for field in custom_fields if field not in BASE_FACTOR_FIELDS],
        "missing_flags",
    ]


def validate_custom_factor_names(config: dict[str, Any] | None) -> None:
    reserved = set(FACTOR_METADATA_FIELDS) | set(BASE_FACTOR_FIELDS) | {"missing_flags"}
    duplicates = sorted(set(factor_definition_names(config)) & reserved)
    if duplicates:
        raise ValueError(f"Configured factor definitions duplicate reserved factor fields: {', '.join(duplicates)}")


def build_factors(
    *,
    rebalance_date: date,
    universe_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    validate_custom_factor_names(config)
    validate_unique_price_rows(price_rows)
    validate_unique_fundamental_rows(fundamental_rows)
    custom_definitions = ordered_factor_definitions(
        config,
        base_variables=FACTOR_EXPRESSION_BASE_FIELDS,
        functions={"ts_return"},
    )
    prices_by_code = group_by_code(price_rows)
    calendar = trading_calendar_from_rows(price_rows)
    fundamentals_by_code = group_by_code(fundamental_rows)
    factor_rows: list[dict[str, Any]] = []

    for asset in universe_rows:
        code = asset.get("code", "")
        prices = rows_until_rebalance(prices_by_code.get(code, []), rebalance_date)
        latest_price_row = prices[-1] if prices else None
        latest_close = parse_float(asset.get("latest_unadjusted_close")) or adjusted_close(latest_price_row)
        fundamental = latest_fundamental(fundamentals_by_code.get(code, []), rebalance_date)

        operating_profit = first_number(fundamental.get("operating_profit") if fundamental else None)
        net_profit = first_number(fundamental.get("net_profit") if fundamental else None)
        equity = first_number(fundamental.get("equity") if fundamental else None)
        total_assets = first_number(fundamental.get("total_assets") if fundamental else None)
        shares = first_number(
            fundamental.get("shares_outstanding") if fundamental else None,
            fundamental.get("avg_shares") if fundamental else None,
        )
        market_cap = latest_close * shares if latest_close is not None and shares is not None else None

        raw_values = {
            "operating_profit_to_total_assets": safe_ratio(operating_profit, total_assets),
            "equity_to_assets": safe_ratio(equity, total_assets),
            "earnings_yield": safe_ratio(net_profit, market_cap),
            "book_to_market": safe_ratio(equity, market_cap),
            "return_12_1": return_with_skip(
                prices,
                calendar,
                rebalance_date,
                TRADING_DAYS_12M,
                TRADING_DAYS_1M,
            ),
            "return_6_1": return_with_skip(
                prices,
                calendar,
                rebalance_date,
                TRADING_DAYS_6M,
                TRADING_DAYS_1M,
            ),
        }
        variables = {
            "latest_unadjusted_close": latest_close,
            "market_cap": market_cap,
            "operating_profit": operating_profit,
            "net_profit": net_profit,
            "equity": equity,
            "total_assets": total_assets,
            "shares": shares,
            **raw_values,
        }

        def configured_ts_return(lookback: Any, skip: Any = 0) -> float | None:
            lookback_days = int(parse_float(lookback) or 0)
            skip_days = int(parse_float(skip) or 0)
            if lookback_days <= 0 or skip_days < 0:
                return None
            return return_with_skip(prices, calendar, rebalance_date, lookback_days, skip_days)

        custom_values: dict[str, Any] = {}
        for definition in custom_definitions:
            value = evaluate_factor_expression(
                definition.expr,
                variables,
                functions={"ts_return": configured_ts_return},
            )
            custom_values[definition.name] = value
            variables[definition.name] = value

        all_factor_values = {**raw_values, **custom_values}
        missing_flags = [name for name, value in all_factor_values.items() if value is None]

        factor_rows.append(
            {
                "rebalance_date": rebalance_date,
                "code": code,
                "name": asset.get("name", ""),
                "market": asset.get("market", ""),
                "sector": asset.get("sector", ""),
                "price_date": latest_price_row.get("date", "") if latest_price_row else "",
                "latest_unadjusted_close": latest_close,
                "fundamentals_available_date": fundamental_available_date(fundamental) if fundamental else "",
                "fundamentals_available_time": fundamental.get("available_time", "") if fundamental else "",
                "document_type": fundamental.get("document_type", "") if fundamental else "",
                "period_end": fundamental.get("period_end", "") if fundamental else "",
                "disclosure_number": fundamental.get("disclosure_number", "") if fundamental else "",
                "market_cap": market_cap,
                "operating_profit": operating_profit,
                "net_profit": net_profit,
                "equity": equity,
                "total_assets": total_assets,
                "shares": shares,
                **raw_values,
                **custom_values,
                "missing_flags": ";".join(missing_flags),
            }
        )
    return factor_rows


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")

    rows = build_factors(
        rebalance_date=rebalance_date,
        universe_rows=read_csv(args.universe),
        price_rows=read_csv(args.prices),
        fundamental_rows=read_csv(args.fundamentals),
        config=config,
    )
    suffix = month_key(rebalance_date)
    output_path = args.out_dir / f"factors_{suffix}.csv"
    fieldnames = factor_output_fields(config)
    write_csv(output_path, [{key: fmt(value) for key, value in row.items()} for row in rows], fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_factors",
            file_path=output_path,
            vendor="local",
            schema_version="factors_v0_1",
            date_range=args.rebalance_date,
            notes=f"{len(rows)} rows",
        )
    print(f"Wrote {len(rows)} factor rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
