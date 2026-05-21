from __future__ import annotations

import argparse
import tempfile
import time
import tracemalloc
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from build_price_volume_factor_panel import build_panel
from research_common import append_manifest, require_pandas, write_csv, write_table


PROFILE_SCHEMA_VERSION = "price_volume_factor_panel_profile_v0_1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile price-volume factor panel runtime, output scale, and audit rates.")
    parser.add_argument("--prices", type=Path, help="Optional existing synthetic/local OHLCV price file.")
    parser.add_argument("--universe-panel", type=Path, help="Optional rebalance_date+code panel used to restrict output rows.")
    parser.add_argument("--rebalance-dates", type=Path, help="Optional CSV/Parquet with rebalance_date or date column.")
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values", help="YYYY-MM-DD; can be repeated.")
    parser.add_argument("--group-field", default="sector", help="Discrete field to preserve for grouped diagnostics.")
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--panel-out", type=Path, help="Optional path to write the generated factor panel.")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--summary-out", type=Path, default=Path("reports/engineering/price_volume_factor_panel_profile.csv"))
    parser.add_argument("--report", type=Path, default=Path("reports/engineering/price_volume_factor_panel_profile.md"))
    parser.add_argument("--run-label", default="price_volume_profile")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    parser.add_argument("--synthetic-codes", type=int, default=40)
    parser.add_argument("--synthetic-days", type=int, default=260)
    parser.add_argument("--synthetic-rebalances", type=int, default=6)
    parser.add_argument("--synthetic-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--work-dir", type=Path, help="Optional directory to keep generated synthetic inputs.")
    return parser


def mb(value: float) -> float:
    return value / 1024 / 1024


def count_table_rows(path: Path, table_format: str = "auto") -> int | None:
    normalized = table_format.lower()
    if normalized == "auto":
        normalized = "parquet" if path.suffix.lower() == ".parquet" or path.is_dir() else "csv"
    if normalized == "csv":
        with path.open("r", encoding="utf-8-sig") as file:
            return max(0, sum(1 for _line in file) - 1)
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return None
    if path.is_dir():
        return sum(pq.ParquetFile(file).metadata.num_rows for file in path.glob("*.parquet"))
    return pq.ParquetFile(path).metadata.num_rows


