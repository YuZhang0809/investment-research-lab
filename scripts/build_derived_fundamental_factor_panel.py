from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from research_common import append_manifest, parse_date, parse_float, read_csv, write_table


DERIVED_FIELDS = [
    "sales_yoy",
    "operating_profit_yoy",
    "net_profit_yoy",
    "operating_margin",
    "operating_margin_delta_yoy",
    "roe",
    "roa",
    "equity_to_assets",
    "shares_outstanding_change_yoy",
    "profit_turn_positive",
    "cash_to_market_cap",
    "net_cash_to_market_cap",
    "debt_to_equity",
    "liabilities_to_assets",
]
RAW_OUTPUT_FIELDS = [
    "sales",
    "operating_profit",
    "net_profit",
    "equity",
    "total_assets",
    "shares_outstanding",
    "market_cap",
    "cash_and_equivalents",
    "interest_bearing_debt",
    "total_liabilities",
    "forecast_eps",
    "forecast_dividend_per_share",
    "result_dividend_per_share",
    "forecast_payout_ratio",
    "result_payout_ratio",
]
CORE_STATEMENT_INPUT_FIELDS = [
    "sales",
    "operating_profit",
    "net_profit",
    "equity",
    "total_assets",
    "shares_outstanding",
]
METADATA_FIELDS = [
    "available_date",
    "available_time",
    "period_type",
    "period_end",
    "document_type",
    "disclosure_number",
    "statement_scope",
    "prior_year_available_date",
    "prior_year_period_end",
    "source_duplicate_count",
    "source_disclosure_count",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build public-safe PIT derived fundamental factor panels from disclosure history."
    )
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--panel-mode", choices=["rebalance", "event"], default="rebalance")
    parser.add_argument(
        "--rebalance-dates",
        type=Path,
        help="CSV/Parquet with rebalance_date or date column. Required for --panel-mode rebalance unless --rebalance-date is repeated.",
    )
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values", help="YYYY-MM-DD; can be repeated.")
    parser.add_argument(
        "--period-type",
        action="append",
        dest="period_type_values",
        help="Optional normalized period type to keep, for example fy or 1q. Can be repeated.",
    )
    parser.add_argument(
        "--allow-mixed-period-types",
        action="store_true",
        help="Allow rebalance output rows for the same date to contain multiple period_type values.",
    )
    parser.add_argument(
        "--document-type-contains",
        action="append",
        dest="document_type_contains",
        help="Keep rows whose document_type contains this text, case-insensitive. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-document-type-contains",
        action="append",
        dest="exclude_document_type_contains",
        help="Drop rows whose document_type contains this text, case-insensitive. Can be repeated.",
    )
    parser.add_argument("--require-useful", action="store_true", help="Drop rows that contain none of the supported numeric inputs.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--run-label", default="derived_fundamentals")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def parse_optional_date(value: Any, field_name: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    return parse_date(text, field_name=field_name)


def available_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(
        row.get("available_date") or row.get("disclosure_date") or row.get("DisclosedDate"),
        "fundamentals.available_date",
    )


def available_date_text(row: dict[str, Any]) -> str:
    value = available_date(row)
    return value.isoformat() if value else ""


def period_end(row: dict[str, Any]) -> date | None:
    return parse_optional_date(row.get("period_end") or row.get("CurrentPeriodEndDate"), "fundamentals.period_end")


def period_end_text(row: dict[str, Any]) -> str:
    value = period_end(row)
    return value.isoformat() if value else ""


def period_type(row: dict[str, Any]) -> str:
    return normalize_token(
        row.get("period_type")
        or row.get("type_of_current_period")
        or row.get("current_period_type")
        or row.get("TypeOfCurrentPeriod")
        or row.get("period")
        or ""
    )


def statement_scope(row: dict[str, Any]) -> str:
    text = normalize_token(
        row.get("statement_scope")
        or row.get("consolidated_flag")
        or row.get("consolidated")
        or row.get("financial_statement_type")
        or ""
    )
    if text in {"true", "1", "yes", "y"}:
        return "consolidated"
    if text in {"false", "0", "no", "n"}:
        return "non_consolidated"
    return text


def normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def disclosure_time(row: dict[str, Any]) -> str:
    return str(row.get("available_time") or row.get("disclosure_time") or row.get("DisclosedTime") or "").strip()


def disclosure_number_sort_value(value: Any) -> tuple[int, int, str]:
    text = str(value or "").strip()
    if text.isdigit():
        return (1, int(text), "")
    return (0, 0, text)


def availability_sort_key(row: dict[str, Any]) -> tuple[str, str, tuple[int, int, str], int]:
    return (
        available_date_text(row),
        disclosure_time(row),
        disclosure_number_sort_value(disclosure_number(row)),
        int(row.get("_source_index", 0) or 0),
    )


def as_of_availability_key(value: date) -> tuple[str, str, tuple[int, int, str], int]:
    return (value.isoformat(), "99:99:99", (1, 10**30, "~"), 10**30)


def disclosure_sort_key(row: dict[str, Any]) -> tuple[str, str, str, tuple[int, int, str], int, int]:
    return (
        available_date_text(row),
        disclosure_time(row),
        period_end_text(row),
        disclosure_number_sort_value(disclosure_number(row)),
        useful_value_count(row),
        int(row.get("_source_index", 0) or 0),
    )


def first_number(row: dict[str, Any], *fields: str) -> float | None:
    for field in fields:
        value = parse_float(row.get(field))
        if value is not None:
            return value
    return None


def normalized_numbers(row: dict[str, Any]) -> dict[str, float | None]:
    return {
        "sales": first_number(row, "sales", "net_sales", "revenue", "NetSales"),
        "operating_profit": first_number(row, "operating_profit", "operating_income", "OperatingProfit"),
        "net_profit": first_number(row, "net_profit", "profit", "profit_attributable_to_owners_of_parent", "Profit"),
        "equity": first_number(row, "equity", "net_assets", "Equity"),
        "total_assets": first_number(row, "total_assets", "assets", "TotalAssets"),
        "shares_outstanding": first_number(row, "shares_outstanding", "shares", "avg_shares"),
        "market_cap": first_number(row, "market_cap", "MarketCapitalization"),
        "cash_and_equivalents": first_number(row, "cash_and_equivalents", "CashAndEquivalents"),
        "interest_bearing_debt": first_number(
            row,
            "interest_bearing_debt",
            "InterestBearingDebt",
            "bonds_and_borrowings",
            "debt",
        ),
        "total_liabilities": first_number(row, "total_liabilities", "TotalLiabilities", "liabilities"),
        "forecast_eps": first_number(
            row,
            "forecast_eps",
            "ForecastEarningsPerShare",
            "ForecastNonConsolidatedEarningsPerShare",
        ),
        "forecast_dividend_per_share": first_number(
            row,
            "forecast_dividend_per_share",
            "ForecastDividendPerShareAnnual",
            "ForecastDividendPerShareFiscalYearEnd",
        ),
        "result_dividend_per_share": first_number(
            row,
            "result_dividend_per_share",
            "ResultDividendPerShareAnnual",
            "ResultDividendPerShareFiscalYearEnd",
        ),
        "forecast_payout_ratio": first_number(
            row,
            "forecast_payout_ratio",
            "ForecastPayoutRatio",
            "ForecastNonConsolidatedPayoutRatio",
        ),
        "result_payout_ratio": first_number(row, "result_payout_ratio", "ResultPayoutRatio"),
    }


def useful_value_count(row: dict[str, Any]) -> int:
    return sum(value is not None for value in normalized_numbers(row).values())


def core_statement_value_count(row: dict[str, Any]) -> int:
    values = normalized_numbers(row)
    return sum(values.get(field) is not None for field in CORE_STATEMENT_INPUT_FIELDS)


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def yoy_change(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior)


def prior_year_date(value: date | None) -> date | None:
    if value is None:
        return None
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def dedupe_disclosures(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows, start=1):
        copied: dict[str, Any] = dict(row)
        copied["_source_index"] = index
        code = disclosure_code(copied)
        if not code:
            continue
        key = (
            code,
            period_type(copied),
            period_end_text(copied),
            available_date_text(copied),
            disclosure_time(copied),
            statement_scope(copied),
        )
        grouped[key].append(copied)
    output: list[dict[str, Any]] = []
    for values in grouped.values():
        selected = sorted(values, key=disclosure_sort_key)[-1]
        selected["_source_duplicate_count"] = len(values)
        output.append(selected)
    output.sort(key=lambda row: (disclosure_code(row), disclosure_sort_key(row)))
    return output


def disclosure_code(row: dict[str, Any]) -> str:
    return str(row.get("code") or row.get("LocalCode") or row.get("Code") or "").strip()


def disclosure_number(row: dict[str, Any]) -> str:
    return str(row.get("disclosure_number") or row.get("DisclosureNumber") or "").strip()


def prior_lookup_key(row: dict[str, Any], target_period_end: date | None = None) -> tuple[str, str, str, str]:
    return (
        disclosure_code(row),
        period_type(row),
        target_period_end.isoformat() if target_period_end else period_end_text(row),
        statement_scope(row),
    )


def build_prior_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        index[prior_lookup_key(row)].append(row)
    for values in index.values():
        values.sort(key=disclosure_sort_key)
    return index


def latest_prior_year(
    row: dict[str, Any],
    index: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    *,
    as_of_key: tuple[str, str, tuple[int, int, str], int] | None = None,
) -> dict[str, Any] | None:
    target_period_end = prior_year_date(period_end(row))
    if target_period_end is None:
        return None
    candidates = index.get(prior_lookup_key(row, target_period_end), [])
    cutoff = as_of_key or availability_sort_key(row)
    valid = [candidate for candidate in candidates if availability_sort_key(candidate) <= cutoff]
    return valid[-1] if valid else None


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def derived_values(row: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    current = normalized_numbers(row)
    prior_values = normalized_numbers(prior or {})
    operating_margin = safe_ratio(current["operating_profit"], current["sales"])
    prior_operating_margin = safe_ratio(prior_values["operating_profit"], prior_values["sales"])
    net_profit = current["net_profit"]
    prior_net_profit = prior_values["net_profit"]
    values = {
        **current,
        "sales_yoy": yoy_change(current["sales"], prior_values["sales"]),
        "operating_profit_yoy": yoy_change(current["operating_profit"], prior_values["operating_profit"]),
        "net_profit_yoy": yoy_change(net_profit, prior_net_profit),
        "operating_margin": operating_margin,
        "operating_margin_delta_yoy": (
            operating_margin - prior_operating_margin
            if operating_margin is not None and prior_operating_margin is not None
            else None
        ),
        "roe": safe_ratio(net_profit, current["equity"]),
        "roa": safe_ratio(net_profit, current["total_assets"]),
        "equity_to_assets": safe_ratio(current["equity"], current["total_assets"]),
        "shares_outstanding_change_yoy": yoy_change(
            current["shares_outstanding"],
            prior_values["shares_outstanding"],
        ),
        "profit_turn_positive": None
        if prior_net_profit is None or net_profit is None
        else (1.0 if prior_net_profit < 0 < net_profit else 0.0),
        "cash_to_market_cap": safe_ratio(current["cash_and_equivalents"], current["market_cap"]),
        "net_cash_to_market_cap": safe_ratio(
            current["cash_and_equivalents"] - current["interest_bearing_debt"]
            if current["cash_and_equivalents"] is not None and current["interest_bearing_debt"] is not None
            else None,
            current["market_cap"],
        ),
        "debt_to_equity": safe_ratio(current["interest_bearing_debt"], current["equity"]),
        "liabilities_to_assets": safe_ratio(current["total_liabilities"], current["total_assets"]),
    }
    missing = [field for field in DERIVED_FIELDS if values.get(field) is None]
    if prior is None:
        missing.append("missing_prior_year")
    values["missing_flags"] = ";".join(dict.fromkeys(missing))
    return values


def enrich_disclosure(row: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    values = derived_values(row, prior)
    return {
        "code": disclosure_code(row),
        "available_date": available_date_text(row),
        "available_time": disclosure_time(row),
        "period_type": period_type(row),
        "period_end": period_end_text(row),
        "document_type": document_type(row),
        "disclosure_number": disclosure_number(row),
        "statement_scope": statement_scope(row),
        "prior_year_available_date": available_date_text(prior or {}),
        "prior_year_period_end": period_end_text(prior or {}),
        "source_duplicate_count": row.get("_source_duplicate_count", 1),
        "source_disclosure_count": 1,
        **{field: fmt(values.get(field)) for field in [*RAW_OUTPUT_FIELDS, *DERIVED_FIELDS]},
        "missing_flags": values["missing_flags"],
    }


def enrich_disclosures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prior_index = build_prior_index(rows)
    return [enrich_disclosure(row, latest_prior_year(row, prior_index)) for row in rows]


def event_panel_rows(enriched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in enriched:
        if not row["code"] or not row["available_date"]:
            continue
        grouped[(row["code"], row["available_date"])].append(row)
    output: list[dict[str, Any]] = []
    for values in grouped.values():
        selected = sorted(
            values,
            key=lambda row: (
                str(row.get("available_date") or ""),
                str(row.get("available_time") or ""),
                str(row.get("period_end") or ""),
                disclosure_number_sort_value(row.get("disclosure_number")),
            ),
        )[-1]
        copied = dict(selected)
        copied["source_disclosure_count"] = len(values)
        output.append(copied)
    output.sort(key=lambda row: (row["available_date"], row["code"]))
    return output


def load_rebalance_dates(path: Path | None, values: list[str] | None) -> list[date]:
    dates: list[date] = []
    for value in values or []:
        parsed = parse_optional_date(value, "rebalance_date")
        if parsed is not None:
            dates.append(parsed)
    if path is not None:
        for row in read_csv(path):
            parsed = parse_optional_date(row.get("rebalance_date") or row.get("date"), "rebalance_dates.rebalance_date")
            if parsed is not None:
                dates.append(parsed)
    clean = sorted(set(dates))
    if not clean:
        raise ValueError("--panel-mode rebalance requires --rebalance-date or --rebalance-dates.")
    return clean


def selected_reporting_period_key(row: dict[str, Any]) -> tuple[date, str]:
    return (period_end(row) or date.min, period_type(row))


def document_type(row: dict[str, Any]) -> str:
    return str(row.get("document_type") or row.get("TypeOfDocument") or "").strip()


def contains_any(value: str, needles: set[str] | None) -> bool:
    if not needles:
        return False
    lowered = value.lower()
    return any(needle in lowered for needle in needles)


def filter_disclosures(
    disclosures: list[dict[str, Any]],
    *,
    period_types: set[str] | None,
    document_type_contains: set[str] | None,
    exclude_document_type_contains: set[str] | None,
    require_useful: bool,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in disclosures:
        if period_types and period_type(row) not in period_types:
            continue
        row_document_type = document_type(row)
        if document_type_contains and not contains_any(row_document_type, document_type_contains):
            continue
        if contains_any(row_document_type, exclude_document_type_contains):
            continue
        if require_useful and useful_value_count(row) == 0:
            continue
        output.append(row)
    return output


def validate_rebalance_period_types(rows: list[dict[str, Any]], *, allow_mixed_period_types: bool) -> None:
    if allow_mixed_period_types:
        return
    by_date: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        by_date[str(row.get("rebalance_date") or "")].add(str(row.get("period_type") or ""))
    for rebalance_date, values in by_date.items():
        if len(values) > 1:
            display = ", ".join(sorted(value or "<blank>" for value in values))
            raise ValueError(
                f"Rebalance date {rebalance_date} has mixed period_type values: {display}. "
                "Pass --period-type to build a comparable panel, or --allow-mixed-period-types for event-style review."
            )


def rebalance_panel_rows(
    disclosures: list[dict[str, Any]],
    rebalance_dates: list[date],
    prior_index: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    *,
    allow_mixed_period_types: bool,
) -> list[dict[str, Any]]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in disclosures:
        by_code[disclosure_code(row)].append(row)
    for values in by_code.values():
        values.sort(key=disclosure_sort_key)
    output: list[dict[str, Any]] = []
    for rebalance_date in rebalance_dates:
        for code in sorted(by_code):
            candidates = [
                row
                for row in by_code[code]
                if available_date(row) is not None
                and available_date(row) <= rebalance_date
            ]
            if not candidates:
                continue
            latest_period = max(selected_reporting_period_key(row) for row in candidates)
            period_candidates = [row for row in candidates if selected_reporting_period_key(row) == latest_period]
            core_period_candidates = [row for row in period_candidates if core_statement_value_count(row) > 0]
            if core_period_candidates:
                period_candidates = core_period_candidates
            else:
                useful_period_candidates = [row for row in period_candidates if useful_value_count(row) > 0]
                if useful_period_candidates:
                    period_candidates = useful_period_candidates
            selected = sorted(period_candidates, key=disclosure_sort_key)[-1]
            prior = latest_prior_year(selected, prior_index, as_of_key=as_of_availability_key(rebalance_date))
            copied = enrich_disclosure(selected, prior)
            copied["rebalance_date"] = rebalance_date.isoformat()
            output.append(copied)
    output.sort(key=lambda row: (row["rebalance_date"], row["code"]))
    validate_rebalance_period_types(output, allow_mixed_period_types=allow_mixed_period_types)
    return output


def fieldnames(panel_mode: str) -> list[str]:
    prefix = ["rebalance_date", "code"] if panel_mode == "rebalance" else ["available_date", "code"]
    metadata = [field for field in METADATA_FIELDS if field not in prefix]
    return [*prefix, *metadata, *RAW_OUTPUT_FIELDS, *DERIVED_FIELDS, "missing_flags"]


def output_date_range(rows: list[dict[str, Any]], panel_mode: str) -> str:
    column = "rebalance_date" if panel_mode == "rebalance" else "available_date"
    values = sorted(str(row.get(column) or "") for row in rows if row.get(column))
    if not values:
        return ""
    return f"{values[0]}..{values[-1]}"


def build_panel(
    fundamentals_rows: list[dict[str, str]],
    *,
    panel_mode: str,
    rebalance_dates: list[date] | None = None,
    period_types: set[str] | None = None,
    allow_mixed_period_types: bool = False,
    document_type_contains: set[str] | None = None,
    exclude_document_type_contains: set[str] | None = None,
    require_useful: bool = False,
) -> list[dict[str, Any]]:
    disclosures = dedupe_disclosures(fundamentals_rows)
    disclosures = filter_disclosures(
        disclosures,
        period_types=period_types,
        document_type_contains=document_type_contains,
        exclude_document_type_contains=exclude_document_type_contains,
        require_useful=require_useful,
    )
    if panel_mode == "event":
        return event_panel_rows(enrich_disclosures(disclosures))
    return rebalance_panel_rows(
        disclosures,
        rebalance_dates or [],
        build_prior_index(disclosures),
        allow_mixed_period_types=allow_mixed_period_types,
    )


def main() -> int:
    args = build_parser().parse_args()
    rebalance_dates = (
        load_rebalance_dates(args.rebalance_dates, args.rebalance_date_values)
        if args.panel_mode == "rebalance"
        else None
    )
    rows = build_panel(
        read_csv(args.fundamentals),
        panel_mode=args.panel_mode,
        rebalance_dates=rebalance_dates,
        period_types={normalize_token(value) for value in args.period_type_values or []},
        allow_mixed_period_types=args.allow_mixed_period_types,
        document_type_contains={str(value).lower() for value in args.document_type_contains or []},
        exclude_document_type_contains={str(value).lower() for value in args.exclude_document_type_contains or []},
        require_useful=args.require_useful,
    )
    write_table(rows, args.out, format=args.output_format, fieldnames=fieldnames(args.panel_mode))
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_fundamental_factor_panel",
            file_path=args.out,
            vendor="local",
            schema_version="derived_fundamental_factor_panel_v0_1",
            date_range=output_date_range(rows, args.panel_mode) or args.run_label,
            notes=f"{len(rows)} rows; panel_mode={args.panel_mode}",
        )
    print(f"Wrote {len(rows)} derived fundamental factor rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
