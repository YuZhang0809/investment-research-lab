from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from group_beta_common import fmt, load_dates, normalize_text, parse_optional_date
from research_common import append_manifest, date_range_from_rows, parse_float, read_table, write_table


FIELDNAMES = [
    "rebalance_date",
    "group_type",
    "group_id",
    "group_name",
    "benchmark_weight",
    "active_weight",
    "target_weight",
    "current_weight",
    "trade_weight",
    "score",
    "constraint_status",
    "constraint_reasons",
]

MODES = {"score_tilt", "top_n_equal", "inverse_volatility"}
MISSING_SCORE_POLICIES = {"exclude", "zero"}
EPSILON = 1e-10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build generic benchmark-relative group allocation panels.")
    parser.add_argument("--group-signals", required=True, type=Path)
    parser.add_argument("--score-field", default="score")
    parser.add_argument("--benchmark-weights", type=Path)
    parser.add_argument("--current-weights", type=Path)
    parser.add_argument("--rebalance-dates", type=Path)
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values")
    parser.add_argument("--mode", choices=sorted(MODES), default="score_tilt")
    parser.add_argument("--vol-field", default="group_vol_6p")
    parser.add_argument("--top-n", type=int)
    parser.add_argument("--active-budget", type=float, default=0.10)
    parser.add_argument("--min-group-weight", type=float, default=0.0)
    parser.add_argument("--max-group-weight", type=float)
    parser.add_argument("--max-active-weight", type=float)
    parser.add_argument("--max-total-active-weight", type=float)
    parser.add_argument("--max-turnover", type=float)
    parser.add_argument("--cash-weight", type=float, default=0.0)
    parser.add_argument("--group-type-cap", action="append", default=[], help="GROUP_TYPE=MAX_WEIGHT; can be repeated.")
    parser.add_argument("--missing-score-policy", choices=sorted(MISSING_SCORE_POLICIES), default="exclude")
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-label", default="group_allocation")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def group_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(row.get("rebalance_date") or row.get("date"), "group_allocation.rebalance_date")


def group_key(row: dict[str, Any]) -> tuple[str, str]:
    return normalize_text(row.get("group_type")), normalize_text(row.get("group_id"))


def parse_weight(row: dict[str, Any], fields: list[str]) -> float | None:
    for field in fields:
        if field in row and normalize_text(row.get(field)) != "":
            return parse_float(row.get(field))
    return None


