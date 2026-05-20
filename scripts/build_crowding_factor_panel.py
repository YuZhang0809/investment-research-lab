from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from research_common import append_manifest, parse_date, parse_float, read_csv, write_table


FIELDNAMES = [
    "rebalance_date",
    "code",
    "available_date",
    "margin_buy_balance_to_volume",
    "margin_sell_balance_to_volume",
    "short_interest_to_volume",
    "crowding_raw",
    "crowding_zscore",
    "crowding_change",
    "missing_flags",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build generic margin/short-interest crowding factor panels.")
    parser.add_argument("--crowding-panel", required=True, type=Path)
    parser.add_argument("--prices", type=Path, help="Optional price/volume panel used when crowding rows do not carry volume.")
    parser.add_argument("--rebalance-dates", type=Path, help="CSV/Parquet with rebalance_date or date column.")
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values", help="YYYY-MM-DD; can be repeated.")
    parser.add_argument("--max-lag-days", type=int, default=30)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--run-label", default="crowding")
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


def first_number(row: dict[str, Any], *fields: str) -> float | None:
    for field in fields:
        value = parse_float(row.get(field))
        if value is not None:
            return value
    return None


def first_text(row: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def row_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(
        row.get("available_date") or row.get("date") or row.get("Date") or row.get("ApplicationDate") or row.get("PublishedDate"),
        "crowding.available_date",
    )


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


def price_volume_index(price_rows: list[dict[str, str]] | None) -> dict[tuple[str, date], float]:
    output: dict[tuple[str, date], float] = {}
    for row in price_rows or []:
        code = first_text(row, "code", "Code", "LocalCode")
        date_value = parse_optional_date(row.get("date") or row.get("Date"), "prices.date")
        volume = first_number(row, "volume", "trading_volume", "Volume", "TradingVolume")
        if code and date_value is not None and volume is not None and volume > 0:
            output[(code, date_value)] = volume
    return output


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def fmt(value: float | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def zscores(values: dict[str, float]) -> dict[str, float | None]:
    if len(values) < 2:
        return {key: None for key in values}
    sigma = stdev(values.values())
    if sigma == 0:
        return {key: None for key in values}
    mean = fmean(values.values())
    return {key: (value - mean) / sigma for key, value in values.items()}


def build_panel(
    crowding_rows: list[dict[str, str]],
    *,
    rebalance_dates: list[date],
    price_rows: list[dict[str, str]] | None = None,
    max_lag_days: int = 30,
) -> list[dict[str, str]]:
    if max_lag_days < 0:
        raise ValueError("max_lag_days cannot be negative.")
    volumes = price_volume_index(price_rows)
    by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in crowding_rows:
        code = first_text(row, "code", "Code", "LocalCode")
        if code and row_date(row) is not None:
            by_code[code].append(row)
    for rows in by_code.values():
        rows.sort(key=lambda item: row_date(item) or date.min)

    output: list[dict[str, str]] = []
    previous_raw_by_code: dict[str, float] = {}
    for rebalance_date in rebalance_dates:
        rows_for_date: list[dict[str, str]] = []
        raw_by_code: dict[str, float] = {}
        for code in sorted(by_code):
            candidates = [row for row in by_code[code] if row_date(row) is not None and row_date(row) <= rebalance_date]
            if not candidates:
                continue
            selected = candidates[-1]
            selected_date = row_date(selected)
            if selected_date is None or (rebalance_date - selected_date).days > max_lag_days:
                continue
            volume = first_number(selected, "volume", "trading_volume", "Volume", "TradingVolume")
            if volume is None:
                volume = volumes.get((code, selected_date))
            long_balance = first_number(selected, "long_margin_balance", "LongMarginTradeVolume", "LongMarginOutstanding")
            short_balance = first_number(selected, "short_margin_balance", "ShortMarginTradeVolume", "ShortMarginOutstanding")
            short_interest = first_number(selected, "short_interest", "ShortInterest", "ShortPosition")
            buy_ratio = safe_ratio(long_balance, volume)
            sell_ratio = safe_ratio(short_balance, volume)
            short_interest_ratio = safe_ratio(short_interest, volume)
            components = [value for value in [buy_ratio, sell_ratio, short_interest_ratio] if value is not None]
            raw = fmean(components) if components else None
            missing = []
            if volume is None:
                missing.append("missing_volume")
            if buy_ratio is None:
                missing.append("margin_buy_balance_to_volume")
            if sell_ratio is None:
                missing.append("margin_sell_balance_to_volume")
            if short_interest_ratio is None:
                missing.append("short_interest_to_volume")
            if raw is None:
                missing.append("crowding_raw")
            else:
                raw_by_code[code] = raw
            rows_for_date.append(
                {
                    "rebalance_date": rebalance_date.isoformat(),
                    "code": code,
                    "available_date": selected_date.isoformat(),
                    "margin_buy_balance_to_volume": fmt(buy_ratio),
                    "margin_sell_balance_to_volume": fmt(sell_ratio),
                    "short_interest_to_volume": fmt(short_interest_ratio),
                    "crowding_raw": fmt(raw),
                    "crowding_zscore": "",
                    "crowding_change": fmt(raw - previous_raw_by_code[code]) if raw is not None and code in previous_raw_by_code else "",
                    "missing_flags": ";".join(dict.fromkeys(missing)),
                }
            )
        z_by_code = zscores(raw_by_code)
        for row in rows_for_date:
            if row["code"] in z_by_code:
                row["crowding_zscore"] = fmt(z_by_code[row["code"]])
            if row["crowding_zscore"] == "" and row["crowding_raw"]:
                flags = [value for value in row["missing_flags"].split(";") if value]
                row["missing_flags"] = ";".join(dict.fromkeys([*flags, "crowding_zscore"]))
        previous_raw_by_code = raw_by_code
        output.extend(rows_for_date)
    output.sort(key=lambda row: (row["rebalance_date"], row["code"]))
    return output


def output_date_range(rows: list[dict[str, str]]) -> str:
    values = sorted(row["rebalance_date"] for row in rows if row.get("rebalance_date"))
    if not values:
        return ""
    return f"{values[0]}..{values[-1]}"


def main() -> int:
    args = build_parser().parse_args()
    rows = build_panel(
        read_csv(args.crowding_panel),
        rebalance_dates=load_rebalance_dates(args.rebalance_dates, args.rebalance_date_values),
        price_rows=read_csv(args.prices) if args.prices else None,
        max_lag_days=args.max_lag_days,
    )
    write_table(rows, args.out, format=args.output_format, fieldnames=FIELDNAMES)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="crowding_factor_panel",
            file_path=args.out,
            vendor="local",
            schema_version="crowding_factor_panel_v0_1",
            date_range=output_date_range(rows) or args.run_label,
            notes=f"{len(rows)} rows",
        )
    print(f"Wrote {len(rows)} crowding factor rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
