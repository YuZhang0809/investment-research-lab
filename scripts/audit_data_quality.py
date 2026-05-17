from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from research_common import append_manifest, parse_bool, parse_date, parse_float, read_csv, write_csv


ISSUE_FIELDS = ["issue_type", "severity", "date", "code", "detail", "value", "threshold"]
SUMMARY_FIELDS = ["issue_type", "severity", "count"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit public research data quality before strategy conclusions.")
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--listings", type=Path)
    parser.add_argument("--contributions", type=Path, help="Optional CSV with date, code, contribution columns.")
    parser.add_argument("--jump-threshold", type=float, default=0.5)
    parser.add_argument("--max-calendar-gap-days", type=int, default=14)
    parser.add_argument("--stale-repeat-count", type=int, default=5)
    parser.add_argument("--max-single-name-contribution", type=float, default=0.25)
    parser.add_argument("--out", type=Path, default=Path("reports/engineering/data_quality_issues.csv"))
    parser.add_argument("--summary-out", type=Path, default=Path("reports/engineering/data_quality_summary.csv"))
    parser.add_argument("--report", type=Path, default=Path("reports/engineering/data_quality_report.md"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def require_columns(rows: list[dict[str, str]], required: set[str], *, table_name: str) -> None:
    if not rows:
        raise ValueError(f"{table_name} is empty.")
    columns = set(rows[0])
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"{table_name} is missing required column(s): {', '.join(missing)}")


def issue(
    issue_type: str,
    severity: str,
    *,
    row_date: date | str | None = None,
    code: str = "",
    detail: str = "",
    value: Any = "",
    threshold: Any = "",
) -> dict[str, Any]:
    return {
        "issue_type": issue_type,
        "severity": severity,
        "date": row_date.isoformat() if isinstance(row_date, date) else row_date or "",
        "code": code,
        "detail": detail,
        "value": value,
        "threshold": threshold,
    }


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def group_by_code(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        code = (row.get("code") or "").strip()
        if code:
            grouped[code].append(row)
    for values in grouped.values():
        values.sort(key=lambda item: parse_date(item.get("date"), field_name="prices.date") or date.min)
    return grouped


def audit_prices(
    rows: list[dict[str, str]],
    *,
    jump_threshold: float,
    max_calendar_gap_days: int,
    stale_repeat_count: int,
) -> list[dict[str, Any]]:
    require_columns(rows, {"date", "code", "unadjusted_close"}, table_name="prices")
    issues: list[dict[str, Any]] = []
    for code, values in group_by_code(rows).items():
        previous_date: date | None = None
        previous_effective_adjusted: float | None = None
        previous_adjustment: float | None = None
        stale_run_length = 1
        stale_run_reported = False
        cumulative_adjustment = 1.0
        for row in values:
            row_date = parse_date(row.get("date"), field_name="prices.date")
            adjusted_text = row.get("adjusted_close", "")
            adjusted = parse_float(adjusted_text)
            unadjusted = parse_float(row.get("unadjusted_close"))
            adjustment = parse_float(row.get("adjustment_factor"))
            if adjustment is not None and adjustment > 0:
                cumulative_adjustment *= adjustment
            effective_adjusted = adjusted
            if effective_adjusted is None and unadjusted is not None and adjustment is not None and adjustment > 0:
                effective_adjusted = unadjusted / cumulative_adjustment
            if row_date is None:
                continue
            if unadjusted is None or unadjusted <= 0:
                issues.append(issue("invalid_unadjusted_price", "error", row_date=row_date, code=code, value=row.get("unadjusted_close", "")))
            if (adjusted_text == "" or adjusted is None) and (adjustment is None or adjustment <= 0):
                issues.append(issue("missing_adjusted_price", "error", row_date=row_date, code=code))
            if adjusted is None and (adjustment is None or adjustment <= 0):
                issues.append(issue("missing_adjusted_price_and_adjustment_factor", "error", row_date=row_date, code=code))
            elif adjusted is not None and adjusted <= 0:
                issues.append(issue("invalid_adjusted_price", "error", row_date=row_date, code=code, value=adjusted))
            if parse_bool(row.get("tradable_flag"), default=True) is False:
                issues.append(issue("not_tradable_price_row", "warning", row_date=row_date, code=code))
            if parse_bool(row.get("price_limit_flag"), default=False) is True:
                issues.append(issue("price_limit_row", "info", row_date=row_date, code=code))
            if previous_date is not None and (row_date - previous_date).days > max_calendar_gap_days:
                issues.append(
                    issue(
                        "price_calendar_gap",
                        "warning",
                        row_date=row_date,
                        code=code,
                        detail=f"previous_date={previous_date.isoformat()}",
                        value=(row_date - previous_date).days,
                        threshold=max_calendar_gap_days,
                    )
                )
            if previous_effective_adjusted is not None and effective_adjusted is not None and previous_effective_adjusted > 0:
                period_return = effective_adjusted / previous_effective_adjusted - 1.0
                if abs(period_return) > jump_threshold:
                    issues.append(
                        issue(
                            "adjusted_price_jump",
                            "warning",
                            row_date=row_date,
                            code=code,
                            value=period_return,
                            threshold=jump_threshold,
                        )
                    )
                if effective_adjusted == previous_effective_adjusted:
                    stale_run_length += 1
                else:
                    stale_run_length = 1
                    stale_run_reported = False
                if stale_run_length >= stale_repeat_count and not stale_run_reported:
                    issues.append(
                        issue(
                            "stale_adjusted_price_run",
                            "warning",
                            row_date=row_date,
                            code=code,
                            value=stale_run_length,
                            threshold=stale_repeat_count,
                        )
                    )
                    stale_run_reported = True
            if previous_adjustment is not None and adjustment is not None and adjustment != previous_adjustment:
                issues.append(
                    issue(
                        "adjustment_factor_change",
                        "info",
                        row_date=row_date,
                        code=code,
                        detail=f"previous_adjustment_factor={previous_adjustment:.10g}",
                        value=adjustment,
                    )
                )
            previous_date = row_date
            previous_effective_adjusted = effective_adjusted
            previous_adjustment = adjustment
    return issues


def audit_listings(rows: list[dict[str, str]], price_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    require_columns(rows, {"code", "delisted_date"}, table_name="listings")
    last_price_by_code: dict[str, date] = {}
    for row in price_rows:
        row_date = parse_date(row.get("date"), field_name="prices.date")
        code = (row.get("code") or "").strip()
        if code and row_date is not None:
            last_price_by_code[code] = max(last_price_by_code.get(code, row_date), row_date)
    issues: list[dict[str, Any]] = []
    for row in rows:
        code = (row.get("code") or "").strip()
        delisted = parse_date(row.get("delisted_date"), field_name="listings.delisted_date") if row.get("delisted_date") else None
        if not code or delisted is None:
            continue
        last_price = last_price_by_code.get(code)
        if last_price is not None and last_price > delisted:
            issues.append(
                issue(
                    "price_after_delisting",
                    "error",
                    row_date=last_price,
                    code=code,
                    detail=f"delisted_date={delisted.isoformat()}",
                )
            )
    return issues


def audit_contributions(rows: list[dict[str, str]], *, max_single_name_contribution: float) -> list[dict[str, Any]]:
    require_columns(rows, {"date", "code", "contribution"}, table_name="contributions")
    issues: list[dict[str, Any]] = []
    for row in rows:
        row_date = parse_date(row.get("date"), field_name="contributions.date")
        contribution = parse_float(row.get("contribution"))
        if row_date is None or contribution is None:
            continue
        if abs(contribution) > max_single_name_contribution:
            issues.append(
                issue(
                    "single_name_abnormal_contribution",
                    "warning",
                    row_date=row_date,
                    code=row.get("code", ""),
                    value=contribution,
                    threshold=max_single_name_contribution,
                )
            )
    return issues


def summarize_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter((row["issue_type"], row["severity"]) for row in rows)
    return [
        {"issue_type": issue_type, "severity": severity, "count": count}
        for (issue_type, severity), count in sorted(counts.items())
    ]


def write_report(path: Path, issues: list[dict[str, Any]], summary_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    errors = sum(1 for row in issues if row["severity"] == "error")
    warnings = sum(1 for row in issues if row["severity"] == "warning")
    lines = [
        "# Data Quality Audit",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| total issues | {len(issues)} |",
        f"| errors | {errors} |",
        f"| warnings | {warnings} |",
        "",
        "## Issue Counts",
        "",
        "| issue type | severity | count |",
        "|---|---|---:|",
    ]
    if summary_rows:
        for row in summary_rows:
            lines.append(f"| {row['issue_type']} | {row['severity']} | {row['count']} |")
    else:
        lines.append("| none |  | 0 |")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "This audit checks generic public-engine contracts only. It flags rows that require review before performance conclusions; it does not repair data.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    if args.jump_threshold <= 0:
        raise ValueError("--jump-threshold must be positive.")
    if args.max_calendar_gap_days <= 0:
        raise ValueError("--max-calendar-gap-days must be positive.")
    if args.stale_repeat_count < 2:
        raise ValueError("--stale-repeat-count must be at least 2.")

    price_rows = read_csv(args.prices)
    issues = audit_prices(
        price_rows,
        jump_threshold=args.jump_threshold,
        max_calendar_gap_days=args.max_calendar_gap_days,
        stale_repeat_count=args.stale_repeat_count,
    )
    if args.listings:
        issues.extend(audit_listings(read_csv(args.listings), price_rows))
    if args.contributions:
        issues.extend(audit_contributions(read_csv(args.contributions), max_single_name_contribution=args.max_single_name_contribution))

    summary_rows = summarize_issues(issues)
    write_csv(args.out, [{key: fmt(value) for key, value in row.items()} for row in issues], ISSUE_FIELDS)
    write_csv(args.summary_out, summary_rows, SUMMARY_FIELDS)
    write_report(args.report, issues, summary_rows)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_data_quality_audit",
            file_path=args.out,
            vendor="local",
            schema_version="data_quality_audit_v0_1",
            date_range="",
            notes=f"{len(issues)} issues; report={args.report.as_posix()}",
        )
    print(f"Wrote {len(issues)} data quality issues to {args.out}")
    print(f"Wrote data quality report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
