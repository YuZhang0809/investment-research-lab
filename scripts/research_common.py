from __future__ import annotations

import csv
import hashlib
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: format_csv_value(row.get(key)) for key in fieldnames})


def format_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime(DATE_FORMAT)
    return str(value)


def parse_date(value: str | None, *, field_name: str) -> date | None:
    if value is None or str(value).strip() == "":
        return None
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
        return float(str(value).replace(",", ""))
    except ValueError:
        return default


def parse_int(value: Any, *, default: int | None = None) -> int | None:
    number = parse_float(value)
    if number is None:
        return default
    return int(number)


def checksum(path: Path) -> str:
    hasher = hashlib.sha256()
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
