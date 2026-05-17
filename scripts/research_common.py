from __future__ import annotations

import csv
import hashlib
import math
from bisect import bisect_left, bisect_right
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable


DATE_FORMAT = "%Y-%m-%d"


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to read config files. Install it or convert the config to JSON."
        ) from exc

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def resolve_table_format(path: Path, table_format: str = "auto") -> str:
    normalized = table_format.lower()
    if normalized != "auto":
        if normalized not in {"csv", "parquet"}:
            raise ValueError(f"Unsupported table format: {table_format}")
        return normalized
    if path.suffix.lower() == ".csv":
        return "csv"
    if path.suffix.lower() == ".parquet" or path.is_dir():
        return "parquet"
    raise ValueError(f"Cannot infer table format from {path}; pass format='csv' or format='parquet'")


def require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for table IO. Install requirements.txt first.") from exc
    return pd


def require_pyarrow() -> None:
    try:
        import pyarrow  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Parquet IO. Install requirements.txt first.") from exc


def read_table(path: Path, format: str = "auto"):
    table_format = resolve_table_format(path, format)
    pd = require_pandas()
    if table_format == "csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    require_pyarrow()
    return pd.read_parquet(path)


def frame_from_rows(rows: Iterable[dict[str, Any]], fieldnames: list[str] | None = None):
    pd = require_pandas()
    normalized_rows = [
        {str(key): format_csv_value(value) for key, value in row.items()}
        for row in rows
    ]
    frame = pd.DataFrame(normalized_rows)
    if fieldnames is not None:
        for field in fieldnames:
            if field not in frame.columns:
                frame[field] = ""
        frame = frame[fieldnames]
    return frame


def write_table(
    data: Any,
    path: Path,
    format: str = "parquet",
    *,
    fieldnames: list[str] | None = None,
) -> None:
    table_format = resolve_table_format(path, format)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(data, "to_dict") and hasattr(data, "columns"):
        frame = data.copy()
        if fieldnames is not None:
            for field in fieldnames:
                if field not in frame.columns:
                    frame[field] = ""
            frame = frame[fieldnames]
    else:
        frame = frame_from_rows(data, fieldnames)

    if table_format == "csv":
        rows = frame.to_dict(orient="records")
        fields = fieldnames or [str(column) for column in frame.columns]
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: format_csv_value(row.get(key)) for key in fields})
        return

    require_pyarrow()
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
        return

    path.mkdir(parents=True, exist_ok=True)
    dataset_file = path / "part-00000.parquet"
    frame.to_parquet(dataset_file, index=False)


def normalize_row_value(value: Any) -> str:
    pd = require_pandas()
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return format_csv_value(value)


def read_csv(path: Path) -> list[dict[str, str]]:
    frame = read_table(path, format="auto")
    return [
        {str(key): normalize_row_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def read_raw_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    write_table(rows, path, format="csv", fieldnames=fieldnames)


def format_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime(DATE_FORMAT)
    return str(value)


def parse_date(value: Any, *, field_name: str) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(f"Invalid date in {field_name}: {value!r}; expected YYYY-MM-DD") from exc


def parse_bool(value: Any, *, default: bool | None = None) -> bool | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return default


def parse_float(value: Any, *, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return default
    if not math.isfinite(number):
        return default
    return number


def parse_int(value: Any, *, default: int | None = None) -> int | None:
    number = parse_float(value)
    if number is None:
        return default
    return int(number)


def checksum(path: Path) -> str:
    hasher = hashlib.sha256()
    if path.is_dir():
        for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
            hasher.update(file_path.relative_to(path).as_posix().encode("utf-8"))
            with file_path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    hasher.update(chunk)
        return hasher.hexdigest()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def date_range_from_rows(rows: list[dict[str, str]], column: str) -> str:
    dates = [parse_date(row.get(column), field_name=column) for row in rows if row.get(column)]
    clean_dates = [value for value in dates if value is not None]
    if not clean_dates:
        return ""
    return f"{min(clean_dates).strftime(DATE_FORMAT)}..{max(clean_dates).strftime(DATE_FORMAT)}"


def median_or_none(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return float(median(clean))


def month_key(value: date) -> str:
    return value.strftime("%Y%m")


def trading_calendar_from_rows(rows: list[dict[str, str]], date_column: str = "date") -> list[date]:
    values: set[date] = set()
    for row in rows:
        value = parse_date(row.get(date_column), field_name=date_column)
        if value is not None:
            values.add(value)
    return sorted(values)


def trading_day_offset(
    calendar: list[date],
    anchor: date,
    offset: int,
    *,
    mode: str = "on_or_after",
) -> date | None:
    if not calendar:
        return None
    if mode == "on_or_after":
        index = bisect_left(calendar, anchor)
    elif mode == "after":
        index = bisect_right(calendar, anchor)
    elif mode == "on_or_before":
        index = bisect_right(calendar, anchor) - 1
    else:
        raise ValueError(f"Unsupported trading day anchor mode: {mode}")
    target_index = index + offset
    if target_index < 0 or target_index >= len(calendar):
        return None
    return calendar[target_index]


def append_manifest(
    manifest_path: Path,
    *,
    source: str,
    file_path: Path,
    vendor: str,
    schema_version: str,
    date_range: str,
    notes: str = "",
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source",
        "file_path",
        "downloaded_at",
        "vendor",
        "schema_version",
        "date_range",
        "checksum",
        "notes",
    ]
    exists = manifest_path.exists()
    with manifest_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if not exists or manifest_path.stat().st_size == 0:
            writer.writeheader()
        writer.writerow(
            {
                "source": source,
                "file_path": str(file_path.as_posix()),
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                "vendor": vendor,
                "schema_version": schema_version,
                "date_range": date_range,
                "checksum": checksum(file_path),
                "notes": notes,
            }
        )
