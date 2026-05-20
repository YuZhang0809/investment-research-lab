from __future__ import annotations

import argparse
from pathlib import Path

from research_common import parse_date, parse_float, read_csv


CONTRACTS = {
    "dividend": {
        "date_fields": ["available_date", "announcement_date", "AnnouncementDate"],
        "key_fields": ["code", "Code", "LocalCode"],
        "numeric_fields": [
            "forecast_dividend_per_share",
            "result_dividend_per_share",
            "forecast_payout_ratio",
            "result_payout_ratio",
            "forecast_eps",
            "GrossDividendRate",
            "ForecastDividendPerShareFiscalYearEnd",
            "ResultDividendPerShareFiscalYearEnd",
            "ForecastEarningsPerShare",
        ],
    },
    "balance_sheet": {
        "date_fields": ["available_date", "disclosure_date", "DisclosedDate"],
        "key_fields": ["code", "Code", "LocalCode"],
        "numeric_fields": [
            "market_cap",
            "cash_and_equivalents",
            "interest_bearing_debt",
            "total_liabilities",
            "equity",
            "total_assets",
            "CashAndEquivalents",
            "InterestBearingDebt",
            "TotalLiabilities",
            "Equity",
            "TotalAssets",
        ],
    },
    "crowding": {
        "date_fields": ["available_date", "date", "Date", "ApplicationDate", "PublishedDate"],
        "key_fields": ["code", "Code", "LocalCode", "sector", "Sector33Code"],
        "numeric_fields": [
            "long_margin_balance",
            "short_margin_balance",
            "short_interest",
            "volume",
            "trading_volume",
            "LongMarginTradeVolume",
            "ShortMarginTradeVolume",
            "LongMarginOutstanding",
            "ShortMarginOutstanding",
            "ShortInterest",
            "ShortPosition",
        ],
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate optional public-safe factor input contracts.")
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--contract", required=True, choices=sorted(CONTRACTS))
    parser.add_argument("--require-numeric", action="append", default=[], help="Require this numeric field to be present and valid.")
    return parser


def first_present(row: dict[str, str], fields: list[str]) -> str | None:
    for field in fields:
        if field in row and str(row.get(field) or "").strip():
            return str(row.get(field) or "").strip()
    return None


def validate_panel(panel: Path, contract: str, require_numeric: list[str] | None = None) -> int:
    spec = CONTRACTS[contract]
    rows = read_csv(panel)
    if not rows:
        raise ValueError(f"{contract} panel is empty: {panel}")
    columns = set(rows[0])
    if not any(field in columns for field in spec["key_fields"]):
        raise ValueError(f"{contract} panel requires one key field from: {', '.join(spec['key_fields'])}")
    if not any(field in columns for field in spec["date_fields"]):
        raise ValueError(f"{contract} panel requires one date field from: {', '.join(spec['date_fields'])}")
    for required in require_numeric or []:
        if required not in columns:
            raise ValueError(f"{contract} panel missing required numeric field: {required}")
    for row_number, row in enumerate(rows, start=2):
        if first_present(row, spec["key_fields"]) is None:
            raise ValueError(f"{contract} panel has blank key at row {row_number}.")
        row_date = first_present(row, spec["date_fields"])
        if row_date is None or parse_date(row_date, field_name=f"{contract}.date") is None:
            raise ValueError(f"{contract} panel has blank or invalid date at row {row_number}.")
        for field in spec["numeric_fields"]:
            if field in row and str(row.get(field) or "").strip() not in {"", "-"}:
                if parse_float(row.get(field)) is None:
                    raise ValueError(f"{contract} panel has invalid numeric field {field!r} at row {row_number}.")
        for field in require_numeric or []:
            if parse_float(row.get(field)) is None:
                raise ValueError(f"{contract} panel has blank or invalid required numeric field {field!r} at row {row_number}.")
    return len(rows)


def main() -> int:
    args = build_parser().parse_args()
    row_count = validate_panel(args.panel, args.contract, args.require_numeric)
    print(f"Validated {row_count} {args.contract} contract rows from {args.panel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
