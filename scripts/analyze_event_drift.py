from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from research_common import append_manifest, parse_date, parse_float, read_csv, write_csv


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


def first_after(points: list[PricePoint], event_date: date) -> int | None:
    for index, point in enumerate(points):
        if point.date > event_date:
            return index
    return None


def drift_return(points: list[PricePoint], entry_index: int, window: int) -> float | None:
    exit_index = entry_index + window
    if entry_index >= len(points) or exit_index >= len(points):
        return None
    entry = points[entry_index].adjusted_close
    exit_value = points[exit_index].adjusted_close
    if entry <= 0:
        return None
    return exit_value / entry - 1.0


def fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.10g}"


def main() -> int:
    args = build_parser().parse_args()
    event_rows = read_csv(args.events)
    price_index = build_price_index(read_csv(args.prices))
    output_rows: list[dict[str, str]] = []
    for row in event_rows:
        event_date = parse_announcement_date(row.get("announcement_datetime", ""))
        points = price_index.get(row.get("code", ""), [])
        entry_index = first_after(points, event_date) if event_date is not None else None
        enriched = dict(row)
        if entry_index is not None:
            enriched["entry_date"] = points[entry_index].date.isoformat()
            for window in WINDOWS:
                enriched[f"next_{window}d_return"] = fmt(drift_return(points, entry_index, window))
        else:
            enriched["entry_date"] = ""
            for window in WINDOWS:
                enriched.setdefault(f"next_{window}d_return", "")
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
        "next_5d_return",
        "next_20d_return",
        "next_60d_return",
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
