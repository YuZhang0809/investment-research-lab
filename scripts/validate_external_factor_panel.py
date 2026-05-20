from __future__ import annotations

import argparse
from pathlib import Path

from external_factor_panels import SUPPORTED_DTYPES, ExternalFactorField, coerce_field_value, normalize_key_value
from research_common import parse_date, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a generic external factor panel contract.")
    parser.add_argument("--panel", required=True, type=Path, help="CSV or Parquet external factor panel.")
    parser.add_argument("--join-key", action="append", required=True, help="Join key column. Repeat for composite keys.")
    parser.add_argument(
        "--field",
        action="append",
        required=True,
        help="Field contract as name:dtype. Dtypes: float, int, string, bool.",
    )
    parser.add_argument(
        "--asof-date-field",
        help="When set, validate this date column and duplicate keys as join keys plus as-of date.",
    )
    return parser


def parse_field_contracts(values: list[str]) -> list[ExternalFactorField]:
    fields: list[ExternalFactorField] = []
    seen: set[str] = set()
    for value in values:
        if ":" not in value:
            raise ValueError(f"--field must be name:dtype, got {value!r}.")
        name, dtype = [part.strip() for part in value.split(":", 1)]
        if not name:
            raise ValueError("--field name cannot be blank.")
        if dtype.lower() not in SUPPORTED_DTYPES:
            raise ValueError(f"Unsupported --field dtype for {name}: {dtype}")
        if name in seen:
            raise ValueError(f"Duplicate --field name: {name}")
        seen.add(name)
        fields.append(ExternalFactorField(name=name, dtype=dtype.lower()))
    return fields


def validate_panel(
    *,
    panel: Path,
    join_keys: list[str],
    fields: list[ExternalFactorField],
    asof_date_field: str | None,
) -> int:
    rows = read_csv(panel)
    if not rows:
        raise ValueError(f"External factor panel is empty: {panel}")
    required = set(join_keys) | {field.name for field in fields}
    if asof_date_field:
        required.add(asof_date_field)
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"External factor panel missing required field(s): {', '.join(missing)}")
    seen: set[tuple[str, ...]] = set()
    duplicate_key_fields = [*join_keys, *([asof_date_field] if asof_date_field else [])]
    for row_number, row in enumerate(rows, start=2):
        for key in join_keys:
            if normalize_key_value(row.get(key), field_name=key) == "":
                raise ValueError(f"Blank join key {key!r} at row {row_number}.")
        if asof_date_field:
            if parse_date(row.get(asof_date_field), field_name=asof_date_field) is None:
                raise ValueError(f"Blank or invalid as-of date {asof_date_field!r} at row {row_number}.")
        for field in fields:
            coerce_field_value(row.get(field.name), field)
        duplicate_key = tuple(normalize_key_value(row.get(key), field_name=key) for key in duplicate_key_fields)
        if duplicate_key in seen:
            detail = ";".join(f"{key}={value}" for key, value in zip(duplicate_key_fields, duplicate_key))
            raise ValueError(f"Duplicate external factor panel rows for {detail}.")
        seen.add(duplicate_key)
    return len(rows)


def main() -> int:
    args = build_parser().parse_args()
    fields = parse_field_contracts(args.field)
    row_count = validate_panel(
        panel=args.panel,
        join_keys=[str(value).strip() for value in args.join_key if str(value).strip()],
        fields=fields,
        asof_date_field=args.asof_date_field,
    )
    print(f"Validated {row_count} external factor panel rows from {args.panel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
