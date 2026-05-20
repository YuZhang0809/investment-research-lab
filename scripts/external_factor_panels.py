from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from research_common import checksum, parse_bool, parse_date, parse_float, parse_int, read_csv


SUPPORTED_DTYPES = {"float", "int", "string", "bool"}
DUPLICATE_POLICIES = {"fail", "latest_available_date"}


@dataclass(frozen=True)
class ExternalFactorField:
    name: str
    dtype: str


@dataclass(frozen=True)
class ExternalFactorPanelConfig:
    name: str
    path: Path
    join_keys: tuple[str, ...]
    fields: tuple[ExternalFactorField, ...]
    asof_enabled: bool
    date_field: str
    max_lag_days: int | None
    duplicate_policy: str


def configured_external_factor_panels(config: dict[str, Any] | None) -> list[ExternalFactorPanelConfig]:
    values = (config or {}).get("external_factor_panels", []) or []
    if not isinstance(values, list):
        raise ValueError("external_factor_panels must be a list.")
    panels: list[ExternalFactorPanelConfig] = []
    seen_fields: dict[str, str] = {}
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"external_factor_panels[{index}] must be a mapping.")
        name = str(item.get("name", "") or "").strip()
        if not name:
            raise ValueError(f"external_factor_panels[{index}].name is required.")
        raw_path = str(item.get("path", "") or "").strip()
        if not raw_path:
            raise ValueError(f"external_factor_panels[{index}].path is required.")
        raw_join_keys = item.get("join_keys")
        if not isinstance(raw_join_keys, list) or not raw_join_keys:
            raise ValueError(f"external_factor_panels[{index}].join_keys must be a non-empty list.")
        join_keys = tuple(str(value).strip() for value in raw_join_keys if str(value).strip())
        if len(join_keys) != len(raw_join_keys):
            raise ValueError(f"external_factor_panels[{index}].join_keys cannot contain blanks.")
        raw_fields = item.get("fields")
        if not isinstance(raw_fields, list) or not raw_fields:
            raise ValueError(f"external_factor_panels[{index}].fields must be a non-empty list.")
        fields: list[ExternalFactorField] = []
        for field_index, raw_field in enumerate(raw_fields, start=1):
            if not isinstance(raw_field, dict):
                raise ValueError(f"external_factor_panels[{index}].fields[{field_index}] must be a mapping.")
            field_name = str(raw_field.get("name", "") or "").strip()
            dtype = str(raw_field.get("dtype", "string") or "string").strip().lower()
            if not field_name:
                raise ValueError(f"external_factor_panels[{index}].fields[{field_index}].name is required.")
            if dtype not in SUPPORTED_DTYPES:
                raise ValueError(f"Unsupported external factor dtype for {field_name}: {dtype}")
            if field_name in seen_fields:
                raise ValueError(
                    f"Duplicate external factor field {field_name!r} in panel {name!r} "
                    f"and panel {seen_fields[field_name]!r}."
                )
            seen_fields[field_name] = name
            fields.append(ExternalFactorField(name=field_name, dtype=dtype))
        asof = item.get("asof", {}) or {}
        if not isinstance(asof, dict):
            raise ValueError(f"external_factor_panels[{index}].asof must be a mapping.")
        asof_enabled = parse_bool(asof.get("enabled"), default=False) or False
        date_field = str(asof.get("date_field", "available_date") or "available_date").strip()
        max_lag_days = parse_int(asof.get("max_lag_days"), default=None)
        if max_lag_days is not None and max_lag_days < 0:
            raise ValueError(f"external_factor_panels[{index}].asof.max_lag_days must be non-negative.")
        duplicate_policy = str(item.get("duplicate_policy", "fail") or "fail").strip()
        if duplicate_policy not in DUPLICATE_POLICIES:
            raise ValueError(f"Unsupported external factor duplicate_policy: {duplicate_policy}")
        if duplicate_policy == "latest_available_date" and not asof_enabled:
            raise ValueError("duplicate_policy latest_available_date requires asof.enabled: true.")
        panels.append(
            ExternalFactorPanelConfig(
                name=name,
                path=Path(raw_path),
                join_keys=join_keys,
                fields=tuple(fields),
                asof_enabled=asof_enabled,
                date_field=date_field,
                max_lag_days=max_lag_days,
                duplicate_policy=duplicate_policy,
            )
        )
    return panels


