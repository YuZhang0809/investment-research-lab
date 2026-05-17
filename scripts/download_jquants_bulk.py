from __future__ import annotations

import argparse
import csv
import gzip
import io
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from download_jquants import convert_fundamentals, convert_master, convert_prices, read_codes_file, token_for_codes
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

PRICE_FIELDS = [
    "date",
    "code",
    "unadjusted_open",
    "unadjusted_high",
    "unadjusted_low",
    "unadjusted_close",
    "adjusted_close",
    "volume",
    "trading_value",
    "adjustment_factor",
    "tradable_flag",
    "price_limit_flag",
    "upper_limit_flag",
    "lower_limit_flag",
]

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


ENDPOINT_CONFIG = {
    "/fins/summary": {
        "source": "jquants_bulk_fundamentals",
        "prefix": "fundamentals_bulk",
        "schema": "jquants_fundamentals_contract_v0_1",
        "fields": FUNDAMENTAL_FIELDS,
    },
    "/equities/bars/daily": {
        "source": "jquants_bulk_prices",
        "prefix": "prices_bulk",
        "schema": "jquants_prices_contract_v0_1",
        "fields": PRICE_FIELDS,
    },
    "/equities/master": {
        "source": "jquants_bulk_listings",
        "prefix": "listings_bulk",
        "schema": "jquants_listings_contract_v0_1",
        "fields": LISTING_FIELDS,
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download J-Quants Bulk CSV files and convert them to local contracts.")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--endpoint", default="/fins/summary", choices=sorted(ENDPOINT_CONFIG))
    parser.add_argument("--from", dest="from_date", required=True, help="Bulk list start date, e.g. 2016-05.")
    parser.add_argument("--to", dest="to_date", required=True, help="Bulk list end date, e.g. 2026-05.")
    parser.add_argument("--codes-file", type=Path, help="Optional CSV/text file of issue codes to keep.")
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/jquants/contracts"))
    parser.add_argument("--out", type=Path, help="Optional output CSV path.")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def download_gzip_csv(url: str) -> list[dict[str, str]]:
    with urllib.request.urlopen(url, timeout=120) as response:
        raw = response.read()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def endpoint_display_name(endpoint: str) -> str:
    return endpoint.strip("/").replace("/", "_")


def filter_codes(rows: list[dict[str, str]], code_set: set[str] | None) -> list[dict[str, str]]:
    if code_set is None:
        return rows
    return [row for row in rows if row.get("Code") in code_set]


def convert_endpoint_rows(endpoint: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if endpoint == "/fins/summary":
        converted = convert_fundamentals(rows)
        return sorted(converted, key=lambda row: (row.get("code", ""), row.get("available_date", ""), row.get("available_time", "")))
    if endpoint == "/equities/bars/daily":
        converted = convert_prices(rows)
        return sorted(converted, key=lambda row: (row.get("date", ""), row.get("code", "")))
    if endpoint == "/equities/master":
        converted = convert_master(rows, "")
        return sorted(converted, key=lambda row: (row.get("source_date", ""), row.get("code", "")))
    raise ValueError(f"Unsupported bulk endpoint: {endpoint}")


def main() -> int:
    args = build_parser().parse_args()
    api_key = require_api_key(args.api_key_env)
    codes = read_codes_file(args.codes_file) if args.codes_file else []
    code_set = set(codes) if codes else None
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
        filtered = filter_codes(raw_rows, code_set)
        rows.extend(filtered)
        print(f"[{index}/{len(files)}] {key}: {len(raw_rows)} rows, {len(filtered)} selected")

    converted = convert_endpoint_rows(args.endpoint, rows)

    token = token_for_codes(codes)
    start_token = args.from_date.replace("-", "")
    end_token = args.to_date.replace("-", "")
    endpoint_config = ENDPOINT_CONFIG[args.endpoint]
    out_path = args.out or args.out_dir / f"{endpoint_config['prefix']}_{token}_{start_token}_{end_token}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out_path, converted, endpoint_config["fields"])
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source=str(endpoint_config["source"]),
            file_path=out_path,
            vendor="J-Quants API V2 Bulk",
            schema_version=str(endpoint_config["schema"]),
            date_range=f"{args.from_date}..{args.to_date}",
            notes=(
                f"{len(converted)} rows; {len(files)} bulk files; endpoint={endpoint_display_name(args.endpoint)}; "
                f"code_filter={'all' if code_set is None else len(code_set)}; "
                f"generated_at={datetime.now().isoformat(timespec='seconds')}"
            ),
        )
    print(f"Wrote {len(converted)} {endpoint_display_name(args.endpoint)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
