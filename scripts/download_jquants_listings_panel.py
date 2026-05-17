from __future__ import annotations

import argparse
import calendar
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from time import sleep
from typing import Any

from download_jquants import convert_master, normalize_date
from jquants_client import DEFAULT_API_KEY_ENV, require_api_key, request_paginated
from research_common import append_manifest, parse_date, read_csv, write_csv


LISTING_FIELDS = [
    "code",
    "name",
    "market",
    "sector",
    "listed_date",
    "delisted_date",
    "security_type",
    "is_common_stock",
    "is_etf_reit_infra",
    "tradable_flag",
    "lot_size",
    "source_date",
    "listing_lifecycle_status",
    "market_code",
    "sector33_code",
    "scale_category",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download J-Quants listing snapshots into a source-dated panel.")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--date", action="append", dest="dates", help="Snapshot date. Can be repeated.")
    parser.add_argument("--dates-file", type=Path, help="CSV/text file with a date column or first-column dates.")
    parser.add_argument("--from", dest="from_date", help="YYYY-MM-DD start date for generated snapshot dates.")
    parser.add_argument("--to", dest="to_date", help="YYYY-MM-DD end date for generated snapshot dates.")
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="quarterly")
    parser.add_argument(
        "--calendar",
        type=Path,
        help="Optional CSV/Parquet with a date column. Uses the last available date per month/quarter.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--out", type=Path, help="Output CSV path.")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def compact_date(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())[:8]


def read_dates_file(path: Path) -> list[str]:
    rows = path.read_text(encoding="utf-8-sig").splitlines()
    values: list[str] = []
    for index, line in enumerate(rows):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [part.strip() for part in stripped.split(",")]
        if index == 0 and parts[0].lower() == "date":
            continue
        values.append(parts[0])
    return values


def generated_calendar_month_ends(start: date, end: date, frequency: str) -> list[date]:
    values: list[date] = []
    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        if frequency == "monthly" or month in {3, 6, 9, 12}:
            last_day = calendar.monthrange(year, month)[1]
            candidate = date(year, month, last_day)
            if start <= candidate <= end:
                values.append(candidate)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return values


def calendar_snapshot_dates(calendar_path: Path, start: date, end: date, frequency: str) -> list[date]:
    groups: dict[str, date] = {}
    for row in read_csv(calendar_path):
        row_date = parse_date(row.get("date"), field_name="calendar.date")
        if row_date is None or row_date < start or row_date > end:
            continue
        if frequency == "quarterly" and row_date.month not in {3, 6, 9, 12}:
            continue
        key = row_date.strftime("%Y-%m")
        groups[key] = max(groups.get(key, row_date), row_date)
    return [groups[key] for key in sorted(groups)]


def resolve_snapshot_dates(args: argparse.Namespace) -> list[str]:
    raw_values: list[str] = []
    if args.dates_file:
        raw_values.extend(read_dates_file(args.dates_file))
    if args.dates:
        raw_values.extend(args.dates)
    if args.from_date or args.to_date:
        if not args.from_date or not args.to_date:
            raise ValueError("Use both --from and --to when generating snapshot dates.")
        start = parse_date(args.from_date, field_name="from")
        end = parse_date(args.to_date, field_name="to")
        if start is None or end is None:
            raise ValueError("--from and --to must be YYYY-MM-DD or YYYYMMDD dates.")
        generated = (
            calendar_snapshot_dates(args.calendar, start, end, args.frequency)
            if args.calendar
            else generated_calendar_month_ends(start, end, args.frequency)
        )
        raw_values.extend(value.isoformat() for value in generated)

    clean: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        parsed = parse_date(value, field_name="snapshot_date")
        if parsed is None:
            continue
        text = parsed.isoformat()
        if text not in seen:
            seen.add(text)
            clean.append(text)
    return sorted(clean)


def default_output_path(dates: list[str], frequency: str) -> Path:
    start = compact_date(dates[0])
    end = compact_date(dates[-1])
    return Path("data/raw/jquants/contracts") / f"listings_panel_{frequency}_{start}_{end}.csv"


def main() -> int:
    args = build_parser().parse_args()
    dates = resolve_snapshot_dates(args)
    if not dates:
        raise ValueError("No snapshot dates resolved. Pass --date/--dates-file or --from/--to.")
    api_key = require_api_key(args.api_key_env)

    panel_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, snapshot_date in enumerate(dates, start=1):
        try:
            raw_rows = request_paginated(api_key, "/equities/master", {"date": snapshot_date})
            converted = convert_master(raw_rows, snapshot_date)
            for row in converted:
                row["source_date"] = normalize_date(row.get("source_date") or snapshot_date)
                if row.get("listing_lifecycle_status") == "snapshot_only_missing_lifecycle_dates":
                    row["listing_lifecycle_status"] = "pit_snapshot_panel_missing_lifecycle_dates"
            panel_rows.extend(converted)
            print(f"[{index}/{len(dates)}] {snapshot_date}: {len(converted)} listings")
        except Exception as exc:
            message = f"{snapshot_date}: {exc}"
            errors.append(message)
            print(f"[{index}/{len(dates)}] {message}")
            if not args.continue_on_error:
                raise
        if args.sleep_seconds > 0 and index < len(dates):
            sleep(args.sleep_seconds)

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in panel_rows:
        key = (str(row.get("source_date") or ""), str(row.get("code") or ""))
        by_key[key] = row
    deduped = sorted(by_key.values(), key=lambda row: (row.get("source_date", ""), row.get("code", "")))

    out_path = args.out or default_output_path(dates, args.frequency)
    write_csv(out_path, deduped, LISTING_FIELDS)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="jquants_listings_panel",
            file_path=out_path,
            vendor="J-Quants API V2",
            schema_version="jquants_listings_panel_contract_v0_1",
            date_range=f"{dates[0]}..{dates[-1]}",
            notes=(
                f"{len(deduped)} rows; {len(dates)} requested snapshots; "
                f"errors={len(errors)}; generated_at={datetime.now().isoformat(timespec='seconds')}"
            ),
        )
    print(f"Wrote {len(deduped)} listing panel rows to {out_path}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