def load_signal_rows(path: Path, input_format: str) -> dict[date, dict[tuple[str, str], dict[str, Any]]]:
    grouped: dict[date, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        row_date = group_date(row)
        key = group_key(row)
        if row_date is None or not key[0] or not key[1]:
            continue
        if key in grouped[row_date]:
            raise ValueError(f"Duplicate group signal row for date={row_date};group_type={key[0]};group_id={key[1]}.")
        grouped[row_date][key] = dict(row)
    if not grouped:
        raise ValueError("Group signal panel has no valid group rows.")
    return dict(grouped)


def load_weight_rows(path: Path | None, input_format: str, fields: list[str]) -> dict[date, dict[tuple[str, str], float]]:
    if path is None:
        return {}
    grouped: dict[date, dict[tuple[str, str], float]] = defaultdict(dict)
    for row in read_table(path, format=input_format).to_dict(orient="records"):
        row_date = group_date(row)
        key = group_key(row)
        if row_date is None or not key[0] or not key[1]:
            continue
        value = parse_weight(row, fields)
        if value is None:
            continue
        if value < 0:
            raise ValueError(f"Group weight must be non-negative for date={row_date};group_type={key[0]};group_id={key[1]}.")
        if key in grouped[row_date]:
            raise ValueError(f"Duplicate group weight row for date={row_date};group_type={key[0]};group_id={key[1]}.")
        grouped[row_date][key] = value
    return dict(grouped)


def latest_weights(weight_rows: dict[date, dict[tuple[str, str], float]], target_date: date) -> dict[tuple[str, str], float]:
    dates = [row_date for row_date in weight_rows if row_date <= target_date]
    if not dates:
        return {}
    return dict(weight_rows[max(dates)])


def normalize_to_total(weights: dict[tuple[str, str], float], target_total: float) -> dict[tuple[str, str], float]:
    total = sum(value for value in weights.values() if value > 0)
    if total <= 0:
        return {key: 0.0 for key in weights}
    return {key: max(value, 0.0) / total * target_total for key, value in weights.items()}


def parse_group_type_caps(values: list[str]) -> dict[str, float]:
    caps: dict[str, float] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--group-type-cap must use GROUP_TYPE=MAX_WEIGHT.")
        group_type, raw_cap = value.split("=", 1)
        cap = parse_float(raw_cap)
        if cap is None or cap < 0:
            raise ValueError(f"Invalid group type cap: {value}")
        caps[normalize_text(group_type)] = cap
    return caps


def score_for(row: dict[str, Any] | None, field: str, missing_policy: str) -> tuple[float | None, list[str]]:
    if row is None:
        return (0.0 if missing_policy == "zero" else None), ["missing_signal_row"]
    value = parse_float(row.get(field))
    if value is None:
        return (0.0 if missing_policy == "zero" else None), ["missing_score"]
    return value, []


def apply_simple_caps(
    target: dict[tuple[str, str], float],
    benchmark: dict[tuple[str, str], float],
    reasons: dict[tuple[str, str], list[str]],
    *,
    min_group_weight: float,
    max_group_weight: float | None,
    max_active_weight: float | None,
) -> dict[tuple[str, str], float]:
    capped: dict[tuple[str, str], float] = {}
    for key, value in target.items():
        new_value = max(value, 0.0)
        if max_active_weight is not None:
            base = benchmark.get(key, 0.0)
            active = new_value - base
            clipped_active = max(-max_active_weight, min(max_active_weight, active))
            if clipped_active != active:
                new_value = max(base + clipped_active, 0.0)
                reasons[key].append("max_active_weight")
        if new_value > 0 and min_group_weight > 0 and new_value < min_group_weight:
            new_value = min_group_weight
            reasons[key].append("min_group_weight")
        if max_group_weight is not None and new_value > max_group_weight:
            new_value = max_group_weight
            reasons[key].append("max_group_weight")
        capped[key] = new_value
    return capped


def apply_total_active_cap(
    target: dict[tuple[str, str], float],
    benchmark: dict[tuple[str, str], float],
    reasons: dict[tuple[str, str], list[str]],
    cap: float | None,
) -> dict[tuple[str, str], float]:
    if cap is None:
        return target
    keys = set(target) | set(benchmark)
    active_sum = sum(abs(target.get(key, 0.0) - benchmark.get(key, 0.0)) for key in keys)
    if active_sum <= cap or active_sum <= 0:
        return target
    scale = cap / active_sum
    output: dict[tuple[str, str], float] = {}
    for key in keys:
        old_value = target.get(key, 0.0)
        new_value = max(benchmark.get(key, 0.0) + (old_value - benchmark.get(key, 0.0)) * scale, 0.0)
        output[key] = new_value
        if abs(new_value - old_value) > EPSILON:
            reasons[key].append("max_total_active_weight")
    return output


def apply_group_type_caps(
    target: dict[tuple[str, str], float],
    reasons: dict[tuple[str, str], list[str]],
    caps: dict[str, float],
) -> dict[tuple[str, str], float]:
    if not caps:
        return target
    totals: dict[str, float] = defaultdict(float)
    for key, value in target.items():
        totals[key[0]] += max(value, 0.0)
    output = dict(target)
    for group_type, cap in caps.items():
        total = totals.get(group_type, 0.0)
        if total <= cap or total <= 0:
            continue
        scale = cap / total
        for key, value in list(output.items()):
            if key[0] == group_type:
                new_value = value * scale
                output[key] = new_value
                if abs(new_value - value) > EPSILON:
                    reasons[key].append("group_type_cap")
    return output


def apply_turnover_cap(
    target: dict[tuple[str, str], float],
    current: dict[tuple[str, str], float],
    reasons: dict[tuple[str, str], list[str]],
    cap: float | None,
) -> dict[tuple[str, str], float]:
    if cap is None:
        return target
    keys = set(target) | set(current)
    turnover = 0.5 * sum(abs(target.get(key, 0.0) - current.get(key, 0.0)) for key in keys)
    if turnover <= cap or turnover <= 0:
        return target
    scale = cap / turnover
    output: dict[tuple[str, str], float] = {}
    for key in keys:
        old_value = target.get(key, 0.0)
        new_value = max(current.get(key, 0.0) + (old_value - current.get(key, 0.0)) * scale, 0.0)
        output[key] = new_value
        if abs(new_value - old_value) > EPSILON:
            reasons[key].append("max_turnover")
    return output


def validate_final_constraints(
    target: dict[tuple[str, str], float],
    benchmark: dict[tuple[str, str], float],
    current: dict[tuple[str, str], float],
    reasons: dict[tuple[str, str], list[str]],
    *,
    min_group_weight: float,
    max_group_weight: float | None,
    max_active_weight: float | None,
    max_total_active_weight: float | None,
    max_turnover: float | None,
    group_type_caps: dict[str, float],
) -> None:
    keys = set(target) | set(benchmark) | set(current)
    for key in keys:
        value = target.get(key, 0.0)
        if value > 0 and min_group_weight > 0 and value < min_group_weight - EPSILON:
            reasons[key].append("final_min_group_weight_violation")
        if max_group_weight is not None and value > max_group_weight + EPSILON:
            reasons[key].append("final_max_group_weight_violation")
        if max_active_weight is not None and abs(value - benchmark.get(key, 0.0)) > max_active_weight + EPSILON:
            reasons[key].append("final_max_active_weight_violation")

    if max_total_active_weight is not None:
        total_active = sum(abs(target.get(key, 0.0) - benchmark.get(key, 0.0)) for key in keys)
        if total_active > max_total_active_weight + EPSILON:
            for key in keys:
                if abs(target.get(key, 0.0) - benchmark.get(key, 0.0)) > EPSILON:
                    reasons[key].append("final_max_total_active_weight_violation")

    if max_turnover is not None:
        turnover = 0.5 * sum(abs(target.get(key, 0.0) - current.get(key, 0.0)) for key in keys)
        if turnover > max_turnover + EPSILON:
            for key in keys:
                if abs(target.get(key, 0.0) - current.get(key, 0.0)) > EPSILON:
                    reasons[key].append("final_max_turnover_violation")

    if group_type_caps:
        totals: dict[str, float] = defaultdict(float)
        for key, value in target.items():
            totals[key[0]] += max(value, 0.0)
        for group_type, cap in group_type_caps.items():
            if totals.get(group_type, 0.0) > cap + EPSILON:
                for key, value in target.items():
                    if key[0] == group_type and value > EPSILON:
                        reasons[key].append("final_group_type_cap_violation")


def score_tilt_targets(
    keys: list[tuple[str, str]],
    signal_rows: dict[tuple[str, str], dict[str, Any]],
    benchmark: dict[tuple[str, str], float],
    *,
    score_field: str,
    active_budget: float,
    missing_score_policy: str,
    reasons: dict[tuple[str, str], list[str]],
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float | None]]:
    scores: dict[tuple[str, str], float | None] = {}
    clean_scores: list[tuple[tuple[str, str], float]] = []
    for key in keys:
        score, score_reasons = score_for(signal_rows.get(key), score_field, missing_score_policy)
        scores[key] = score
        reasons[key].extend(score_reasons)
        if score is None:
            reasons[key].append("excluded_missing_score")
        else:
            clean_scores.append((key, score))
    target = dict(benchmark)
    if len(clean_scores) <= 1:
        return target, scores
    mean_score = sum(score for _key, score in clean_scores) / len(clean_scores)
    centered = {key: score - mean_score for key, score in clean_scores}
    denominator = sum(abs(value) for value in centered.values())
    if denominator <= 0:
        return target, scores
    for key, value in centered.items():
        target[key] = benchmark.get(key, 0.0) + active_budget * value / denominator
    return target, scores


