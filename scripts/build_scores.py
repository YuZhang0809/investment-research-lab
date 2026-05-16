from __future__ import annotations

import argparse
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from research_common import append_manifest, load_yaml, month_key, parse_date, parse_float, read_csv, write_csv


DEFAULT_QUALITY_FACTORS = ["operating_profit_to_total_assets", "equity_to_assets"]
DEFAULT_VALUE_FACTORS = ["earnings_yield", "book_to_market"]
DEFAULT_MOMENTUM_FACTORS = ["return_12_1", "return_6_1"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build QVM z-scores and ranks from factor CSV.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--factors", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/scores"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def winsorize(value: float | None, lower: float, upper: float) -> float | None:
    if value is None:
        return None
    return min(max(value, lower), upper)


def average_available(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return mean(clean)


def configured_factors(config: dict[str, Any], group: str, defaults: list[str]) -> list[str]:
    values = config["factors"].get(group, {}).get("variables")
    if not values:
        return defaults
    return [str(value) for value in values]


def fmt(value: Any) -> Any:
    if isinstance(value, float):
        return f"{value:.10g}"
    return value


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")
    rows = read_csv(args.factors)
    quality_factors = configured_factors(config, "quality", DEFAULT_QUALITY_FACTORS)
    value_factors = configured_factors(config, "value", DEFAULT_VALUE_FACTORS)
    momentum_factors = configured_factors(config, "momentum", DEFAULT_MOMENTUM_FACTORS)
    raw_factors = list(dict.fromkeys([*quality_factors, *value_factors, *momentum_factors]))

    lower_pct = float(config["factors"]["winsorize"].get("lower_pct", 1))
    upper_pct = float(config["factors"]["winsorize"].get("upper_pct", 99))
    factor_stats: dict[str, dict[str, float]] = {}
    zscores_by_code: dict[str, dict[str, float | None]] = {row["code"]: {} for row in rows}

    for factor in raw_factors:
        values = [parse_float(row.get(factor)) for row in rows]
        clean = [value for value in values if value is not None]
        lower = percentile(clean, lower_pct) if clean else math.nan
        upper = percentile(clean, upper_pct) if clean else math.nan
        clipped = [winsorize(value, lower, upper) for value in values]
        clean_clipped = [value for value in clipped if value is not None]
        center = mean(clean_clipped) if clean_clipped else math.nan
        scale = pstdev(clean_clipped) if len(clean_clipped) > 1 else 0.0
        factor_stats[factor] = {"lower": lower, "upper": upper, "mean": center, "std": scale}
        for row, value in zip(rows, clipped):
            if value is None or not scale:
                zscores_by_code[row["code"]][factor] = None
            else:
                zscores_by_code[row["code"]][factor] = (value - center) / scale

    quality_weight = float(config["factors"]["quality"].get("weight", 0.4))
    value_weight = float(config["factors"]["value"].get("weight", 0.4))
    momentum_weight = float(config["factors"]["momentum"].get("weight", 0.2))
    score_rows: list[dict[str, Any]] = []

    for row in rows:
        code = row["code"]
        z = zscores_by_code[code]
        quality = average_available([z.get(factor) for factor in quality_factors])
        value = average_available([z.get(factor) for factor in value_factors])
        momentum = average_available([z.get(factor) for factor in momentum_factors])
        missing_score_components = [
            name
            for name, value_item in [
                ("quality_score", quality),
                ("value_score", value),
                ("momentum_score", momentum),
            ]
            if value_item is None
        ]
        qvm_score = None
        if not missing_score_components:
            qvm_score = quality_weight * quality + value_weight * value + momentum_weight * momentum
        score_rows.append(
            {
                "rebalance_date": row.get("rebalance_date", args.rebalance_date),
                "code": code,
                "name": row.get("name", ""),
                "sector": row.get("sector", ""),
                "latest_unadjusted_close": row.get("latest_unadjusted_close", ""),
                "quality_score": quality,
                "value_score": value,
                "momentum_score": momentum,
                "qvm_score": qvm_score,
                "missing_score_components": ";".join(missing_score_components),
                **{f"{factor}_z": z.get(factor) for factor in raw_factors},
            }
        )

    ranked = sorted(
        [row for row in score_rows if row["qvm_score"] is not None],
        key=lambda row: row["qvm_score"],
        reverse=True,
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    for row in score_rows:
        row.setdefault("rank", "")

    output_path = args.out_dir / f"scores_{month_key(rebalance_date)}.csv"
    fieldnames = [
        "rebalance_date",
        "rank",
        "code",
        "name",
        "sector",
        "latest_unadjusted_close",
        "quality_score",
        "value_score",
        "momentum_score",
        "qvm_score",
        "missing_score_components",
        *[f"{factor}_z" for factor in raw_factors],
    ]
    write_csv(output_path, [{key: fmt(value) for key, value in row.items()} for row in score_rows], fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_scores",
            file_path=output_path,
            vendor="local",
            schema_version="scores_v0_1",
            date_range=args.rebalance_date,
            notes=f"{len(score_rows)} rows",
        )
    print(f"Wrote {len(score_rows)} score rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