def external_factor_field_names(config: dict[str, Any] | None) -> list[str]:
    fields: list[str] = []
    for panel in configured_external_factor_panels(config):
        for field in panel.fields:
            if field.name not in fields:
                fields.append(field.name)
    return fields


def external_factor_panel_fingerprints(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    fingerprints: list[dict[str, Any]] = []
    for panel in configured_external_factor_panels(config):
        fingerprints.append(
            {
                "name": panel.name,
                "path": str(panel.path),
                "checksum": checksum(panel.path),
                "join_keys": list(panel.join_keys),
                "fields": [{"name": field.name, "dtype": field.dtype} for field in panel.fields],
                "asof": {
                    "enabled": panel.asof_enabled,
                    "date_field": panel.date_field,
                    "max_lag_days": panel.max_lag_days,
                },
                "duplicate_policy": panel.duplicate_policy,
            }
        )
    return fingerprints


def normalize_key_value(value: Any, *, field_name: str) -> str:
    if field_name.endswith("date") or field_name in {"rebalance_date", "available_date"}:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value or "").strip()
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            text = text[:10]
        parsed = parse_date(text, field_name=field_name)
        return parsed.isoformat() if parsed else ""
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    return text


def coerce_field_value(value: Any, field: ExternalFactorField) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    if field.dtype == "float":
        parsed = parse_float(text)
        if parsed is None:
            raise ValueError(f"Invalid float value for external factor {field.name}: {value!r}")
        return parsed
    if field.dtype == "int":
        parsed = parse_int(text, default=None)
        if parsed is None:
            raise ValueError(f"Invalid int value for external factor {field.name}: {value!r}")
        return parsed
    if field.dtype == "bool":
        parsed = parse_bool(text, default=None)
        if parsed is None:
            raise ValueError(f"Invalid bool value for external factor {field.name}: {value!r}")
        return parsed
    return text


def append_missing_flag(current: Any, field: str) -> str:
    values = [value for value in str(current or "").split(";") if value]
    if field not in values:
        values.append(field)
    return ";".join(values)


def require_columns(rows: list[dict[str, str]], columns: set[str], *, panel: ExternalFactorPanelConfig) -> None:
    available = set(rows[0]) if rows else set()
    missing = sorted(column for column in columns if column not in available)
    if missing:
        raise ValueError(f"External factor panel {panel.name!r} missing required field(s): {', '.join(missing)}")


def require_factor_columns(rows: list[dict[str, Any]], columns: set[str], *, panel: ExternalFactorPanelConfig) -> None:
    available: set[str] = set()
    for row in rows:
        available.update(str(key) for key in row)
    missing = sorted(column for column in columns if column not in available)
    if missing:
        raise ValueError(f"Factor rows missing join field(s) for external panel {panel.name!r}: {', '.join(missing)}")


def assert_no_output_collisions(rows: list[dict[str, Any]], fields: list[str], *, panel: ExternalFactorPanelConfig) -> None:
    existing: set[str] = set()
    for row in rows:
        existing.update(str(key) for key in row)
    collisions = sorted(field for field in fields if field in existing)
    if collisions:
        raise ValueError(
            f"External factor panel {panel.name!r} field(s) collide with existing factor fields: "
            f"{', '.join(collisions)}"
        )


def row_payload(row: dict[str, str], panel: ExternalFactorPanelConfig) -> dict[str, Any]:
    return {field.name: coerce_field_value(row.get(field.name), field) for field in panel.fields}


