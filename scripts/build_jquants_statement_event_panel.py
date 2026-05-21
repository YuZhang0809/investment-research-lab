from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from research_common import append_manifest, parse_date, read_csv, write_table


FIELDNAMES = [
    "event_id",
    "announcement_datetime",
    "code",
    "company_name",
    "document_type",
    "event_label",
    "title",
    "url_or_doc_id",
    "parsed_flag",
    "parse_confidence",
    "notes",
    "period_type",
    "period_end",
    "disclosure_number",
    "forecast_eps",
    "forecast_dividend_per_share",
    "result_dividend_per_share",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a public-safe generic event panel from J-Quants-style statements rows."
    )
    parser.add_argument("--statements", required=True, type=Path)
    parser.add_argument(
        "--document-type-contains",
        action="append",
        dest="document_type_contains",
        help="Keep rows whose TypeOfDocument/document_type contains this text. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-document-type-contains",
        action="append",
        dest="exclude_document_type_contains",
        help="Drop rows whose TypeOfDocument/document_type contains this text. Can be repeated.",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--run-label", default="statement_events")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def first_text(row: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def document_type(row: dict[str, Any]) -> str:
    return first_text(row, "document_type", "TypeOfDocument")


def contains_any(value: str, needles: set[str] | None) -> bool:
    if not needles:
        return False
    lowered = value.lower()
    return any(needle in lowered for needle in needles)


def parse_announcement_datetime(row: dict[str, Any]) -> str:
    raw_date = first_text(row, "announcement_date", "available_date", "disclosure_date", "DisclosedDate")
    parsed_date = parse_date(raw_date, field_name="statements.DisclosedDate")
    if parsed_date is None:
        return ""
    raw_time = first_text(row, "announcement_time", "available_time", "disclosure_time", "DisclosedTime")
    if not raw_time:
        return parsed_date.isoformat()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(raw_time, fmt).time()
            return datetime.combine(parsed_date, parsed_time).isoformat(sep=" ")
        except ValueError:
            continue
    raise ValueError(f"Invalid statements.DisclosedTime: {raw_time!r}")


def period_type(row: dict[str, Any]) -> str:
    return first_text(row, "period_type", "TypeOfCurrentPeriod").lower()


def period_end(row: dict[str, Any]) -> str:
    return first_text(row, "period_end", "CurrentPeriodEndDate")


def disclosure_number(row: dict[str, Any]) -> str:
    return first_text(row, "disclosure_number", "DisclosureNumber")


def event_label_for_document_type(value: str) -> str:
    lowered = value.lower()
    if "dividendforecastrevision" in lowered:
        return "dividend_forecast_revision"
    if "earnforecastrevision" in lowered or "forecastrevision" in lowered:
        return "company_forecast_revision"
    if "financialstatements" in lowered:
        return "financial_statement"
    return "other_statement_event"


def slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return text or "blank"


def statement_event_id(row: dict[str, Any], announcement: str, index: int) -> str:
    parts = [
        "statement",
        first_text(row, "code", "Code", "LocalCode"),
        announcement.replace("-", "").replace(":", "").replace(" ", "T"),
        disclosure_number(row) or str(index),
    ]
    return "_".join(slug(part) for part in parts if part)


def normalize_rows(
    rows: list[dict[str, str]],
    *,
    document_type_contains: set[str] | None = None,
    exclude_document_type_contains: set[str] | None = None,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        code = first_text(row, "code", "Code", "LocalCode")
        if not code:
            continue
        doc_type = document_type(row)
        if document_type_contains and not contains_any(doc_type, document_type_contains):
            continue
        if contains_any(doc_type, exclude_document_type_contains):
            continue
        announcement = parse_announcement_datetime(row)
        if not announcement:
            continue
        label = event_label_for_document_type(doc_type)
        event_id = first_text(row, "event_id") or statement_event_id(row, announcement, index)
        title = first_text(row, "title", "Title")
        if not title:
            title = doc_type
        output.append(
            {
                "event_id": event_id,
                "announcement_datetime": announcement,
                "code": code,
                "company_name": first_text(row, "company_name", "CompanyName"),
                "document_type": doc_type,
                "event_label": label,
                "title": title,
                "url_or_doc_id": first_text(row, "url_or_doc_id", "DisclosureNumber"),
                "parsed_flag": "true",
                "parse_confidence": "1.000",
                "notes": (
                    "fundamental_improvement_drift_proxy_input"
                    if label in {"financial_statement", "company_forecast_revision"}
                    else ""
                ),
                "period_type": period_type(row),
                "period_end": period_end(row),
                "disclosure_number": disclosure_number(row),
                "forecast_eps": first_text(row, "forecast_eps", "ForecastEarningsPerShare"),
                "forecast_dividend_per_share": first_text(
                    row,
                    "forecast_dividend_per_share",
                    "ForecastDividendPerShareAnnual",
                    "ForecastDividendPerShareFiscalYearEnd",
                ),
                "result_dividend_per_share": first_text(
                    row,
                    "result_dividend_per_share",
                    "ResultDividendPerShareAnnual",
                    "ResultDividendPerShareFiscalYearEnd",
                ),
            }
        )
    output.sort(key=lambda item: (item["announcement_datetime"], item["code"], item["event_id"]))
    return output


def date_range(rows: list[dict[str, str]]) -> str:
    dates = [row["announcement_datetime"][:10] for row in rows if row.get("announcement_datetime")]
    if not dates:
        return ""
    return f"{min(dates)}..{max(dates)}"


def main() -> int:
    args = build_parser().parse_args()
    rows = normalize_rows(
        read_csv(args.statements),
        document_type_contains={value.lower() for value in args.document_type_contains or []} or None,
        exclude_document_type_contains={value.lower() for value in args.exclude_document_type_contains or []} or None,
    )
    write_table(rows, args.out, format=args.output_format, fieldnames=FIELDNAMES)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_jquants_statement_event_panel",
            file_path=args.out,
            vendor="local",
            schema_version="jquants_statement_events_v0_1",
            date_range=date_range(rows) or args.run_label,
            notes=f"{len(rows)} statement event rows; Standard-compatible fields only",
        )
    print(f"Wrote {len(rows)} statement event rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