def top_n_equal_targets(
    keys: list[tuple[str, str]],
    signal_rows: dict[tuple[str, str], dict[str, Any]],
    target_total: float,
    *,
    score_field: str,
    top_n: int | None,
    missing_score_policy: str,
    reasons: dict[tuple[str, str], list[str]],
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float | None]]:
    scored: list[tuple[tuple[str, str], float]] = []
    scores: dict[tuple[str, str], float | None] = {}
    for key in keys:
        score, score_reasons = score_for(signal_rows.get(key), score_field, missing_score_policy)
        scores[key] = score
        reasons[key].extend(score_reasons)
        if score is None:
            reasons[key].append("excluded_missing_score")
        else:
            scored.append((key, score))
    scored.sort(key=lambda item: (-item[1], item[0][0], item[0][1]))
    selected = [key for key, _score in (scored[:top_n] if top_n is not None and top_n > 0 else scored)]
    target = {key: 0.0 for key in keys}
    if selected:
        weight = target_total / len(selected)
        for key in selected:
            target[key] = weight
    for key in keys:
        if key not in selected:
            reasons[key].append("top_n_excluded")
    return target, scores


def inverse_volatility_targets(
    keys: list[tuple[str, str]],
    signal_rows: dict[tuple[str, str], dict[str, Any]],
    target_total: float,
    *,
    score_field: str,
    vol_field: str,
    top_n: int | None,
    missing_score_policy: str,
    reasons: dict[tuple[str, str], list[str]],
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float | None]]:
    scores: dict[tuple[str, str], float | None] = {}
    eligible: list[tuple[tuple[str, str], float]] = []
    for key in keys:
        score, score_reasons = score_for(signal_rows.get(key), score_field, missing_score_policy)
        scores[key] = score
        reasons[key].extend(score_reasons)
        if score is None:
            reasons[key].append("excluded_missing_score")
            continue
        eligible.append((key, score))
    eligible.sort(key=lambda item: (-item[1], item[0][0], item[0][1]))
    selected = [key for key, _score in (eligible[:top_n] if top_n is not None and top_n > 0 else eligible)]
    raw: dict[tuple[str, str], float] = {}
    for key in selected:
        vol = parse_float(signal_rows.get(key, {}).get(vol_field))
        if vol is None or vol <= 0:
            reasons[key].append("missing_volatility")
            continue
        raw[key] = 1.0 / vol
    target = {key: 0.0 for key in keys}
    target.update(normalize_to_total(raw, target_total))
    for key in keys:
        if key not in selected:
            reasons[key].append("top_n_excluded")
    return target, scores