def synthetic_rebalance_dates(start: date, days: int, count: int) -> list[date]:
    if days <= 0:
        raise ValueError("synthetic-days must be positive.")
    if count <= 0:
        raise ValueError("synthetic-rebalances must be positive.")
    step = max(1, days // count)
    offsets = list(range(step - 1, days, step))[:count]
    if not offsets:
        offsets = [days - 1]
    return [start + timedelta(days=offset) for offset in offsets]


def synthetic_prices(codes: int, days: int):
    pd = require_pandas()
    if codes <= 0:
        raise ValueError("synthetic-codes must be positive.")
    if days <= 0:
        raise ValueError("synthetic-days must be positive.")
    start = date(2020, 1, 1)
    rows: list[dict[str, Any]] = []
    sectors = ["SectorA", "SectorB", "SectorC", "SectorD", "SectorE"]
    for code_index in range(codes):
        code = f"S{code_index + 1:05d}"
        sector = sectors[code_index % len(sectors)]
        base = 50.0 + code_index * 0.25
        for offset in range(days):
            current = start + timedelta(days=offset)
            drift = offset * (0.01 + (code_index % 7) * 0.001)
            cycle = ((offset + code_index) % 11 - 5) * 0.03
            open_value = base + drift + cycle
            close = open_value + ((offset % 5) - 2) * 0.04
            high = max(open_value, close) + 0.30
            low = max(0.01, min(open_value, close) - 0.25)
            volume = 10_000 + code_index * 25 + offset * 3
            rows.append(
                {
                    "date": current.isoformat(),
                    "code": code,
                    "sector": sector,
                    "unadjusted_open": f"{open_value:.4f}",
                    "unadjusted_high": f"{high:.4f}",
                    "unadjusted_low": f"{low:.4f}",
                    "unadjusted_close": f"{close:.4f}",
                    "adjusted_close": f"{close:.4f}",
                    "volume": str(volume),
                    "trading_value": f"{volume * close:.4f}",
                    "price_limit_flag": "false",
                }
            )
    return pd.DataFrame(rows), synthetic_rebalance_dates(start, days, count=max(1, min(days, 6)))


def write_synthetic_inputs(
    directory: Path,
    *,
    codes: int,
    days: int,
    rebalances: int,
    table_format: str,
) -> tuple[Path, Path, list[str], int, int]:
    pd = require_pandas()
    directory.mkdir(parents=True, exist_ok=True)
    prices_frame, _default_dates = synthetic_prices(codes, days)
    dates = synthetic_rebalance_dates(date(2020, 1, 1), days, rebalances)
    price_path = directory / f"synthetic_price_volume_prices.{table_format}"
    universe_path = directory / f"synthetic_price_volume_universe.{table_format}"
    if table_format == "csv":
        price_path = price_path.with_suffix(".csv")
        universe_path = universe_path.with_suffix(".csv")
    rebalance_values = [value.isoformat() for value in dates]
    universe_rows = []
    sectors = prices_frame[["code", "sector"]].drop_duplicates("code")
    for rebalance_date in rebalance_values:
        for row in sectors.to_dict(orient="records"):
            universe_rows.append(
                {
                    "rebalance_date": rebalance_date,
                    "code": row["code"],
                    "sector": row["sector"],
                    "included_flag": "true",
                }
            )
    universe_frame = pd.DataFrame(universe_rows)
    write_table(prices_frame, price_path, format=table_format)
    write_table(universe_frame, universe_path, format=table_format)
    return price_path, universe_path, rebalance_values, len(prices_frame), len(universe_frame)


def table_memory_mb(frame: Any) -> float:
    if hasattr(frame, "memory_usage"):
        return mb(float(frame.memory_usage(deep=True).sum()))
    return 0.0


def flag_counts(frame: Any, field: str) -> dict[str, int]:
    values = frame[field].fillna("").astype(str) if field in frame.columns else []
    counts: dict[str, int] = {}
    for value in values:
        normalized = value or "none"
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def profile_price_volume_factor_panel(
    *,
    prices_path: Path,
    universe_panel_path: Path | None,
    rebalance_dates_path: Path | None,
    rebalance_date_values: list[str] | None,
    group_field: str | None,
    input_format: str,
    panel_out: Path | None = None,
    output_format: str = "parquet",
    run_label: str = "price_volume_profile",
    synthetic: bool = False,
    price_rows: int | None = None,
    universe_rows: int | None = None,
) -> tuple[dict[str, Any], Any, list[str]]:
    if price_rows is None:
        price_rows = count_table_rows(prices_path, input_format)
    if universe_panel_path is not None and universe_rows is None:
        universe_rows = count_table_rows(universe_panel_path, input_format)

    tracemalloc.start()
    started = time.perf_counter()
    try:
        panel, fields = build_panel(
            prices_path,
            rebalance_dates_path=rebalance_dates_path,
            rebalance_date_values=rebalance_date_values,
            universe_panel_path=universe_panel_path,
            group_field=group_field,
            input_format=input_format,
        )
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    runtime_seconds = time.perf_counter() - started
    write_seconds = 0.0
    if panel_out is not None:
        write_started = time.perf_counter()
        write_table(panel, panel_out, format=output_format, fieldnames=fields)
        write_seconds = time.perf_counter() - write_started

    output_rows = len(panel)
    rebalance_count = len(set(panel["rebalance_date"].dropna().astype(str))) if output_rows else 0
    missing_rows = int(panel["missing_flags"].fillna("").astype(str).ne("").sum()) if output_rows else 0
    coverage_issue_rows = int(panel["coverage_flags"].fillna("").astype(str).ne("").sum()) if output_rows else 0
    vwap_counts = flag_counts(panel, "vwap_proxy_flag")
    summary = {
        "run_label": run_label,
        "synthetic": synthetic,
        "price_rows": price_rows if price_rows is not None else "",
        "universe_panel_rows": universe_rows if universe_rows is not None else "",
        "rebalance_count": rebalance_count,
        "output_rows": output_rows,
        "runtime_seconds": f"{runtime_seconds:.4f}",
        "panel_write_seconds": f"{write_seconds:.4f}",
        "peak_python_memory_mb": f"{mb(float(peak)):.2f}",
        "panel_memory_mb": f"{table_memory_mb(panel):.2f}",
        "missing_row_count": missing_rows,
        "missing_row_rate": f"{missing_rows / output_rows:.6f}" if output_rows else "0",
        "coverage_issue_row_count": coverage_issue_rows,
        "coverage_issue_row_rate": f"{coverage_issue_rows / output_rows:.6f}" if output_rows else "0",
        "coverage_clean_rate": f"{1 - coverage_issue_rows / output_rows:.6f}" if output_rows else "0",
        "vwap_proxy_ok_count": vwap_counts.get("none", 0),
        "vwap_proxy_flagged_count": output_rows - vwap_counts.get("none", 0),
        "missing_volume_count": vwap_counts.get("missing_volume", 0),
        "zero_volume_count": vwap_counts.get("zero_volume", 0),
        "missing_trading_value_count": vwap_counts.get("missing_trading_value", 0),
        "panel_output_path": str(panel_out or ""),
    }
    return summary, panel, fields


def write_report(path: Path, summary: dict[str, Any], *, used_universe_panel: bool, used_explicit_rebalances: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Price-Volume Factor Panel Profile",
        "",
        "This report profiles generic engineering behavior only. It does not contain strategy conclusions.",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key, value in summary.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Memory Safety Notes",
            "",
            f"- universe panel supplied: {str(used_universe_panel).lower()}",
            f"- explicit rebalance dates supplied: {str(used_explicit_rebalances).lower()}",
            "- For full-market research, supply both a rebalance-date list and a universe panel so output rows are `rebalance_date x included codes`, not every daily price date.",
            "- Peak memory is measured with Python `tracemalloc`; pandas/native memory may be higher, so private full-market runs should also watch process RSS.",
            "- Keep generated synthetic inputs out of git unless they are tiny fixtures.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


SUMMARY_FIELDS = [
    "run_label",
    "synthetic",
    "price_rows",
    "universe_panel_rows",
    "rebalance_count",
    "output_rows",
    "runtime_seconds",
    "panel_write_seconds",
    "peak_python_memory_mb",
    "panel_memory_mb",
    "missing_row_count",
    "missing_row_rate",
    "coverage_issue_row_count",
    "coverage_issue_row_rate",
    "coverage_clean_rate",
    "vwap_proxy_ok_count",
    "vwap_proxy_flagged_count",
    "missing_volume_count",
    "zero_volume_count",
    "missing_trading_value_count",
    "panel_output_path",
]


def run_with_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.prices is not None:
        summary, _panel, _fields = profile_price_volume_factor_panel(
            prices_path=args.prices,
            universe_panel_path=args.universe_panel,
            rebalance_dates_path=args.rebalance_dates,
            rebalance_date_values=args.rebalance_date_values,
            group_field=args.group_field,
            input_format=args.input_format,
            panel_out=args.panel_out,
            output_format=args.output_format,
            run_label=args.run_label,
            synthetic=False,
        )
        write_csv(args.summary_out, [summary], SUMMARY_FIELDS)
        write_report(
            args.report,
            summary,
            used_universe_panel=args.universe_panel is not None,
            used_explicit_rebalances=args.rebalance_dates is not None or bool(args.rebalance_date_values),
        )
        return summary

    def run_synthetic(work_dir: Path) -> dict[str, Any]:
        price_path, universe_path, rebalance_values, price_rows, universe_rows = write_synthetic_inputs(
            work_dir,
            codes=args.synthetic_codes,
            days=args.synthetic_days,
            rebalances=args.synthetic_rebalances,
            table_format=args.synthetic_format,
        )
        panel_out = args.panel_out
        if panel_out is None and args.work_dir is not None:
            panel_out = work_dir / f"synthetic_price_volume_panel.{args.output_format}"
        summary, _panel, _fields = profile_price_volume_factor_panel(
            prices_path=price_path,
            universe_panel_path=universe_path,
            rebalance_dates_path=None,
            rebalance_date_values=rebalance_values,
            group_field=args.group_field,
            input_format=args.synthetic_format,
            panel_out=panel_out,
            output_format=args.output_format,
            run_label=args.run_label,
            synthetic=True,
            price_rows=price_rows,
            universe_rows=universe_rows,
        )
        write_csv(args.summary_out, [summary], SUMMARY_FIELDS)
        write_report(args.report, summary, used_universe_panel=True, used_explicit_rebalances=True)
        return summary

    if args.work_dir is not None:
        return run_synthetic(args.work_dir)
    with tempfile.TemporaryDirectory() as temp_dir:
        return run_synthetic(Path(temp_dir))


def main() -> int:
    args = build_parser().parse_args()
    summary = run_with_args(args)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_price_volume_factor_panel_profile",
            file_path=args.summary_out,
            vendor="local",
            schema_version=PROFILE_SCHEMA_VERSION,
            date_range=str(summary.get("run_label", "")),
            notes=f"output_rows={summary.get('output_rows', '')}; synthetic={summary.get('synthetic', '')}",
        )
    print(f"Wrote price-volume factor panel profile to {args.summary_out}")
    print(f"Wrote price-volume factor panel profile report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
