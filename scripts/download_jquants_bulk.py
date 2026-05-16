from __future__ import annotations

import argparse
import csv
import gzip
import io
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from download_jquants import convert_fundamentals, read_codes_file, token_for_codes
from jquants_client import DEFAULT_API_KEY_ENV, request_json, require_api_key
from research_common import append_manifest, write_csv


FUNDAMENTAL_FIELDS = [
    "code",
    "available_date",
    "available_time",
    "document_type",
    "period_type",
    "period_start",
    "period_end",
    "fiscal_year_start",
    "fiscal_year_end",
    "sales",
    "operating_profit",
    "ordinary_profit",
    "net_profit",
    "equity",
    "total_assets",
    "eps",
    "bps",
    "shares_outstanding",
    "avg_shares",
    "disclosure_number",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download J-Quants Bulk CSV files and convert them to v0.1 contracts.")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--endpoint", default="/fins/summary", choices=["/fins/summary"])
    parser.add_argument("--from", dest="from_date", required=True, help="Bulk list start date, e.g. 2016-05.")
    parser.add_argument("--to", dest="to_date", required=True, help="Bulk list end date, e.g. 2026-05.")
    parser.add_argument("--codes-file", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/jquants/contracts"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def download_gzip_csv(url: str) -> list[dict[str, str]]:
    with urllib.request.urlopen(url, timeout=120) as response:
        raw = response.read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def main() -> int:
    args = build_parser().parse_args()
    api_key = require_api_key(args.api_key_env)
    codes = read_codes_file(args.codes_file)
    code_set = set(codes)
    payload = request_json(
        api_key,
        "/bulk/list",
        {"endpoint": args.endpoint, "from": args.from_date, "to": args.to_date},
    )
    files = payload.get("data", [])
    if not isinstance(files, list) or not files:
        raise ValueError(f"No bulk files returned for {args.endpoint} {args.from_date}..{args.to_date}")

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(files, start=1):
        key = item.get("Key")
        if not key:
            continue
        url_payload = request_json(api_key, "/bulk/get", {"key": str(key)})
        url = url_payload.get("url")
        if not url:
            raise ValueError(f"No download URL returned for {key}")
        raw_rows = download_gzip_csv(str(url))
        filtered = [row for row in raw_rows if row.get("Code") in code_set]
        rows.extend(filtered)
        print(f"[{index}/{len(files)}] {key}: {len(raw_rows)} rows, {len(filtered)} selected")

    converted = convert_fundamentals(rows)
    converted.sort(key=lambda row: (row.get("code", ""), row.get("available_date", ""), row.get("available_time", "")))

    token = token_for_codes(codes)
    start_token = args.from_date.replace("-", "")
    end_token = args.to_date.replace("-", "")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"fundamentals_bulk_{token}_{start_token}_{end_token}.csv"
    write_csv(out_path, converted, FUNDAMENTAL_FIELDS)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="jquants_bulk_fundamentals",
            file_path=out_path,
            vendor="J-Quants API V2 Bulk",
            schema_version="jquants_fundamentals_contract_v0_1",
            date_range=f"{args.from_date}..{args.to_date}",
            notes=f"{len(converted)} rows; {len(files)} bulk files; generated_at={datetime.now().isoformat(timespec='seconds')}",
        )
    print(f"Wrote {len(converted)} fundamentals rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