def build_panel(
    signal_rows_by_date: dict[date, dict[tuple[str, str], dict[str, Any]]],
    *,
    benchmark_weights_by_date: dict[date, dict[tuple[str, str], float]] | None = None,
    current_weights_by_date: dict[date, dict[tuple[str, str], float]] | None = None,
    rebalance_dates: list[date] | None = None,
    score_field: str = "score",
    mode: str = "score_tilt",
    vol_field: str = "group_vol_6p",
    top_n: int | None = None,
    active_budget: float = 0.10,
    min_group_weight: float = 0.0,
    max_group_weight: float | None = None,
    max_active_weight: float | None = None,
    max_total_active_weight: float | None = None,
    max_turnover: float | None = None,
    cash_weight: float = 0.0,
    group_type_caps: dict[str, float] | None = None,
    missing_score_policy: str = "exclude",
) -> list[dict[str, Any]]:
    if mode not in MODES:
        raise ValueError(f"Unsupported allocation mode: {mode}")
    if missing_score_policy not in MISSING_SCORE_POLICIES:
        raise ValueError(f"Unsupported missing score policy: {missing_score_policy}")
    if active_budget < 0 or cash_weight < 0 or cash_weight >= 1:
        raise ValueError("active_budget must be non-negative and cash_weight must be in [0, 1).")
    if top_n is not None and top_n <= 0:
        raise ValueError("--top-n must be positive when supplied.")

    benchmark_weights_by_date = benchmark_weights_by_date or {}
    current_weights_by_date = current_weights_by_date or {}
    group_type_caps = group_type_caps or {}
    target_total = 1.0 - cash_weight
    output: list[dict[str, Any]] = []
    previous_target: dict[tuple[str, str], float] = {}
    dates = sorted(rebalance_dates or signal_rows_by_date.keys())
    for rebalance_date in dates:
        signal_rows = signal_rows_by_date.get(rebalance_date, {})
        explicit_benchmark = latest_weights(benchmark_weights_by_date, rebalance_date)
        benchmark_keys = set(explicit_benchmark)
        signal_keys = set(signal_rows)
        keys = sorted(signal_keys | benchmark_keys | set(previous_target))
        if not keys:
            continue
        benchmark = normalize_to_total(explicit_benchmark, target_total) if explicit_benchmark else {
            key: target_total / len(keys) for key in keys
        }
        for key in keys:
            benchmark.setdefault(key, 0.0)
        current = latest_weights(current_weights_by_date, rebalance_date) or previous_target or benchmark
        for key in keys:
            current.setdefault(key, 0.0)
        reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
        if mode == "score_tilt":
            target, scores = score_tilt_targets(
                keys,
                signal_rows,
                benchmark,
                score_field=score_field,
                active_budget=active_budget,
                missing_score_policy=missing_score_policy,
                reasons=reasons,
            )
        elif mode == "top_n_equal":
            target, scores = top_n_equal_targets(
                keys,
                signal_rows,
                target_total,
                score_field=score_field,
                top_n=top_n,
                missing_score_policy=missing_score_policy,
                reasons=reasons,
            )
        else:
            target, scores = inverse_volatility_targets(
                keys,
                signal_rows,
                target_total,
                score_field=score_field,
                vol_field=vol_field,
                top_n=top_n,
                missing_score_policy=missing_score_policy,
                reasons=reasons,
            )
        for key in keys:
            target.setdefault(key, 0.0)
        target = apply_simple_caps(
            target,
            benchmark,
            reasons,
            min_group_weight=min_group_weight,
            max_group_weight=max_group_weight,
            max_active_weight=max_active_weight,
        )
        target = apply_total_active_cap(target, benchmark, reasons, max_total_active_weight)
        target = apply_group_type_caps(target, reasons, group_type_caps)
        target = apply_turnover_cap(target, current, reasons, max_turnover)
        validate_final_constraints(
            target,
            benchmark,
            current,
            reasons,
            min_group_weight=min_group_weight,
            max_group_weight=max_group_weight,
            max_active_weight=max_active_weight,
            max_total_active_weight=max_total_active_weight,
            max_turnover=max_turnover,
            group_type_caps=group_type_caps,
        )
        names = {key: normalize_text(row.get("group_name")) for key, row in signal_rows.items()}
        for key in sorted(set(keys) | set(target) | set(current) | set(benchmark)):
            reason_values = [value for value in dict.fromkeys(reasons.get(key, [])) if value]
            has_final_violation = any(value.startswith("final_") and value.endswith("_violation") for value in reason_values)
            row = {
                "rebalance_date": rebalance_date,
                "group_type": key[0],
                "group_id": key[1],
                "group_name": names.get(key, ""),
                "benchmark_weight": benchmark.get(key, 0.0),
                "active_weight": target.get(key, 0.0) - benchmark.get(key, 0.0),
                "target_weight": target.get(key, 0.0),
                "current_weight": current.get(key, 0.0),
                "trade_weight": target.get(key, 0.0) - current.get(key, 0.0),
                "score": scores.get(key),
                "constraint_status": "violation" if has_final_violation else ("clipped" if reason_values else "ok"),
                "constraint_reasons": ";".join(reason_values),
            }
            if (
                not has_final_violation
                and row["target_weight"] == 0
                and any(value.startswith("excluded") or value == "top_n_excluded" for value in reason_values)
            ):
                row["constraint_status"] = "excluded"
            output.append(row)
        previous_target = {key: target.get(key, 0.0) for key in set(keys) | set(target)}
    return output


