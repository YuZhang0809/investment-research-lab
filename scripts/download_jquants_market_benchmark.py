from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from jquants_client import DEFAULT_API_KEY_ENV, require_api_key, request_paginated
from research_common import append_manifest, write_csv


BENCHMARK_FIELDS = [
    "date",
    "benchmark_id",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download J-Quants market benchmark series.")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--benchmark", choices=["topix"], default="topix")
    parser.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD or YYYYMMDD start date.")
    parser.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD or YYYYMMDD end date.")
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/jquants/contracts"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def normalize_date(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value)
    if " " in text:
        text = text.split(" ", 1)[0]
    return text[:10]


def compact_date(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())[:8]


def first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def convert_topix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in rows:
        close = first_value(row, "Close", "C")
        converted.append(
            {
                "date": normalize_date(row.get("Date")),
                "benchmark_id": "TOPIX",
                "open": first_value(row, "Open", "O"),
                "high": first_value(row, "High", "H"),
                "low": first_value(row, "Low", "L"),
                "close": close,
                "adjusted_close": close,
            }
        )
    converted.sort(key=lambda item: item["date"])
    return converted


def main() -> int:
    args = build_parser().parse_args()
    api_key = require_api_key(args.api_key_env)
    if args.benchmark != "topix":
        raise ValueError(f"Unsupported benchmark: {args.benchmark}")

    rows = request_paginated(
        api_key,
        "/indices/topix",
        {"from": args.from_date, "to": args.to_date},
    )
    converted = convert_topix(rows)
    start_token = compact_date(args.from_date)
    end_token = compact_date(args.to_date)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"market_benchmark_topix_{start_token}_{end_token}.csv"
    write_csv(out_path, converted, BENCHMARK_FIELDS)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="jquants_market_benchmark_topix",
            file_path=out_path,
            vendor="J-Quants API V2",
            schema_version="market_benchmark_contract_v0_1",
            date_range=f"{args.from_date}..{args.to_date}",
            notes=f"{len(converted)} rows; generated_at={datetime.now().isoformat(timespec='seconds')}",
        )
    print(f"Wrote {len(converted)} TOPIX benchmark rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
