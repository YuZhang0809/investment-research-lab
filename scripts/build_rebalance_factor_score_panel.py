from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from build_factors import build_factors, factor_output_fields
from build_scores import STRATEGY_VERSION_CHOICES, build_scores
from research_common import (
    append_manifest,
    checksum,
    format_csv_value,
    load_yaml,
    normalize_row_value,
    parse_bool,
    parse_date,
    read_csv,
    read_table,
    trading_calendar_from_rows,
    write_table,
)
from run_qvm_walkforward import UNIVERSE_CACHE_FIELDS, rebalance_dates, score_cache_fields


PANEL_EXTRA_FIELDS = [
    "included_flag",
    "exclusion_reason",
    "adjusted_close",
    "fundamental_available_date",
    "rank_score",
    "candidate_rank",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a rebalance-level factor/score panel from a price/universe panel.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--price-universe-panel", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="monthly")
    parser.add_argument("--strategy-version", choices=STRATEGY_VERSION_CHOICES, default="qvm")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def text_date(value: Any, *, field_name: str) -> str:
    parsed = parse_date(value, field_name=field_name)
    return parsed.isoformat() if parsed else ""


def unique_fields(fields: list[str]) -> list[str]:
    return list(dict.fromkeys(fields))


def read_rows(path: Path, input_format: str) -> list[dict[str, str]]:
    if input_format == "auto":
        return read_csv(path)
    frame = read_table(path, format=input_format)
    return [
        {str(key): normalize_row_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def factor_score_panel_fields(config: dict[str, Any], raw_factors: list[str]) -> list[str]:
    return unique_fields(
        [
            "rebalance_date",
            "code",
            *PANEL_EXTRA_FIELDS,
            *UNIVERSE_CACHE_FIELDS,
            *factor_output_fields(config),
            *score_cache_fields(raw_factors),
        ]
    )


def panel_rows_for_date(rows: list[dict[str, str]], rebalance_date: date) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if parse_date(row.get("rebalance_date"), field_name="price_universe_panel.rebalance_date") == rebalance_date
    ]


def included_universe_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    included: list[dict[str, Any]] = []
    for row in rows:
        flag = parse_bool(row.get("included_flag"), default=None)
        if flag is None:
            raise ValueError(f"Invalid included_flag in price/universe panel: {row.get('included_flag')!r}")
        if flag:
            included.append(dict(row))
    return included


def merge_panel_rows(
    *,
    panel_rows: list[dict[str, str]],
    factor_rows: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    factor_by_code = {str(row.get("code", "")): row for row in factor_rows}
    score_by_code = {str(row.get("code", "")): row for row in score_rows}
    rows: list[dict[str, Any]] = []
    for panel_row in sorted(panel_rows, key=lambda item: str(item.get("code", ""))):
        code = str(panel_row.get("code", ""))
        included = parse_bool(panel_row.get("included_flag"), default=None)
        if included is None:
            raise ValueError(f"Invalid included_flag in price/universe panel: {panel_row.get('included_flag')!r}")
        factor = factor_by_code.get(code, {})
        score = score_by_code.get(code, {})
        rows.append(
            {
                **panel_row,
                **factor,
                **score,
                "rebalance_date": text_date(
                    score.get("rebalance_date") or factor.get("rebalance_date") or panel_row.get("rebalance_date"),
                    field_name="factor_score_panel.rebalance_date",
                ),
                "code": code,
                "included_flag": "true" if included else "false",
                "exclusion_reason": "" if included else panel_row.get("exclusion_reason", ""),
                "adjusted_close": panel_row.get("adjusted_close", ""),
                "fundamental_available_date": factor.get("fundamentals_available_date", ""),
                "rank_score": score.get("composite_score", ""),
                "candidate_rank": score.get("rank", ""),
            }
        )
    return rows


def build_factor_score_panel_rows(
    *,
    config: dict[str, Any],
    price_universe_panel_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
    start_date: date,
    end_date: date,
    frequency: str,
    strategy_version: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    dates = rebalance_dates(trading_calendar_from_rows(price_rows), start_date, end_date, frequency)
    if not dates:
        raise ValueError("No rebalance dates found in price file for the requested window.")

    output_rows: list[dict[str, Any]] = []
    all_raw_factors: list[str] = []
    for rebalance_date in dates:
        panel_rows = panel_rows_for_date(price_universe_panel_rows, rebalance_date)
        if not panel_rows:
            raise ValueError(f"No price/universe panel rows found for rebalance date {rebalance_date}.")
        universe_rows = included_universe_rows(panel_rows)
        factor_rows = build_factors(
            rebalance_date=rebalance_date,
            universe_rows=universe_rows,
            price_rows=price_rows,
            fundamental_rows=fundamental_rows,
            config=config,
        )
        score_rows, raw_factors = build_scores(
            config=config,
            factor_rows=factor_rows,
            strategy_version=strategy_version,
        )
        for raw_factor in raw_factors:
            if raw_factor not in all_raw_factors:
                all_raw_factors.append(raw_factor)
        output_rows.extend(
            merge_panel_rows(
                panel_rows=panel_rows,
                factor_rows=factor_rows,
                score_rows=score_rows,
            )
        )
    return output_rows, all_raw_factors


def build_factor_score_panel(
    *,
    config: dict[str, Any],
    price_universe_panel_path: Path,
    prices_path: Path,
    fundamentals_path: Path,
    start_date: str,
    end_date: str,
    frequency: str,
    strategy_version: str,
    out_path: Path,
    output_format: str,
    input_format: str = "auto",
) -> int:
    parsed_start = parse_date(start_date, field_name="start_date")
    parsed_end = parse_date(end_date, field_name="end_date")
    if parsed_start is None or parsed_end is None:
        raise ValueError("start-date and end-date are required")
    rows, raw_factors = build_factor_score_panel_rows(
        config=config,
        price_universe_panel_rows=read_rows(price_universe_panel_path, input_format),
        price_rows=read_rows(prices_path, input_format),
        fundamental_rows=read_rows(fundamentals_path, input_format),
        start_date=parsed_start,
        end_date=parsed_end,
        frequency=frequency,
        strategy_version=strategy_version,
    )
    fieldnames = factor_score_panel_fields(config, raw_factors)
    normalized_rows = [
        {field: format_csv_value(row.get(field, "")) for field in fieldnames}
        for row in rows
    ]
    write_table(normalized_rows, out_path, format=output_format, fieldnames=fieldnames)
    return len(normalized_rows)


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    row_count = build_factor_score_panel(
        config=config,
        price_universe_panel_path=args.price_universe_panel,
        prices_path=args.prices,
        fundamentals_path=args.fundamentals,
        start_date=args.start_date,
        end_date=args.end_date,
        frequency=args.frequency,
        strategy_version=args.strategy_version,
        out_path=args.out,
        input_format=args.input_format,
        output_format=args.output_format,
    )
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_rebalance_factor_score_panel",
            file_path=args.out,
            vendor="local",
            schema_version="rebalance_factor_score_panel_v0_1",
            date_range=f"{args.start_date}..{args.end_date}",
            notes=(
                f"strategy_version={args.strategy_version}; rows={row_count}; "
                f"price_universe_panel={checksum(args.price_universe_panel)}; "
                f"prices={checksum(args.prices)}; fundamentals={checksum(args.fundamentals)}"
            ),
        )
    print(f"Wrote {row_count} factor/score panel rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
