from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research_common import load_yaml, month_key, parse_date, parse_float, read_csv, write_csv


LISTING_REQUIRED = [
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
]
PRICE_REQUIRED = [
    "date",
    "code",
    "unadjusted_close",
    "adjusted_close",
    "trading_value",
    "tradable_flag",
    "price_limit_flag",
]
FUNDAMENTAL_REQUIRED = [
    "code",
    "available_date",
    "available_time",
    "document_type",
    "operating_profit",
    "net_profit",
    "equity",
    "total_assets",
    "shares_outstanding",
]

DATE_COLUMNS = {
    "listings": ["listed_date", "delisted_date"],
    "prices": ["date"],
    "fundamentals": ["available_date"],
}
NUMBER_COLUMNS = {
    "listings": ["lot_size"],
    "prices": ["unadjusted_close", "adjusted_close", "trading_value"],
    "fundamentals": [
        "operating_profit",
        "net_profit",
        "equity",
        "total_assets",
        "shares_outstanding",
    ],
}
ISSUE_FIELDS = ["severity", "dataset", "check", "code", "column", "value", "message"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate minimal v0.1 research CSV contracts.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--rebalance-date", help="Optional YYYY-MM-DD date for point-in-time warnings.")
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/validation"))
    parser.add_argument("--label", help="Optional output label. Defaults to rebalance month or 'contract'.")
    parser.add_argument("--fail-on-warning", action="store_true")
    return parser


def issue(
    issues: list[dict[str, str]],
    *,
    severity: str,
    dataset: str,
    check: str,
    message: str,
    code: str = "",
    column: str = "",
    value: Any = "",
) -> None:
    issues.append(
        {
            "severity": severity,
            "dataset": dataset,
            "check": check,
            "code": code,
            "column": column,
            "value": "" if value is None else str(value),
            "message": message,
        }
    )


def columns(rows: list[dict[str, str]]) -> set[str]:
    if not rows:
        return set()
    return set(rows[0].keys())


def check_required_columns(
    issues: list[dict[str, str]],
    *,
    dataset: str,
    rows: list[dict[str, str]],
    required: list[str],
) -> None:
    if not rows:
        issue(
            issues,
            severity="error",
            dataset=dataset,
            check="non_empty",
            message=f"{dataset} file has no rows",
        )
        return
    present = columns(rows)
    for column in required:
        if column not in present:
            issue(
                issues,
                severity="error",
                dataset=dataset,
                check="required_columns",
                column=column,
                message=f"Missing required column: {column}",
            )


def check_dates(issues: list[dict[str, str]], *, dataset: str, rows: list[dict[str, str]]) -> None:
    for row_index, row in enumerate(rows, start=2):
        code = row.get("code", "")
        for column in DATE_COLUMNS[dataset]:
            if column not in row or not row.get(column):
                continue
            try:
                parse_date(row.get(column), field_name=f"{dataset}.{column}")
            except ValueError as exc:
                issue(
                    issues,
                    severity="error",
                    dataset=dataset,
                    check="date_parse",
                    code=code,
                    column=column,
                    value=row.get(column, ""),
                    message=f"Row {row_index}: {exc}",
                )


def check_numbers(issues: list[dict[str, str]], *, dataset: str, rows: list[dict[str, str]]) -> None:
    for row_index, row in enumerate(rows, start=2):
        code = row.get("code", "")
        for column in NUMBER_COLUMNS[dataset]:
            if column not in row or row.get(column) in (None, ""):
                continue
            if parse_float(row.get(column)) is None:
                issue(
                    issues,
                    severity="error",
                    dataset=dataset,
                    check="number_parse",
                    code=code,
                    column=column,
                    value=row.get(column, ""),
                    message=f"Row {row_index}: expected numeric value",
                )


def check_duplicate_keys(
    issues: list[dict[str, str]],
    *,
    dataset: str,
    rows: list[dict[str, str]],
    key_columns: list[str],
) -> None:
    counts: Counter[tuple[str, ...]] = Counter(
        tuple((row.get(column) or "").strip() for column in key_columns) for row in rows
    )
    for key, count in counts.items():
        if count <= 1 or any(not part for part in key):
            continue
        issue(
            issues,
            severity="error",
            dataset=dataset,
            check="duplicate_key",
            code=key[-1] if "code" in key_columns else "",
            column=",".join(key_columns),
            value="|".join(key),
            message=f"Duplicate key appears {count} times",
        )


def max_required_price_rows(config: dict[str, Any]) -> int:
    universe = config.get("universe", {})
    min_ipo_age = int(universe.get("min_ipo_age_trading_days") or 0)
    liquidity_lookback = int(universe.get("liquidity_lookback_days") or 0)
    momentum_12_1_rows = 252 + 1
    return max(min_ipo_age, liquidity_lookback, momentum_12_1_rows)


def check_code_coverage(
    issues: list[dict[str, str]],
    *,
    listing_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
) -> None:
    listing_codes = {row.get("code", "").strip() for row in listing_rows if row.get("code")}
    price_codes = {row.get("code", "").strip() for row in price_rows if row.get("code")}
    fundamental_codes = {row.get("code", "").strip() for row in fundamental_rows if row.get("code")}

    for code in sorted(price_codes - listing_codes):
        issue(
            issues,
            severity="error",
            dataset="prices",
            check="code_coverage",
            code=code,
            message="Price code is missing from listings",
        )
    for code in sorted(fundamental_codes - listing_codes):
        issue(
            issues,
            severity="warning",
            dataset="fundamentals",
            check="code_coverage",
            code=code,
            message="Fundamental code is missing from listings",
        )
    for code in sorted(price_codes - fundamental_codes):
        issue(
            issues,
            severity="warning",
            dataset="prices",
            check="code_coverage",
            code=code,
            message="Price code has no fundamentals rows",
        )
    for code in sorted(fundamental_codes - price_codes):
        issue(
            issues,
            severity="warning",
            dataset="fundamentals",
            check="code_coverage",
            code=code,
            message="Fundamental code has no price rows",
        )


def check_price_coverage(
    issues: list[dict[str, str]],
    *,
    price_rows: list[dict[str, str]],
    required_rows: int,
) -> None:
    by_code: dict[str, set[str]] = defaultdict(set)
    for row in price_rows:
        code = (row.get("code") or "").strip()
        row_date = (row.get("date") or "").strip()
        if code and row_date:
            by_code[code].add(row_date)
    for code, dates in sorted(by_code.items()):
        if len(dates) < required_rows:
            issue(
                issues,
                severity="warning",
                dataset="prices",
                check="history_coverage",
                code=code,
                value=len(dates),
                message=f"Only {len(dates)} price rows; configured lookbacks need about {required_rows}",
            )


def check_rebalance_warnings(
    issues: list[dict[str, str]],
    *,
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
    rebalance_date: Any,
) -> None:
    if rebalance_date is None:
        return
    future_prices = 0
    future_fundamentals = 0
    for row in price_rows:
        try:
            row_date = parse_date(row.get("date"), field_name="prices.date")
        except ValueError:
            continue
        if row_date and row_date > rebalance_date:
            future_prices += 1
    for row in fundamental_rows:
        try:
            available_date = parse_date(row.get("available_date"), field_name="fundamentals.available_date")
        except ValueError:
            continue
        if available_date and available_date > rebalance_date:
            future_fundamentals += 1
    if future_prices:
        issue(
            issues,
            severity="warning",
            dataset="prices",
            check="rebalance_window",
            value=future_prices,
            message="Rows exist after rebalance date; downstream scripts must gate by date",
        )
    if future_fundamentals:
        issue(
            issues,
            severity="warning",
            dataset="fundamentals",
            check="rebalance_window",
            value=future_fundamentals,
            message="Disclosures exist after rebalance date; downstream scripts must use available_date gate",
        )


def validate_contracts(
    *,
    config: dict[str, Any],
    listing_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
    rebalance_date: Any = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    issues: list[dict[str, str]] = []
    for dataset, rows, required in [
        ("listings", listing_rows, LISTING_REQUIRED),
        ("prices", price_rows, PRICE_REQUIRED),
        ("fundamentals", fundamental_rows, FUNDAMENTAL_REQUIRED),
    ]:
        check_required_columns(issues, dataset=dataset, rows=rows, required=required)
        check_dates(issues, dataset=dataset, rows=rows)
        check_numbers(issues, dataset=dataset, rows=rows)

    check_duplicate_keys(issues, dataset="listings", rows=listing_rows, key_columns=["code"])
    check_duplicate_keys(issues, dataset="prices", rows=price_rows, key_columns=["date", "code"])
    check_duplicate_keys(
        issues,
        dataset="fundamentals",
        rows=fundamental_rows,
        key_columns=["code", "available_date", "available_time", "document_type"],
    )
    check_code_coverage(
        issues,
        listing_rows=listing_rows,
        price_rows=price_rows,
        fundamental_rows=fundamental_rows,
    )
    check_price_coverage(
        issues,
        price_rows=price_rows,
        required_rows=max_required_price_rows(config),
    )
    check_rebalance_warnings(
        issues,
        price_rows=price_rows,
        fundamental_rows=fundamental_rows,
        rebalance_date=rebalance_date,
    )

    severity_counts = Counter(issue_row["severity"] for issue_row in issues)
    summary = {
        "rows": {
            "listings": len(listing_rows),
            "prices": len(price_rows),
            "fundamentals": len(fundamental_rows),
        },
        "unique_codes": {
            "listings": len({row.get("code", "") for row in listing_rows if row.get("code")}),
            "prices": len({row.get("code", "") for row in price_rows if row.get("code")}),
            "fundamentals": len({row.get("code", "") for row in fundamental_rows if row.get("code")}),
        },
        "issues": dict(severity_counts),
    }
    return issues, summary


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date") if args.rebalance_date else None
    issues, summary = validate_contracts(
        config=config,
        listing_rows=read_csv(args.listings),
        price_rows=read_csv(args.prices),
        fundamental_rows=read_csv(args.fundamentals),
        rebalance_date=rebalance_date,
    )
    label = args.label or (month_key(rebalance_date) if rebalance_date else "contract")
    issues_path = args.out_dir / f"contract_validation_issues_{label}.csv"
    summary_path = args.out_dir / f"contract_validation_summary_{label}.json"
    write_csv(issues_path, issues, ISSUE_FIELDS)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    errors = sum(1 for row in issues if row["severity"] == "error")
    warnings = sum(1 for row in issues if row["severity"] == "warning")
    print(f"Wrote {len(issues)} validation issues to {issues_path}")
    print(f"Wrote validation summary to {summary_path}")
    print(f"Validation result: {errors} errors; {warnings} warnings")
    if errors or (warnings and args.fail_on_warning):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
