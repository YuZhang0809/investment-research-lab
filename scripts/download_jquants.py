from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Any

from jquants_client import DEFAULT_API_KEY_ENV, require_api_key, request_paginated
from research_common import append_manifest, parse_float, write_csv


DEFAULT_DATE = "2026-05-15"
DEFAULT_CODES = ["86970"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download J-Quants V2 data and convert it to v0.1 contract CSVs."
    )
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--date", default=DEFAULT_DATE, help="YYYY-MM-DD date for master and daily prices.")
    parser.add_argument("--prices-from", help="Optional YYYY-MM-DD start date for daily prices.")
    parser.add_argument("--prices-to", help="Optional YYYY-MM-DD end date for daily prices. Defaults to --date.")
    parser.add_argument(
        "--price-code",
        action="append",
        dest="price_codes",
        help="Optional issue code for price history. Can be repeated. If omitted, downloads all issues for --date.",
    )
    parser.add_argument(
        "--price-codes-file",
        type=Path,
        help="Optional CSV/text file of issue codes for price history. Reads a 'code' column or first column.",
    )
    parser.add_argument(
        "--code",
        action="append",
        dest="codes",
        help="Issue code for /fins/summary. Can be repeated. Defaults to 86970.",
    )
    parser.add_argument(
        "--codes-file",
        type=Path,
        help="Optional CSV/text file of issue codes for /fins/summary. Reads a 'code' column or first column.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/raw/jquants/contracts"),
        help="Output directory for contract CSVs. This path is ignored by Git.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest/data_manifest.csv"),
        help="Manifest CSV path.",
    )
    parser.add_argument("--no-manifest", action="store_true", help="Do not register output files.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue downloading remaining codes on API errors.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Pause between per-code API calls.")
    parser.add_argument("--skip-master", action="store_true", help="Skip /equities/master download and output.")
    parser.add_argument("--skip-prices", action="store_true", help="Skip /equities/bars/daily download and output.")
    parser.add_argument("--skip-fundamentals", action="store_true", help="Skip /fins/summary download and output.")
    return parser


def read_codes_file(path: Path) -> list[str]:
    rows = path.read_text(encoding="utf-8-sig").splitlines()
    codes: list[str] = []
    for index, line in enumerate(rows):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [part.strip() for part in stripped.split(",")]
        if index == 0 and parts[0].lower() == "code":
            continue
        code = parts[0]
        if code:
            codes.append(code)
    return codes


