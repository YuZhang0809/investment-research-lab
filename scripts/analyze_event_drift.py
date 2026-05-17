from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from research_common import (
    append_manifest,
    parse_date,
    parse_float,
    read_csv,
    trading_calendar_from_rows,
    trading_day_offset,
    write_csv,
)


WINDOWS = [1, 5, 20, 60]


@dataclass
class PricePoint:
    date: date
    adjusted_close: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append post-event drift windows to a TDnet event CSV.")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/events"))
    parser.add_argument("--run-label", default="observation")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def parse_announcement_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid announcement_datetime: {value!r}")


def build_price_index(rows: list[dict[str, str]]) -> dict[str, list[PricePoint]]:
    grouped: dict[str, list[PricePoint]] = defaultdict(list)
    for row in rows:
        code = row.get("code", "")
        row_date = parse_date(row.get("date"), field_name="prices.date")
        adjusted = parse_float(row.get("adjusted_close") or row.get("unadjusted_close"))
        if not code or row_date is None or adjusted is None or adjusted <= 0:
            continue
        grouped[code].append(PricePoint(row_date, adjusted))
    for points in grouped.values():
        points.sort(key=lambda point: point.date)
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


def drift_return(
    points: list[PricePoint],
    calendar: list[date],
    entry_date: date,
    window: int,
) -> tuple[float | None, str]:
    entry_point = price_on_date(points, entry_date)
    if entry_point is None:
        return None, "missing_entry_price"
    exit_date = trading_day_offset(calendar, entry_date, window, mode="on_or_after")
    if exit_date is None:
        return None, "insufficient_forward_window"
    exit_point = price_on_date(points, exit_date)
    if exit_point is None:
        if not has_price_after(points, exit_date) and points[-1].date < exit_date:
            return None, "price_tail_gap"
        return None, "missing_exit_price"
    entry = entry_point.adjusted_close
    exit_value = exit_point.adjusted_close
    if entry <= 0:
        return None, "invalid_entry_price"
    return exit_value / entry - 1.0, "ok"


def fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.10g}"


def main() -> int:
    args = build_parser().parse_args()
    event_rows = read_csv(args.events)
    price_rows = read_csv(args.prices)
    price_index = build_price_index(price_rows)
    calendar = trading_calendar_from_rows(price_rows)
    output_rows: list[dict[str, str]] = []
    for row in event_rows:
        event_date = parse_announcement_date(row.get("announcement_datetime", ""))
        points = price_index.get(row.get("code", ""), [])
        entry_date = trading_day_offset(calendar, event_date, 0, mode="after") if event_date is not None else None
        enriched = dict(row)
        if entry_date is not None:
            enriched["entry_date"] = entry_date.isoformat()
            enriched["entry_status"] = "ok" if price_on_date(points, entry_date) else "missing_entry_price"
            for window in WINDOWS:
                value, status = drift_return(points, calendar, entry_date, window)
                enriched[f"next_{window}d_return"] = fmt(value)
                enriched[f"next_{window}d_status"] = status
        else:
            enriched["entry_date"] = ""
            enriched["entry_status"] = "missing_entry_date"
            for window in WINDOWS:
                enriched[f"next_{window}d_return"] = ""
                enriched[f"next_{window}d_status"] = "missing_entry_date"
        output_rows.append(enriched)

    suffix = args.run_label
    if output_rows:
        dates = [parse_announcement_date(row.get("announcement_datetime", "")) for row in output_rows]
        clean_dates = [value for value in dates if value is not None]
        if clean_dates:
            suffix = f"{min(clean_dates).strftime('%Y%m')}_{max(clean_dates).strftime('%Y%m')}"
    output_path = args.out_dir / f"tdnet_event_drift_{suffix}.csv"
    fieldnames = [
        "event_id",
        "announcement_datetime",
        "entry_date",
        "entry_status",
        "code",
        "company_name",
        "document_type",
        "event_label",
        "title",
        "url_or_doc_id",
        "parsed_flag",
        "parse_confidence",
        "notes",
        "next_1d_return",
        "next_1d_status",
        "next_5d_return",
        "next_5d_status",
        "next_20d_return",
        "next_20d_status",
        "next_60d_return",
        "next_60d_status",
    ]
    write_csv(output_path, output_rows, fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_tdnet_event_drift",
            file_path=output_path,
            vendor="local",
            schema_version="tdnet_event_drift_v0_1",
            date_range=suffix,
            notes=f"{len(output_rows)} event rows; adjusted-close drift windows",
        )
    print(f"Wrote {len(output_rows)} event drift rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
