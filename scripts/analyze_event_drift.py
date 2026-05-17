from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
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
REQUIRED_EVENT_FIELDS = {"event_id", "announcement_datetime", "code", "event_label"}


@dataclass
class PricePoint:
    date: date
    adjusted_close: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append post-event drift windows to a TDnet event CSV.")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--window", action="append", type=int, dest="windows", help="Trading-day window. Can be repeated.")
    parser.add_argument("--entry-mode", choices=["next_trading_day", "same_day_if_before_cutoff"], default="next_trading_day")
    parser.add_argument("--same-day-cutoff", default="15:00", help="HH:MM cutoff for same_day_if_before_cutoff.")
    parser.add_argument("--overlap-window-days", type=int, default=60)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/events"))
    parser.add_argument("--run-label", default="observation")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def require_event_columns(rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("events is empty.")
    missing = sorted(REQUIRED_EVENT_FIELDS - set(rows[0]))
    if missing:
        raise ValueError(f"events is missing required column(s): {', '.join(missing)}")


def parse_windows(values: list[int] | None) -> list[int]:
    windows = values or WINDOWS
    clean = sorted(set(windows))
    if not clean or any(value <= 0 for value in clean):
        raise ValueError("--window values must be positive.")
    return clean


def parse_cutoff(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"Invalid --same-day-cutoff: {value!r}; expected HH:MM") from exc


def parse_announcement_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid announcement_datetime: {value!r}")


def parse_announcement_date(value: str) -> date | None:
    parsed = parse_announcement_datetime(value)
    if parsed is None:
        return None
    return parsed.date()


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


def entry_trading_date(
    announcement: datetime | None,
    calendar: list[date],
    *,
    entry_mode: str,
    same_day_cutoff: time,
) -> date | None:
    if announcement is None:
        return None
    if entry_mode == "next_trading_day":
        return trading_day_offset(calendar, announcement.date(), 0, mode="after")
    if entry_mode == "same_day_if_before_cutoff" and announcement.time() < same_day_cutoff:
        return trading_day_offset(calendar, announcement.date(), 0, mode="on_or_after")
    return trading_day_offset(calendar, announcement.date(), 0, mode="after")


def tradable_timestamp(announcement: datetime | None, entry_date: date) -> str:
    market_open = datetime.combine(entry_date, time(9, 0))
    if announcement is not None and announcement.date() == entry_date and announcement > market_open:
        return announcement.isoformat(sep=" ")
    return market_open.isoformat(sep=" ")


def overlap_metadata(rows: list[dict[str, str]], *, overlap_window_days: int) -> dict[int, dict[str, int]]:
    if overlap_window_days <= 0:
        raise ValueError("--overlap-window-days must be positive.")
    parsed: list[tuple[int, str, str, datetime]] = []
    for index, row in enumerate(rows):
        announcement = parse_announcement_datetime(row.get("announcement_datetime", ""))
        code = (row.get("code") or "").strip()
        label = (row.get("event_label") or "").strip()
        if announcement is not None and code:
            parsed.append((index, code, label, announcement))
    duplicate_counts: dict[tuple[str, str, date], int] = defaultdict(int)
    for _index, code, label, announcement in parsed:
        duplicate_counts[(code, label, announcement.date())] += 1
    output: dict[int, dict[str, int]] = {}
    for index, code, label, announcement in parsed:
        overlaps = [
            item
            for item in parsed
            if item[0] != index
            and item[1] == code
            and abs((item[3].date() - announcement.date()).days) <= overlap_window_days
        ]
        prior = [
            item
            for item in overlaps
            if item[3] < announcement
        ]
        output[index] = {
            "event_overlap_count": len(overlaps),
            "duplicate_event_count": duplicate_counts[(code, label, announcement.date())],
            "event_sequence_in_overlap_window": len(prior) + 1,
        }
    return output


def fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.10g}"


def main() -> int:
    args = build_parser().parse_args()
    windows = parse_windows(args.windows)
    same_day_cutoff = parse_cutoff(args.same_day_cutoff)
    event_rows = read_csv(args.events)
    require_event_columns(event_rows)
    price_rows = read_csv(args.prices)
    price_index = build_price_index(price_rows)
    calendar = trading_calendar_from_rows(price_rows)
    overlaps = overlap_metadata(event_rows, overlap_window_days=args.overlap_window_days)
    output_rows: list[dict[str, str]] = []
    for index, row in enumerate(event_rows):
        announcement = parse_announcement_datetime(row.get("announcement_datetime", ""))
        points = price_index.get(row.get("code", ""), [])
        entry_date = entry_trading_date(
            announcement,
            calendar,
            entry_mode=args.entry_mode,
            same_day_cutoff=same_day_cutoff,
        )
        enriched = dict(row)
        enriched.update({key: str(value) for key, value in overlaps.get(index, {
            "event_overlap_count": 0,
            "duplicate_event_count": 0,
            "event_sequence_in_overlap_window": 0,
        }).items()})
        if entry_date is not None:
            enriched["entry_date"] = entry_date.isoformat()
            enriched["tradable_timestamp"] = tradable_timestamp(announcement, entry_date)
            enriched["entry_status"] = "ok" if price_on_date(points, entry_date) else "missing_entry_price"
            for window in windows:
                value, status = drift_return(points, calendar, entry_date, window)
                enriched[f"next_{window}d_return"] = fmt(value)
                enriched[f"next_{window}d_status"] = status
        else:
            enriched["entry_date"] = ""
            enriched["tradable_timestamp"] = ""
            enriched["entry_status"] = "missing_entry_date"
            for window in windows:
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
        "tradable_timestamp",
        "entry_status",
        "event_overlap_count",
        "duplicate_event_count",
        "event_sequence_in_overlap_window",
        "code",
        "company_name",
        "document_type",
        "event_label",
        "title",
        "url_or_doc_id",
        "parsed_flag",
        "parse_confidence",
        "notes",
        *[field for window in windows for field in (f"next_{window}d_return", f"next_{window}d_status")],
    ]
    write_csv(output_path, output_rows, fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_tdnet_event_drift",
            file_path=output_path,
            vendor="local",
            schema_version="tdnet_event_drift_v0_2",
            date_range=suffix,
            notes=f"{len(output_rows)} event rows; windows={','.join(str(window) for window in windows)}",
        )
    print(f"Wrote {len(output_rows)} event drift rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
