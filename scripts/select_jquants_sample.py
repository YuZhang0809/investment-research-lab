from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_common import parse_bool, parse_float, read_csv, write_csv


DEFAULT_MARKETS = {"プライム", "スタンダード", "グロース"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select a reproducible J-Quants test sample.")
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--min-price", type=float, default=100)
    parser.add_argument("--max-price", type=float, default=2000)
    parser.add_argument("--min-trading-value", type=float, default=10_000_000)
    parser.add_argument("--max-trading-value", type=float, default=500_000_000)
    parser.add_argument("--include-metadata", action="store_true", help="Include name, market, sector, and liquidity fields.")
    return parser


def is_listed_common_stock(row: dict[str, str]) -> bool:
    if not parse_bool(row.get("is_common_stock"), default=False):
        return False
    if parse_bool(row.get("is_etf_reit_infra"), default=False):
        return False
    if row.get("market") not in DEFAULT_MARKETS:
        return False
    if row.get("sector") == "その他":
        return False
    name = row.get("name", "").lower()
    blocked_tokens = ["etf", "reit", "投資法人", "上場投信", "etn", "ｉｆｒｅｅｅｔｆ", "ｎｅｘｔ　ｆｕｎｄｓ"]
    return not any(token in name for token in blocked_tokens)


def main() -> int:
    args = build_parser().parse_args()
    listings = {row["code"]: row for row in read_csv(args.listings) if row.get("code")}
    candidates: list[dict[str, Any]] = []

    for price in read_csv(args.prices):
        code = price.get("code", "")
        listing = listings.get(code)
        if not listing or not is_listed_common_stock(listing):
            continue
        close = parse_float(price.get("unadjusted_close"))
        trading_value = parse_float(price.get("trading_value"))
        if close is None or trading_value is None:
            continue
        if close < args.min_price or close > args.max_price:
            continue
        if trading_value < args.min_trading_value or trading_value > args.max_trading_value:
            continue
        candidates.append(
            {
                "code": code,
                "name": listing.get("name", ""),
                "market": listing.get("market", ""),
                "sector": listing.get("sector", ""),
                "close": close,
                "trading_value": trading_value,
            }
        )

    candidates.sort(key=lambda row: (float(row["trading_value"]), row["code"]))
    selected = candidates[: args.limit]
    if args.include_metadata:
        fieldnames = ["code", "name", "market", "sector", "close", "trading_value"]
        rows = selected
    else:
        fieldnames = ["code"]
        rows = [{"code": row["code"]} for row in selected]
    write_csv(args.out, rows, fieldnames)
    print(f"Selected {len(selected)} codes from {len(candidates)} candidates into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