def unique_codes(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        code = str(value).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def resolve_codes(cli_codes: list[str] | None, codes_file: Path | None, default: list[str]) -> list[str]:
    values: list[str] = []
    if codes_file:
        values.extend(read_codes_file(codes_file))
    if cli_codes:
        values.extend(cli_codes)
    if not values:
        values.extend(default)
    return unique_codes(values)


def token_for_codes(codes: list[str]) -> str:
    if not codes:
        return "all"
    if len(codes) <= 5:
        return "_".join(codes)
    return f"{len(codes)}codes_{codes[0]}_{codes[-1]}"


def manifest_range_for_codes(codes: list[str]) -> str:
    if len(codes) <= 20:
        return ",".join(codes)
    return f"{len(codes)} codes"


def fetch_many_codes(
    api_key: str,
    *,
    path: str,
    codes: list[str],
    base_params: dict[str, str],
    code_param: str = "code",
    continue_on_error: bool = False,
    sleep_seconds: float = 0.0,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, code in enumerate(codes, start=1):
        params = dict(base_params)
        params[code_param] = code
        try:
            code_rows = request_paginated(api_key, path, params)
            rows.extend(code_rows)
            print(f"[{index}/{len(codes)}] {path} code={code}: {len(code_rows)} rows")
        except Exception as exc:
            errors.append({"path": path, "code": code, "error": str(exc)})
            print(f"[{index}/{len(codes)}] {path} code={code}: ERROR {exc}")
            if not continue_on_error:
                raise
        if sleep_seconds > 0 and index < len(codes):
            sleep(sleep_seconds)
    return rows, errors


def normalize_date(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value)
    if " " in text:
        text = text.split(" ", 1)[0]
    return text[:10]


def is_etf_reit_infra(row: dict[str, Any]) -> bool:
    market = str(row.get("MktNm") or "")
    sector = str(row.get("S33Nm") or "")
    name = str(row.get("CoName") or "").lower()
    code = str(row.get("Code") or "")
    # J-Quants V2 master sample does not expose instrument type directly.
    # This conservative first pass keeps common exchange sections and marks known non-equity markets.
    if any(token in market.lower() for token in ["etf", "reit", "infra", "インフラ"]):
        return True
    if market == "その他" and sector == "その他":
        return True
    if any(token in name for token in ["etf", "reit", "投資法人", "上場投信", "etn"]):
        return True
    if code.startswith(("13", "14")) and market == "":
        return True
    return False


def convert_master(rows: list[dict[str, Any]], date_value: str) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in rows:
        non_equity = is_etf_reit_infra(row)
        converted.append(
            {
                "code": row.get("Code", ""),
                "name": row.get("CoName", ""),
                "market": row.get("MktNm") or row.get("Mkt", ""),
                "sector": row.get("S33Nm") or row.get("S33", ""),
                "listed_date": "",
                "delisted_date": "",
                "last_trading_date": "",
                "security_type": "etf" if non_equity else "common_stock",
                "is_common_stock": "false" if non_equity else "true",
                "is_etf_reit_infra": "true" if non_equity else "false",
                "tradable_flag": "",
                "lot_size": "100",
                "source_date": row.get("Date") or date_value,
                "source": "jquants_master_snapshot",
                "listing_lifecycle_status": "snapshot_only_missing_lifecycle_dates",
                "delisting_reason": "",
                "successor_code": "",
                "market_code": row.get("Mkt", ""),
                "sector33_code": row.get("S33", ""),
                "scale_category": row.get("ScaleCat", ""),
            }
        )
    return converted


def has_trade(row: dict[str, Any]) -> bool:
    close = parse_float(row.get("C"))
    volume = parse_float(row.get("Vo"), default=0.0) or 0.0
    trading_value = parse_float(row.get("Va"), default=0.0) or 0.0
    return close is not None and close > 0 and (volume > 0 or trading_value > 0)


def price_limit_flag(row: dict[str, Any]) -> bool:
    return str(row.get("UL") or "0") not in {"", "0"} or str(row.get("LL") or "0") not in {"", "0"}


def convert_prices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in rows:
        converted.append(
            {
                "date": normalize_date(row.get("Date")),
                "code": row.get("Code", ""),
                "unadjusted_open": row.get("O", ""),
                "unadjusted_high": row.get("H", ""),
                "unadjusted_low": row.get("L", ""),
                "unadjusted_close": row.get("C", ""),
                "adjusted_close": row.get("AdjC", ""),
                "volume": row.get("Vo", ""),
                "trading_value": row.get("Va", ""),
                "adjustment_factor": row.get("AdjFactor", ""),
                "tradable_flag": "true" if has_trade(row) else "false",
                "price_limit_flag": "true" if price_limit_flag(row) else "false",
                "upper_limit_flag": row.get("UL", ""),
                "lower_limit_flag": row.get("LL", ""),
            }
        )
    return converted


def convert_fundamentals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in rows:
        converted.append(
            {
                "code": row.get("Code", ""),
                "available_date": normalize_date(row.get("DiscDate")),
                "available_time": row.get("DiscTime", ""),
                "document_type": row.get("DocType", ""),
                "period_type": row.get("CurPerType", ""),
                "period_start": normalize_date(row.get("CurPerSt")),
                "period_end": normalize_date(row.get("CurPerEn")),
                "fiscal_year_start": normalize_date(row.get("CurFYSt")),
                "fiscal_year_end": normalize_date(row.get("CurFYEn")),
                "sales": row.get("Sales", ""),
                "operating_profit": row.get("OP", ""),
                "ordinary_profit": row.get("OdP", ""),
                "net_profit": row.get("NP", ""),
                "equity": row.get("Eq", ""),
                "total_assets": row.get("TA", ""),
                "eps": row.get("EPS", ""),
                "bps": row.get("BPS", ""),
                "shares_outstanding": row.get("ShOutFY", ""),
                "avg_shares": row.get("AvgSh", ""),
                "disclosure_number": row.get("DiscNo", ""),
            }
        )
    return converted


def register_output(
    manifest_path: Path,
    *,
    source: str,
    file_path: Path,
    schema_version: str,
    date_range: str,
    rows: int,
    no_manifest: bool,
) -> None:
    if no_manifest:
        return
    append_manifest(
        manifest_path,
        source=source,
        file_path=file_path,
        vendor="J-Quants API V2",
        schema_version=schema_version,
        date_range=date_range,
        notes=f"{rows} rows; generated_at={datetime.now().isoformat(timespec='seconds')}",
    )


def main() -> int:
    args = build_parser().parse_args()
    api_key = require_api_key(args.api_key_env)
    codes = resolve_codes(args.codes, args.codes_file, DEFAULT_CODES)
    date_token = args.date.replace("-", "")

    master_rows = [] if args.skip_master else request_paginated(api_key, "/equities/master", {"date": args.date})
    price_errors: list[dict[str, str]] = []
    if args.skip_prices:
        price_rows = []
        price_range = ""
        price_file_token = date_token
    elif args.prices_from:
        price_rows = []
        if args.price_codes or args.price_codes_file:
            price_codes = resolve_codes(args.price_codes, args.price_codes_file, [])
        else:
            price_codes = [""]
        price_to = args.prices_to or args.date
        if price_codes == [""]:
            price_rows = request_paginated(
                api_key,
                "/equities/bars/daily",
                {"from": args.prices_from, "to": price_to},
            )
            price_errors = []
        else:
            price_rows, price_errors = fetch_many_codes(
                api_key,
                path="/equities/bars/daily",
                codes=price_codes,
                base_params={"from": args.prices_from, "to": price_to},
                continue_on_error=args.continue_on_error,
                sleep_seconds=args.sleep_seconds,
            )
        price_range = f"{args.prices_from}..{price_to}"
        price_file_token = f"{args.prices_from.replace('-', '')}_{price_to.replace('-', '')}"
        if price_codes != [""]:
            price_file_token = f"{token_for_codes(price_codes)}_{price_file_token}"
    else:
        price_rows = request_paginated(api_key, "/equities/bars/daily", {"date": args.date})
        price_errors = []
        price_range = args.date
        price_file_token = date_token
    if args.skip_fundamentals:
        fundamentals_rows = []
        fundamentals_errors: list[dict[str, str]] = []
    else:
        fundamentals_rows, fundamentals_errors = fetch_many_codes(
            api_key,
            path="/fins/summary",
            codes=codes,
            base_params={},
            continue_on_error=args.continue_on_error,
            sleep_seconds=args.sleep_seconds,
        )

    listings = convert_master(master_rows, args.date)
    prices = convert_prices(price_rows)
    fundamentals = convert_fundamentals(fundamentals_rows)

    listings_path = args.out_dir / f"listings_{date_token}.csv"
    prices_path = args.out_dir / f"prices_{price_file_token}.csv"
    fundamentals_path = args.out_dir / f"fundamentals_{token_for_codes(codes)}.csv"
    errors_path = args.out_dir / f"errors_{date_token}.csv"

    if not args.skip_master:
        write_csv(
            listings_path,
            listings,
            [
                "code",
                "name",
                "market",
                "sector",
                "listed_date",
                "delisted_date",
                "last_trading_date",
                "security_type",
                "is_common_stock",
                "is_etf_reit_infra",
                "tradable_flag",
                "lot_size",
                "source_date",
                "source",
                "listing_lifecycle_status",
                "delisting_reason",
                "successor_code",
                "market_code",
                "sector33_code",
                "scale_category",
            ],
        )
    if not args.skip_prices:
        write_csv(
            prices_path,
            prices,
            [
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
            ],
        )
    if not args.skip_fundamentals:
        write_csv(
            fundamentals_path,
            fundamentals,
            [
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
            ],
        )
    all_errors = price_errors + fundamentals_errors
    if all_errors:
        write_csv(errors_path, all_errors, ["path", "code", "error"])

    if not args.skip_master:
        register_output(
            args.manifest,
            source="jquants_contract_listings",
            file_path=listings_path,
            schema_version="jquants_listings_contract_v0_1",
            date_range=args.date,
            rows=len(listings),
            no_manifest=args.no_manifest,
        )
    if not args.skip_prices:
        register_output(
            args.manifest,
            source="jquants_contract_prices",
            file_path=prices_path,
            schema_version="jquants_prices_contract_v0_1",
            date_range=price_range,
            rows=len(prices),
            no_manifest=args.no_manifest,
        )
    if not args.skip_fundamentals:
        register_output(
            args.manifest,
            source="jquants_contract_fundamentals",
            file_path=fundamentals_path,
            schema_version="jquants_fundamentals_contract_v0_1",
            date_range=manifest_range_for_codes(codes),
            rows=len(fundamentals),
            no_manifest=args.no_manifest,
        )

    if not args.skip_master:
        print(f"Wrote {len(listings)} listings rows to {listings_path}")
    if not args.skip_prices:
        print(f"Wrote {len(prices)} price rows to {prices_path}")
    if not args.skip_fundamentals:
        print(f"Wrote {len(fundamentals)} fundamentals rows to {fundamentals_path}")
    if all_errors:
        print(f"Wrote {len(all_errors)} API errors to {errors_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