def main() -> int:
    args = build_parser().parse_args()
    signal_rows = load_signal_rows(args.group_signals, args.input_format)
    rows = build_panel(
        signal_rows,
        benchmark_weights_by_date=load_weight_rows(args.benchmark_weights, args.input_format, ["benchmark_weight", "weight", "target_weight"]),
        current_weights_by_date=load_weight_rows(args.current_weights, args.input_format, ["current_weight", "target_weight", "weight"]),
        rebalance_dates=load_dates(args.rebalance_dates, args.rebalance_date_values, field_name="rebalance_date"),
        score_field=args.score_field,
        mode=args.mode,
        vol_field=args.vol_field,
        top_n=args.top_n,
        active_budget=args.active_budget,
        min_group_weight=args.min_group_weight,
        max_group_weight=args.max_group_weight,
        max_active_weight=args.max_active_weight,
        max_total_active_weight=args.max_total_active_weight,
        max_turnover=args.max_turnover,
        cash_weight=args.cash_weight,
        group_type_caps=parse_group_type_caps(args.group_type_cap),
        missing_score_policy=args.missing_score_policy,
    )
    serializable = [{key: fmt(value) for key, value in row.items()} for row in rows]
    write_table(serializable, args.out, format=args.output_format, fieldnames=FIELDNAMES)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_group_allocation",
            file_path=args.out,
            vendor="internal",
            schema_version="group_allocation_v0_1",
            date_range=date_range_from_rows(serializable, "rebalance_date"),
            notes=f"run_label={args.run_label};rows={len(rows)};mode={args.mode}",
        )
    print(f"Wrote {len(rows)} group allocation rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
