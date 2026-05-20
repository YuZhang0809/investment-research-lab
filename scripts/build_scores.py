from __future__ import annotations

import argparse
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from factor_expressions import factor_definition_names_for_group
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
GROUP_RELATIVE_METHODS = {"zscore", "rank_pct"}
STRATEGY_VERSION_CHOICES = [
    "value_only",
    "qv",
    "qvm",
    "value_dominant_quality_filter_momentum_exclusion",
    "weighted_groups",
    "configurable",
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
    factor_config = config.get("factors", {}) or {}
    values = factor_config.get(group, {}).get("variables")
    configured = [str(value) for value in values] if values is not None else list(defaults)
    for name in factor_definition_names_for_group(config, group):
        if name not in configured:
            configured.append(name)
    return configured


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
    if strategy_version in {"weighted_groups", "configurable"}:
        return quality_factors, value_factors, momentum_factors
    return quality_factors, value_factors, momentum_factors


def configured_scoring_mode(config: dict[str, Any], strategy_version: str) -> str:
    if strategy_version == "configurable":
        mode = str((config.get("strategy", {}).get("scoring", {}) or {}).get("mode", "")).strip()
        if not mode:
            raise ValueError("strategy.scoring.mode is required for strategy-version configurable.")
        if mode not in {"weighted_groups", "weighted_factors"}:
            raise ValueError(f"Unsupported strategy.scoring.mode for configurable: {mode}")
        return mode
    if strategy_version == "weighted_groups":
        return "weighted_groups"
    return "legacy"


def configured_group_weights(config: dict[str, Any]) -> dict[str, float]:
    scoring = config.get("strategy", {}).get("scoring", {}) or {}
    mode = str(scoring.get("mode", "")).strip()
    if mode != "weighted_groups":
        raise ValueError("strategy.scoring.mode must be weighted_groups for group-weight scoring.")
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


def configured_factor_weights(config: dict[str, Any]) -> dict[str, float]:
    scoring = config.get("strategy", {}).get("scoring", {}) or {}
    mode = str(scoring.get("mode", "")).strip()
    if mode != "weighted_factors":
        raise ValueError("strategy.scoring.mode must be weighted_factors for factor-weight scoring.")
    raw_weights = scoring.get("weights", {}) or {}
    weights: dict[str, float] = {}
    for name, raw_value in raw_weights.items():
        factor = str(name).strip()
        if not factor:
            raise ValueError("Factor weight names must be non-empty.")
        value = float(raw_value or 0.0)
        if value < 0:
            raise ValueError(f"Score weight for {factor} must be non-negative.")
        weights[factor] = value
    if not any(value > 0 for value in weights.values()):
        raise ValueError("At least one strategy.scoring factor weight must be greater than zero.")
    return weights


def score_output_field(factor: str, *, direct_fields: set[str] | None = None) -> str:
    return factor if factor in (direct_fields or set()) else f"{factor}_z"


def list_config_values(value: Any, *, field_name: str) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError(f"{field_name} must be a list.")
    normalized = [str(item).strip() for item in values if str(item).strip()]
    if not normalized:
        raise ValueError(f"{field_name} must contain at least one value.")
    return normalized


def configured_group_relative_transforms(config: dict[str, Any]) -> list[dict[str, Any]]:
    values = (config.get("strategy", {}) or {}).get("group_relative_transforms", []) or []
    if not isinstance(values, list):
        raise ValueError("strategy.group_relative_transforms must be a list.")
    transforms: list[dict[str, Any]] = []
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"strategy.group_relative_transforms[{index}] must be a mapping.")
        group_field = str(item.get("group_field", "") or "").strip()
        if not group_field:
            raise ValueError(f"strategy.group_relative_transforms[{index}].group_field is required.")
        fields = list_config_values(item.get("fields"), field_name=f"strategy.group_relative_transforms[{index}].fields")
        methods = list_config_values(item.get("methods"), field_name=f"strategy.group_relative_transforms[{index}].methods")
        unknown_methods = sorted(set(methods) - GROUP_RELATIVE_METHODS)
        if unknown_methods:
            raise ValueError(
                f"Unsupported group_relative_transforms method(s): {', '.join(unknown_methods)}"
            )
        min_group_size = int(item.get("min_group_size", 2) or 2)
        if min_group_size < 1:
            raise ValueError(f"strategy.group_relative_transforms[{index}].min_group_size must be at least 1.")
        output_prefix = str(item.get("output_prefix", "group_relative") or "").strip()
        if not output_prefix:
            raise ValueError(f"strategy.group_relative_transforms[{index}].output_prefix is required.")
        transforms.append(
            {
                "group_field": group_field,
                "fields": fields,
                "methods": methods,
                "min_group_size": min_group_size,
                "output_prefix": output_prefix,
            }
        )
    validate_unique_group_relative_outputs(transforms)
    return transforms


