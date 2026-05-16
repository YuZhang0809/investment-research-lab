from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from research_common import append_manifest, parse_date, parse_float, parse_int, read_csv, write_csv


@dataclass
class PricePoint:
    date: date
    adjusted_close: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a frozen-feature ML ranker dataset from QVM score CSVs.")
    parser.add_argument("--scores-dir", type=Path, default=Path("data/processed/scores"))
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--holding-days", type=int, default=63)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/ml"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/ml"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def build_price_index(rows: list[dict[str, str]]) -> dict[str, list[PricePoint]]:
    grouped: dict[str, list[PricePoint]] = defaultdict(list)
    for row in rows:
        code = row.get("code", "")
        row_date = parse_date(row.get("date"), field_name="prices.date")
        adjusted = parse_float(row.get("adjusted_close") or row.get("unadjusted_close"))
        if not code or row_date is None or adjusted is None or adjusted <= 0:
            continue
        grouped[code].append(PricePoint(row_date, adjusted))
    for points in grouped.values():
        points.sort(key=lambda point: point.date)
    return grouped


def first_on_or_after(points: list[PricePoint], target: date) -> int | None:
    for index, point in enumerate(points):
        if point.date >= target:
            return index
    return None


def future_return(points: list[PricePoint], rebalance_date: date, holding_days: int) -> float | None:
    entry_index = first_on_or_after(points, rebalance_date)
    if entry_index is None:
        return None
    exit_index = entry_index + holding_days
    if exit_index >= len(points):
        return None
    entry = points[entry_index].adjusted_close
    exit_value = points[exit_index].adjusted_close
    if entry <= 0:
        return None
    return exit_value / entry - 1.0


def score_files(scores_dir: Path, start: date, end: date) -> list[Path]:
    paths = []
    for path in scores_dir.glob("scores_*.csv"):
        token = path.stem.replace("scores_", "")
        if len(token) != 6:
            continue
        score_date = parse_date(f"{token[:4]}-{token[4:]}-01", field_name="score_month")
        if score_date and start.replace(day=1) <= score_date <= end.replace(day=1):
            paths.append(path)
    return sorted(paths)


def fmt(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def write_report(path: Path, rows: list[dict[str, str]], start: date, end: date, holding_days: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    returns = [parse_float(row.get("future_return")) for row in rows]
    clean = [value for value in returns if value is not None]
    avg_return = sum(clean) / len(clean) if clean else None
    lines = [
        "# ML Ranker Dataset Report",
        "",
        "- status: dataset only; no live model and no trading signal",
        f"- window: {start.isoformat()}..{end.isoformat()}",
        f"- holding_days label: {holding_days}",
        f"- rows: {len(rows):,}",
        f"- average future_return: {fmt(avg_return)}",
        "",
        "## Guardrails",
        "",
        "- Use walk-forward only. Random split is not allowed.",
        "- Treat QVM as the baseline, not as a feature zoo seed.",
        "- Do not promote ML unless frozen-parameter results beat the hand-built QVM baseline after costs.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    start = parse_date(args.start_date, field_name="start_date")
    end = parse_date(args.end_date, field_name="end_date")
    if start is None or end is None:
        raise ValueError("start-date and end-date are required")
    price_index = build_price_index(read_csv(args.prices))
    dataset_rows: list[dict[str, str]] = []
    for path in score_files(args.scores_dir, start, end):
        for row in read_csv(path):
            rebalance_date = parse_date(row.get("rebalance_date"), field_name="scores.rebalance_date")
            if rebalance_date is None or not (start <= rebalance_date <= end):
                continue
            ret = future_return(price_index.get(row.get("code", ""), []), rebalance_date, args.holding_days)
            if ret is None:
                continue
            dataset_rows.append(
                {
                    "rebalance_date": rebalance_date.isoformat(),
                    "code": row.get("code", ""),
                    "sector": row.get("sector", ""),
                    "rank": fmt(parse_int(row.get("rank"))),
                    "quality_score": fmt(parse_float(row.get("quality_score"))),
                    "value_score": fmt(parse_float(row.get("value_score"))),
                    "momentum_score": fmt(parse_float(row.get("momentum_score"))),
                    "qvm_score": fmt(parse_float(row.get("qvm_score"))),
                    "future_return": fmt(ret),
                    "label_holding_days": str(args.holding_days),
                }
            )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    token = f"{start.strftime('%Y%m')}_{end.strftime('%Y%m')}_{args.holding_days}d"
    output_path = args.out_dir / f"qvm_ranker_dataset_{token}.csv"
    report_path = args.report_dir / f"qvm_ranker_dataset_{token}.md"
    fieldnames = [
        "rebalance_date",
        "code",
        "sector",
        "rank",
        "quality_score",
        "value_score",
        "momentum_score",
        "qvm_score",
        "future_return",
        "label_holding_days",
    ]
    write_csv(output_path, dataset_rows, fieldnames)
    write_report(report_path, dataset_rows, start, end, args.holding_days)
    if not args.no_manifest:
        for source, path, schema, notes in [
            ("derived_ml_ranker_dataset", output_path, "ml_ranker_dataset_v0_1", f"{len(dataset_rows)} rows"),
            ("derived_ml_ranker_report", report_path, "ml_ranker_report_v0_1", "dataset report"),
        ]:
            append_manifest(
                args.manifest,
                source=source,
                file_path=path,
                vendor="local",
                schema_version=schema,
                date_range=f"{start.isoformat()}..{end.isoformat()}",
                notes=notes,
            )
    print(f"Wrote ML ranker dataset to {output_path}")
    print(f"Wrote ML ranker report to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
