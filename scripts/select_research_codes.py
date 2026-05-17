from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

from research_common import parse_bool, read_csv, write_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select strategy-agnostic research codes from a listings panel.")
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--as-of", help="Optional source_date cutoff. Defaults to all historical snapshots.")
    parser.add_argument("--min-snapshots", type=int, default=1)
    parser.add_argument("--include-metadata", action="store_true")
    parser.add_argument("--max-codes", type=int)
    return parser


def normalize_kind(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def is_common_research_stock(row: dict[str, str]) -> bool:
    if parse_bool(row.get("is_common_stock"), default=False) is not True:
        return False
    if parse_bool(row.get("is_etf_reit_infra"), default=False):
        return False
    security_type = normalize_kind(row.get("security_type"))
    if security_type and security_type != "common_stock":
        return False
    return True


def latest(values: list[str]) -> str:
    clean = [value for value in values if value]
    return clean[-1] if clean else ""


def main() -> int:
    args = build_parser().parse_args()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(args.listings):
        code = (row.get("code") or "").strip()
        if not code:
            continue
        source_date = row.get("source_date") or ""
        if args.as_of and source_date and source_date > args.as_of:
            continue
        if not is_common_research_stock(row):
            continue
        grouped[code].append(row)

    rows: list[dict[str, Any]] = []
    for code, values in grouped.items():
        values.sort(key=lambda row: row.get("source_date") or "")
        snapshot_count = len({row.get("source_date") for row in values if row.get("source_date")})
        if snapshot_count < args.min_snapshots:
            continue
        rows.append(
            {
                "code": code,
                "first_seen": values[0].get("source_date", ""),
                "last_seen": values[-1].get("source_date", ""),
                "snapshot_count": snapshot_count,
                "name": latest([row.get("name", "") for row in values]),
                "market": latest([row.get("market", "") for row in values]),
                "sector": latest([row.get("sector", "") for row in values]),
                "security_type": latest([row.get("security_type", "") for row in values]),
            }
        )

    rows.sort(key=lambda row: row["code"])
    if args.max_codes is not None:
        rows = rows[: args.max_codes]
    if args.include_metadata:
        fieldnames = ["code", "first_seen", "last_seen", "snapshot_count", "name", "market", "sector", "security_type"]
        output_rows = rows
    else:
        fieldnames = ["code"]
        output_rows = [{"code": row["code"]} for row in rows]
    write_csv(args.out, output_rows, fieldnames)
    print(f"Selected {len(output_rows)} research codes into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