def build_exact_index(rows: list[dict[str, str]], panel: ExternalFactorPanelConfig) -> dict[tuple[str, ...], dict[str, Any]]:
    require_columns(rows, set(panel.join_keys) | {field.name for field in panel.fields}, panel=panel)
    index: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(normalize_key_value(row.get(field), field_name=field) for field in panel.join_keys)
        if key in index:
            detail = ";".join(f"{field}={value}" for field, value in zip(panel.join_keys, key))
            raise ValueError(f"Duplicate external factor panel {panel.name!r} rows for {detail}.")
        index[key] = row_payload(row, panel)
    return index


def build_asof_index(
    rows: list[dict[str, str]],
    panel: ExternalFactorPanelConfig,
) -> tuple[list[str], dict[tuple[str, ...], list[tuple[date, dict[str, Any]]]]]:
    match_keys = [field for field in panel.join_keys if field != "rebalance_date"]
    if not match_keys:
        raise ValueError(f"External factor panel {panel.name!r} as-of join requires at least one non-date join key.")
    require_columns(rows, set(match_keys) | {panel.date_field} | {field.name for field in panel.fields}, panel=panel)
    index: dict[tuple[str, ...], list[tuple[date, dict[str, Any]]]] = {}
    canonical_rows: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        available_date = parse_date(row.get(panel.date_field), field_name=f"{panel.name}.{panel.date_field}")
        if available_date is None:
            continue
        key = tuple(normalize_key_value(row.get(field), field_name=field) for field in match_keys)
        duplicate_key = (*key, available_date.isoformat())
        if duplicate_key in canonical_rows and panel.duplicate_policy == "fail":
            detail = ";".join(f"{field}={value}" for field, value in zip([*match_keys, panel.date_field], duplicate_key))
            raise ValueError(f"Duplicate external factor panel {panel.name!r} rows for {detail}.")
        canonical_rows[duplicate_key] = row
    for duplicate_key, row in canonical_rows.items():
        available_date = parse_date(row.get(panel.date_field), field_name=f"{panel.name}.{panel.date_field}")
        if available_date is None:
            continue
        key = tuple(duplicate_key[: len(match_keys)])
        index.setdefault(key, []).append((available_date, row_payload(row, panel)))
    for values in index.values():
        values.sort(key=lambda item: item[0])
    return match_keys, index


def join_external_factor_panels(
    factor_rows: list[dict[str, Any]],
    config: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    panels = configured_external_factor_panels(config)
    if not panels:
        return factor_rows
    output = [dict(row) for row in factor_rows]
    for panel in panels:
        field_names = [field.name for field in panel.fields]
        assert_no_output_collisions(output, field_names, panel=panel)
        rows = read_csv(panel.path)
        if not rows:
            for row in output:
                for field in field_names:
                    row[field] = None
                    row["missing_flags"] = append_missing_flag(row.get("missing_flags", ""), field)
            continue
        if panel.asof_enabled:
            required_factor_columns = {field for field in panel.join_keys if field != "rebalance_date"} | {"rebalance_date"}
            require_factor_columns(output, required_factor_columns, panel=panel)
            match_keys, index = build_asof_index(rows, panel)
            for row in output:
                rebalance_date = parse_date(row.get("rebalance_date"), field_name="factor_rows.rebalance_date")
                key = tuple(normalize_key_value(row.get(field), field_name=field) for field in match_keys)
                selected: dict[str, Any] | None = None
                if rebalance_date is not None:
                    for available_date, payload in reversed(index.get(key, [])):
                        if available_date <= rebalance_date:
                            if panel.max_lag_days is None or (rebalance_date - available_date).days <= panel.max_lag_days:
                                selected = payload
                            break
                for field in field_names:
                    value = selected.get(field) if selected else None
                    row[field] = value
                    if value is None:
                        row["missing_flags"] = append_missing_flag(row.get("missing_flags", ""), field)
            continue

        require_factor_columns(output, set(panel.join_keys), panel=panel)
        index = build_exact_index(rows, panel)
        for row in output:
            key = tuple(normalize_key_value(row.get(field), field_name=field) for field in panel.join_keys)
            selected = index.get(key)
            for field in field_names:
                value = selected.get(field) if selected else None
                row[field] = value
                if value is None:
                    row["missing_flags"] = append_missing_flag(row.get("missing_flags", ""), field)
    return output
