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
FACTOR_GROUPS = ["quality", "value", "momentum"]
GROUP_SCORE_FIELDS = {
    "quality": "quality_score",
    "value": "value_score",
    "momentum": "momentum_score",
}
STRATEGY_VERSION_CHOICES = [
    "value_only",
    "qv",
    "qvm",
    "value_dominant_quality_filter_momentum_exclusion",
    "weighted_groups",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build QVM z-scores and ranks from factor CSV.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--rebalance-date", required=True)
    parser.add_argument("--factors", required=True, type=Path)
    parser.add_argument("--strategy-version", choices=STRATEGY_VERSION_CHOICES, default="qvm")
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


def strategy_factor_groups(
    config: dict[str, Any],
    strategy_version: str,
) -> tuple[list[str], list[str], list[str]]:
    quality_factors = configured_factors(config, "quality", DEFAULT_QUALITY_FACTORS)
    value_factors = configured_factors(config, "value", DEFAULT_VALUE_FACTORS)
    momentum_factors = configured_factors(config, "momentum", DEFAULT_MOMENTUM_FACTORS)

    if strategy_version == "value_only":
        return [], value_factors, []
    if strategy_version == "qv":
        return quality_factors, value_factors, []
    if strategy_version == "weighted_groups":
        return quality_factors, value_factors, momentum_factors
    return quality_factors, value_factors, momentum_factors


def configured_group_weights(config: dict[str, Any]) -> dict[str, float]:
    scoring = config.get("strategy", {}).get("scoring", {}) or {}
    mode = str(scoring.get("mode", "")).strip()
    if mode != "weighted_groups":
        raise ValueError("strategy.scoring.mode must be weighted_groups for strategy-version weighted_groups.")
    raw_weights = scoring.get("weights", {}) or {}
    unknown = sorted(set(raw_weights) - set(FACTOR_GROUPS))
    if unknown:
        raise ValueError(f"Unknown score group(s): {', '.join(unknown)}")
    weights: dict[str, float] = {}
    for group in FACTOR_GROUPS:
        value = float(raw_weights.get(group, 0.0) or 0.0)
        if value < 0:
            raise ValueError(f"Score weight for {group} must be non-negative.")
        weights[group] = value
    if not any(value > 0 for value in weights.values()):
        raise ValueError("At least one strategy.scoring weight must be greater than zero.")
    return weights


def configured_filters(config: dict[str, Any]) -> list[dict[str, Any]]:
    values = config.get("strategy", {}).get("filters", []) or []
    filters: list[dict[str, Any]] = []
    for index, item in enumerate(values, start=1):
        group = str((item or {}).get("group", "")).strip()
        rule = str((item or {}).get("rule", "")).strip()
        if group not in FACTOR_GROUPS:
            raise ValueError(f"Unknown filter group in strategy.filters[{index}]: {group}")
        if rule != "exclude_bottom_pct":
            raise ValueError(f"Unsupported filter rule in strategy.filters[{index}]: {rule}")
        pct = float((item or {}).get("pct", 0.0) or 0.0)
        if pct < 0 or pct > 100:
            raise ValueError(f"Filter pct must be between 0 and 100 in strategy.filters[{index}].")
        filters.append({"group": group, "rule": rule, "pct": pct})
    return filters


def append_reason(current: str, reason: str) -> str:
    if not current:
        return reason
    values = current.split(";")
    if reason in values:
        return current
    return f"{current};{reason}"


def weighted_group_score(
    group_scores: dict[str, float | None],
    weights: dict[str, float],
) -> tuple[float | None, list[str]]:
    missing = [
        GROUP_SCORE_FIELDS[group]
        for group, weight in weights.items()
        if weight > 0 and group_scores.get(group) is None
    ]
    if missing:
        return None, missing
    score = sum(weights[group] * (group_scores.get(group) or 0.0) for group in FACTOR_GROUPS)
    return score, []


def apply_tail_filters(score_rows: list[dict[str, Any]], filters: list[dict[str, Any]]) -> None:
    for filter_config in filters:
        group = filter_config["group"]
        pct = float(filter_config["pct"])
        field = GROUP_SCORE_FIELDS[group]
        missing_reason = field
        for row in score_rows:
            if row.get("filter_status") == "pass" and row.get(field) is None:
                row["filter_status"] = "missing_required_score"
                row["filter_reasons"] = append_reason(str(row.get("filter_reasons", "")), missing_reason)
                row["missing_score_components"] = append_reason(
                    str(row.get("missing_score_components", "")),
                    missing_reason,
                )

        eligible = [
            row
            for row in score_rows
            if row.get("filter_status") == "pass" and row.get(field) is not None
        ]
        exclude_count = math.ceil(len(eligible) * pct / 100.0)
        if exclude_count <= 0:
            continue
        reason = f"{group}_bottom_{pct:g}pct"
        for row in sorted(eligible, key=lambda item: (float(item[field]), str(item.get("code", ""))))[:exclude_count]:
            row["filter_status"] = "filtered"
            row["filter_reasons"] = append_reason(str(row.get("filter_reasons", "")), reason)


def strategy_score(
    strategy_version: str,
    *,
    quality: float | None,
    value: float | None,
    momentum: float | None,
    quality_weight: float,
    value_weight: float,
    momentum_weight: float,
) -> tuple[float | None, list[str]]:
    missing: list[str] = []
    if strategy_version == "value_only":
        if value is None:
            missing.append("value_score")
            return None, missing
        return value, missing

    if strategy_version == "qv":
        if quality is None:
            missing.append("quality_score")
        if value is None:
            missing.append("value_score")
        if missing:
            return None, missing
        return 0.5 * (quality or 0.0) + 0.5 * (value or 0.0), missing

    if strategy_version == "value_dominant_quality_filter_momentum_exclusion":
        if value is None:
            missing.append("value_score")
        if quality is None:
            missing.append("quality_score")
        if momentum is not None and momentum < 0:
            missing.append("momentum_exclusion")
        if missing:
            return None, missing
        return 0.7 * (value or 0.0) + 0.3 * (quality or 0.0), missing

    for name, value_item in [
        ("quality_score", quality),
        ("value_score", value),
        ("momentum_score", momentum),
    ]:
        if value_item is None:
            missing.append(name)
    if missing:
        return None, missing
    return (
        quality_weight * (quality or 0.0)
        + value_weight * (value or 0.0)
        + momentum_weight * (momentum or 0.0)
    ), missing


def build_scores(
    *,
    config: dict[str, Any],
    factor_rows: list[dict[str, str]],
    strategy_version: str = "qvm",
) -> tuple[list[dict[str, Any]], list[str]]:
    quality_factors, value_factors, momentum_factors = strategy_factor_groups(config, strategy_version)
    rows = factor_rows
    raw_factors = list(dict.fromkeys([*quality_factors, *value_factors, *momentum_factors]))
    weighted_mode = strategy_version == "weighted_groups"
    group_weights = configured_group_weights(config) if weighted_mode else {}
    filters = configured_filters(config) if weighted_mode else []

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
            if value is None:
                zscores_by_code[row["code"]][factor] = None
            elif not scale:
                zscores_by_code[row["code"]][factor] = 0.0
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
        if weighted_mode:
            composite_score, missing_score_components = weighted_group_score(
                {"quality": quality, "value": value, "momentum": momentum},
                group_weights,
            )
            qvm_score = composite_score
        else:
            qvm_score, missing_score_components = strategy_score(
                strategy_version,
                quality=quality,
                value=value,
                momentum=momentum,
                quality_weight=quality_weight,
                value_weight=value_weight,
                momentum_weight=momentum_weight,
            )
            composite_score = qvm_score
        score_rows.append(
            {
                "rebalance_date": row.get("rebalance_date", ""),
                "code": code,
                "name": row.get("name", ""),
                "sector": row.get("sector", ""),
                "latest_unadjusted_close": row.get("latest_unadjusted_close", ""),
                "quality_score": quality,
                "value_score": value,
                "momentum_score": momentum,
                "composite_score": composite_score,
                "qvm_score": qvm_score,
                "filter_status": "pass" if composite_score is not None else "missing_required_score",
                "filter_reasons": "",
                "missing_score_components": ";".join(missing_score_components),
                **{f"{factor}_z": z.get(factor) for factor in raw_factors},
            }
        )

    if weighted_mode and filters:
        apply_tail_filters(score_rows, filters)

    ranked = sorted(
        [
            row
            for row in score_rows
            if row["composite_score"] is not None and row.get("filter_status") == "pass"
        ],
        key=lambda row: (-float(row["composite_score"]), str(row.get("code", ""))),
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    for row in score_rows:
        row.setdefault("rank", "")

    return score_rows, raw_factors


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    rebalance_date = parse_date(args.rebalance_date, field_name="rebalance_date")
    if rebalance_date is None:
        raise ValueError("rebalance_date is required")
    score_rows, raw_factors = build_scores(
        config=config,
        factor_rows=read_csv(args.factors),
        strategy_version=args.strategy_version,
    )
    for row in score_rows:
        row["rebalance_date"] = row.get("rebalance_date") or args.rebalance_date

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
        "composite_score",
        "qvm_score",
        "filter_status",
        "filter_reasons",
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
