from __future__ import annotations

import argparse
from pathlib import Path

from research_common import append_manifest, date_range_from_rows, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register a data file in data_manifest.csv.")
    parser.add_argument("--file", required=True, type=Path, help="Data file to register.")
    parser.add_argument("--source", required=True, help="Logical source name, e.g. jquants_prices.")
    parser.add_argument("--vendor", required=True, help="Vendor or origin, e.g. J-Quants or manual.")
    parser.add_argument("--schema-version", required=True, help="Schema version for this file.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest/data_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument("--date-column", help="Optional CSV date column used to infer date_range.")
    parser.add_argument("--date-range", help="Explicit date range, overrides --date-column.")
    parser.add_argument("--notes", default="", help="Free-form notes.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = args.file
    if not path.exists():
        raise FileNotFoundError(path)

    date_range = args.date_range or ""
    if not date_range and args.date_column:
        date_range = date_range_from_rows(read_csv(path), args.date_column)

    append_manifest(
        args.manifest,
        source=args.source,
        file_path=path,
        vendor=args.vendor,
        schema_version=args.schema_version,
        date_range=date_range,
        notes=args.notes,
    )
    print(f"Registered {path} in {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
