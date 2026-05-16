from __future__ import annotations

import argparse
from pathlib import Path

from research_common import append_manifest, date_range_from_rows, read_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile CSV scale and estimate when to move to Parquet.")
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--sample-size", type=int, default=300)
    parser.add_argument("--target-sizes", nargs="+", type=int, default=[300, 800, 1500, 3000])
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports/engineering/sample_expansion_profile.md"),
    )
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024


def unique_codes(rows: list[dict[str, str]]) -> int:
    return len({row.get("code", "") for row in rows if row.get("code")})


def write_report(
    path: Path,
    *,
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
    prices_path: Path,
    fundamentals_path: Path,
    sample_size: int,
    target_sizes: list[int],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    price_count = len(price_rows)
    fundamental_count = len(fundamental_rows)
    price_codes = unique_codes(price_rows) or sample_size
    fundamental_codes = unique_codes(fundamental_rows) or sample_size
    price_rows_per_code = price_count / price_codes if price_codes else 0
    fundamental_rows_per_code = fundamental_count / fundamental_codes if fundamental_codes else 0
    price_mb_per_code = mb(prices_path) / price_codes if price_codes else 0
    fundamental_mb_per_code = mb(fundamentals_path) / fundamental_codes if fundamental_codes else 0

    lines = [
        "# Sample Expansion Profile",
        "",
        "## Current Sample",
        "",
        "| data | rows | codes | file size | date range |",
        "|---|---:|---:|---:|---|",
        f"| prices | {price_count:,} | {price_codes:,} | {mb(prices_path):.2f} MB | {date_range_from_rows(price_rows, 'date')} |",
        f"| fundamentals | {fundamental_count:,} | {fundamental_codes:,} | {mb(fundamentals_path):.2f} MB | {date_range_from_rows(fundamental_rows, 'available_date')} |",
        "",
        "## Linear Scale Estimate",
        "",
        "| target codes | price rows | price file | fundamental rows | fundamental file | CSV stance |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for target in target_sizes:
        est_price_rows = int(price_rows_per_code * target)
        est_fund_rows = int(fundamental_rows_per_code * target)
        est_price_mb = price_mb_per_code * target
        est_fund_mb = fundamental_mb_per_code * target
        total_rows = est_price_rows + est_fund_rows
        if total_rows < 1_000_000:
            stance = "CSV ok"
        elif total_rows < 5_000_000:
            stance = "Prefer Parquet for repeated runs"
        else:
            stance = "Parquet required; database still optional"
        lines.append(
            f"| {target:,} | {est_price_rows:,} | {est_price_mb:.2f} MB | {est_fund_rows:,} | {est_fund_mb:.2f} MB | {stance} |"
        )

    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- Keep CSV for the current 300-code engineering sample and for human-readable audit outputs.",
            "- Add Parquet once repeated walk-forward runs exceed about one million rows or become IO-bound.",
            "- Do not introduce a database in v0.1. The bottleneck is execution realism and data discipline, not relational querying.",
            "- Keep `data_manifest.csv` as the audit spine regardless of CSV or Parquet storage.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    price_rows = read_csv(args.prices)
    fundamental_rows = read_csv(args.fundamentals)
    write_report(
        args.out,
        price_rows=price_rows,
        fundamental_rows=fundamental_rows,
        prices_path=args.prices,
        fundamentals_path=args.fundamentals,
        sample_size=args.sample_size,
        target_sizes=args.target_sizes,
    )
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_scale_profile",
            file_path=args.out,
            vendor="local",
            schema_version="scale_profile_v0_1",
            date_range=date_range_from_rows(price_rows, "date"),
            notes=f"sample_size={args.sample_size}",
        )
    print(f"Wrote scale profile to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