def group_relative_output_field(output_prefix: str, field: str, method: str) -> str:
    suffix = "z" if method == "zscore" else "rank_pct"
    return f"{output_prefix}_{field}_{suffix}"


def group_relative_output_fields(transforms: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for transform in transforms:
        output_prefix = str(transform["output_prefix"])
        for field in transform["fields"]:
            for method in transform["methods"]:
                output_field = group_relative_output_field(output_prefix, field, method)
                if output_field not in fields:
                    fields.append(output_field)
    return fields


def validate_unique_group_relative_outputs(transforms: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    for index, transform in enumerate(transforms, start=1):
        output_prefix = str(transform["output_prefix"])
        for field in transform["fields"]:
            for method in transform["methods"]:
                output_field = group_relative_output_field(output_prefix, field, method)
                source = f"strategy.group_relative_transforms[{index}]"
                if output_field in seen:
                    raise ValueError(
                        f"Duplicate group_relative_transforms output field {output_field!r} "
                        f"from {seen[output_field]} and {source}."
                    )
                seen[output_field] = source


def score_direct_fields(config: dict[str, Any]) -> set[str]:
    return set(group_relative_output_fields(configured_group_relative_transforms(config)))


def validate_group_relative_transforms(transforms: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = available_columns(rows)
    missing: list[str] = []
    for transform in transforms:
        group_field = str(transform["group_field"])
        if group_field not in columns:
            missing.append(group_field)
        for field in transform["fields"]:
            if field not in columns:
                missing.append(field)
    if missing:
        raise ValueError(f"Unknown group_relative_transforms field(s): {', '.join(sorted(set(missing)))}")


def normalized_group_value(row: dict[str, Any], field: str) -> str:
    value = str(row.get(field, "") or "").strip()
    return value or "UNKNOWN"


def rank_percentiles(values: list[tuple[dict[str, Any], float]]) -> dict[str, float]:
    ordered = sorted(values, key=lambda item: (item[1], str(item[0].get("code", ""))))
    if len(ordered) == 1:
        return {str(ordered[0][0].get("code", "")): 1.0}
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        value = ordered[index][1]
        end = index + 1
        while end < len(ordered) and ordered[end][1] == value:
            end += 1
        average_position = (index + end - 1) / 2.0
        percentile_value = average_position / (len(ordered) - 1)
        for item_index in range(index, end):
            ranks[str(ordered[item_index][0].get("code", ""))] = percentile_value
        index = end
    return ranks


def compute_group_relative_values(
    rows: list[dict[str, Any]],
    transforms: list[dict[str, Any]],
) -> dict[str, dict[str, float | None]]:
    values_by_code: dict[str, dict[str, float | None]] = {str(row["code"]): {} for row in rows}
    for transform in transforms:
        group_field = str(transform["group_field"])
        output_prefix = str(transform["output_prefix"])
        min_group_size = int(transform["min_group_size"])
        for field in transform["fields"]:
            grouped: dict[tuple[str, str], list[tuple[dict[str, Any], float]]] = {}
            for row in rows:
                value = parse_float(row.get(field))
                if value is None:
                    continue
                key = (str(row.get("rebalance_date", "")), normalized_group_value(row, group_field))
                grouped.setdefault(key, []).append((row, value))
            for _key, group_values in grouped.items():
                if len(group_values) < min_group_size:
                    continue
                clean = [value for _row, value in group_values]
                if "zscore" in transform["methods"]:
                    output_field = group_relative_output_field(output_prefix, field, "zscore")
                    center = mean(clean)
                    scale = pstdev(clean) if len(clean) > 1 else 0.0
                    if scale:
                        for row, value in group_values:
                            values_by_code[str(row["code"])][output_field] = (value - center) / scale
                if "rank_pct" in transform["methods"]:
                    output_field = group_relative_output_field(output_prefix, field, "rank_pct")
                    for code, rank_value in rank_percentiles(group_values).items():
                        values_by_code[code][output_field] = rank_value
    for row in rows:
        code = str(row["code"])
        for output_field in group_relative_output_fields(transforms):
            values_by_code[code].setdefault(output_field, None)
    return values_by_code


def configured_filters(config: dict[str, Any]) -> list[dict[str, Any]]:
    values = config.get("strategy", {}).get("filters", []) or []
    filters: list[dict[str, Any]] = []
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"strategy.filters[{index}] must be a mapping.")
        group = str((item or {}).get("group", "")).strip()
        field = str((item or {}).get("field", "")).strip()
        rule = str((item or {}).get("rule", "")).strip()
        if group and field:
            raise ValueError(f"Use either group or field in strategy.filters[{index}], not both.")
        if not group and not field:
            raise ValueError(f"strategy.filters[{index}] requires group or field.")
        if group and group not in FACTOR_GROUPS:
            raise ValueError(f"Unknown filter group in strategy.filters[{index}]: {group}")
        allowed_rules = {
            "exclude_bottom_pct",
            "exclude_top_pct",
            "exclude_below",
            "exclude_above",
            "require_not_missing",
        }
        if rule not in allowed_rules:
            raise ValueError(f"Unsupported filter rule in strategy.filters[{index}]: {rule}")
        normalized = {"group": group, "field": field, "rule": rule}
        if rule in {"exclude_bottom_pct", "exclude_top_pct"}:
            if "pct" not in item:
                raise ValueError(f"strategy.filters[{index}].pct is required for {rule}.")
            pct = float((item or {}).get("pct", 0.0) or 0.0)
            if pct <= 0 or pct > 100:
                raise ValueError(f"Filter pct must be greater than 0 and no more than 100 in strategy.filters[{index}].")
            normalized["pct"] = pct
        if rule in {"exclude_below", "exclude_above"}:
            threshold = parse_float((item or {}).get("value"))
            if threshold is None:
                raise ValueError(f"strategy.filters[{index}].value must be numeric for {rule}.")
            normalized["value"] = threshold
        filters.append(normalized)
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


def weighted_factor_score(
    zscores: dict[str, float | None],
    weights: dict[str, float],
    *,
    direct_fields: set[str] | None = None,
) -> tuple[float | None, list[str]]:
    direct_fields = direct_fields or set()
    missing = [
        factor if factor in direct_fields else f"{factor}_z"
        for factor, weight in weights.items()
        if weight > 0 and zscores.get(factor) is None
    ]
    if missing:
        return None, missing
    score = sum(weight * (zscores.get(factor) or 0.0) for factor, weight in weights.items())
    return score, []


def available_columns(rows: list[dict[str, Any]]) -> set[str]:
    columns: set[str] = set()
    for row in rows:
        columns.update(str(key) for key in row)
    return columns


def validate_weighted_factor_fields(
    weights: dict[str, float],
    rows: list[dict[str, Any]],
    *,
    extra_fields: set[str] | None = None,
) -> None:
    if not rows:
        return
    columns = available_columns(rows) | (extra_fields or set())
    unknown = sorted(factor for factor in weights if factor not in columns)
    if unknown:
        raise ValueError(f"Unknown weighted_factors field(s): {', '.join(unknown)}")


def filter_field(filter_config: dict[str, Any], row: dict[str, Any] | None = None) -> tuple[str, str]:
    group = str(filter_config.get("group", "") or "")
    if group:
        return GROUP_SCORE_FIELDS[group], group
    configured_field = str(filter_config.get("field", "") or "")
    rule = str(filter_config.get("rule", ""))
    allow_z_resolution = rule in {"exclude_bottom_pct", "exclude_top_pct", "require_not_missing"}
    if row is not None and configured_field in row:
        return configured_field, configured_field
    if row is not None and allow_z_resolution and f"{configured_field}_z" in row:
        return f"{configured_field}_z", configured_field
    if row is not None:
        raise ValueError(f"Unknown filter field: {configured_field}")
    return configured_field, configured_field


def numeric_filter_value(row: dict[str, Any], field: str) -> float | None:
    return parse_float(row.get(field))


def mark_missing_filter_value(row: dict[str, Any], field: str) -> None:
    row["filter_status"] = "missing_required_score"
    row["filter_reasons"] = append_reason(str(row.get("filter_reasons", "")), field)
    row["missing_score_components"] = append_reason(
        str(row.get("missing_score_components", "")),
        field,
    )


def apply_filters(score_rows: list[dict[str, Any]], filters: list[dict[str, Any]]) -> None:
    for filter_config in filters:
        field, label = filter_field(filter_config, score_rows[0] if score_rows else None)
        rule = str(filter_config["rule"])
        for row in score_rows:
            if row.get("filter_status") == "pass" and numeric_filter_value(row, field) is None:
                mark_missing_filter_value(row, field)

        if rule == "require_not_missing":
            continue

        if rule in {"exclude_below", "exclude_above"}:
            threshold = float(filter_config["value"])
            for row in score_rows:
                if row.get("filter_status") != "pass":
                    continue
                value = numeric_filter_value(row, field)
                if value is None:
                    continue
                if rule == "exclude_below" and value < threshold:
                    row["filter_status"] = "filtered"
                    row["filter_reasons"] = append_reason(
                        str(row.get("filter_reasons", "")),
                        f"{label}_below_{threshold:g}",
                    )
                if rule == "exclude_above" and value > threshold:
                    row["filter_status"] = "filtered"
                    row["filter_reasons"] = append_reason(
                        str(row.get("filter_reasons", "")),
                        f"{label}_above_{threshold:g}",
                    )
            continue

        eligible = [
            row
            for row in score_rows
            if row.get("filter_status") == "pass" and numeric_filter_value(row, field) is not None
        ]
        pct = float(filter_config.get("pct", 0.0))
        exclude_count = math.ceil(len(eligible) * pct / 100.0)
        if exclude_count <= 0:
            continue
        if rule == "exclude_bottom_pct":
            selected = sorted(eligible, key=lambda item: (float(item[field]), str(item.get("code", ""))))[:exclude_count]
            reason = f"{label}_bottom_{pct:g}pct"
        elif rule == "exclude_top_pct":
            selected = sorted(
                eligible,
                key=lambda item: (-float(item[field]), str(item.get("code", ""))),
            )[:exclude_count]
            reason = f"{label}_top_{pct:g}pct"
        else:
            raise ValueError(f"Unsupported filter rule: {rule}")
        for row in selected:
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
    scoring_mode = configured_scoring_mode(config, strategy_version)
    group_relative_transforms = configured_group_relative_transforms(config)
    validate_group_relative_transforms(group_relative_transforms, rows)
    group_relative_fields = group_relative_output_fields(group_relative_transforms)
    factor_weights = configured_factor_weights(config) if scoring_mode == "weighted_factors" else {}
    validate_weighted_factor_fields(factor_weights, rows, extra_fields=set(group_relative_fields))
    base_raw_factors = list(
        dict.fromkeys(
            [
                *quality_factors,
                *value_factors,
                *momentum_factors,
                *[factor for factor in factor_weights if factor not in group_relative_fields],
            ]
        )
    )
    raw_factors = list(dict.fromkeys([*base_raw_factors, *group_relative_fields]))
    group_weighted_mode = scoring_mode == "weighted_groups"
    factor_weighted_mode = scoring_mode == "weighted_factors"
    group_weights = configured_group_weights(config) if group_weighted_mode else {}
    filters = configured_filters(config) if scoring_mode in {"weighted_groups", "weighted_factors"} else []

    factor_config = config.get("factors", {}) or {}
    lower_pct = float((factor_config.get("winsorize", {}) or {}).get("lower_pct", 1))
    upper_pct = float((factor_config.get("winsorize", {}) or {}).get("upper_pct", 99))
    factor_stats: dict[str, dict[str, float]] = {}
    zscores_by_code: dict[str, dict[str, float | None]] = {row["code"]: {} for row in rows}

    for factor in base_raw_factors:
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
    group_relative_by_code = compute_group_relative_values(rows, group_relative_transforms)

    quality_weight = float((factor_config.get("quality", {}) or {}).get("weight", 0.4))
    value_weight = float((factor_config.get("value", {}) or {}).get("weight", 0.4))
    momentum_weight = float((factor_config.get("momentum", {}) or {}).get("weight", 0.2))
    score_rows: list[dict[str, Any]] = []

    for row in rows:
        code = row["code"]
        z = zscores_by_code[code]
        direct_scores = group_relative_by_code.get(code, {})
        score_components = {**z, **direct_scores}
        quality = average_available([z.get(factor) for factor in quality_factors])
        value = average_available([z.get(factor) for factor in value_factors])
        momentum = average_available([z.get(factor) for factor in momentum_factors])
        if group_weighted_mode:
            composite_score, missing_score_components = weighted_group_score(
                {"quality": quality, "value": value, "momentum": momentum},
                group_weights,
            )
            qvm_score = composite_score
        elif factor_weighted_mode:
            composite_score, missing_score_components = weighted_factor_score(
                score_components,
                factor_weights,
                direct_fields=set(group_relative_fields),
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
                **{
                    score_output_field(factor, direct_fields=set(group_relative_fields)): (
                        score_components.get(factor) if factor in group_relative_fields else z.get(factor)
                    )
                    for factor in raw_factors
                },
            }
        )

    if filters:
        apply_filters(score_rows, filters)

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
        *[score_output_field(factor, direct_fields=score_direct_fields(config)) for factor in raw_factors],
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
