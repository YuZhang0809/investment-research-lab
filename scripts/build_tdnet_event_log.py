from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from research_common import append_manifest, load_yaml, read_csv, write_csv


DEFAULT_FIELDS = [
    "event_id",
    "announcement_datetime",
    "code",
    "company_name",
    "document_type",
    "event_label",
    "title",
    "url_or_doc_id",
    "parsed_flag",
    "parse_confidence",
    "notes",
    "next_1d_return",
    "next_5d_return",
    "next_20d_return",
    "next_60d_return",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize a TDnet/manual event CSV into the v0.1 event log.")
    parser.add_argument("--config", type=Path, default=Path("configs/tdnet_events_v0_1.example.yml"))
    parser.add_argument("--events", type=Path, default=Path("experiments/tdnet_events_v0_1/events.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/events"))
    parser.add_argument("--run-label", default="observation_template")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def parse_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid announcement_datetime: {value!r}")


def bool_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return "true"
    if text in {"0", "false", "no", "n"}:
        return "false"
    return ""


def normalize_rows(rows: list[dict[str, str]], allowed_labels: set[str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        code = (row.get("code") or "").strip()
        announcement = parse_datetime(row.get("announcement_datetime", ""))
        label = (row.get("event_label") or "other").strip()
        if label not in allowed_labels:
            label = "other"
        confidence = row.get("parse_confidence", "")
        if confidence:
            parsed_confidence = float(confidence)
            confidence = f"{max(0.0, min(parsed_confidence, 1.0)):.3f}"
        normalized.append(
            {
                "event_id": row.get("event_id") or f"manual_{index:06d}",
                "announcement_datetime": announcement.isoformat(sep=" ") if announcement else "",
                "code": code,
                "company_name": row.get("company_name", ""),
                "document_type": row.get("document_type", ""),
                "event_label": label,
                "title": row.get("title", ""),
                "url_or_doc_id": row.get("url_or_doc_id", ""),
                "parsed_flag": bool_text(row.get("parsed_flag")),
                "parse_confidence": confidence,
                "notes": row.get("notes", ""),
                "next_1d_return": row.get("next_1d_return", ""),
                "next_5d_return": row.get("next_5d_return", ""),
                "next_20d_return": row.get("next_20d_return", ""),
                "next_60d_return": row.get("next_60d_return", ""),
            }
        )
    return normalized


def output_suffix(rows: list[dict[str, Any]], run_label: str) -> str:
    dates = []
    for row in rows:
        announcement = parse_datetime(row.get("announcement_datetime", ""))
        if announcement:
            dates.append(announcement)
    if not dates:
        return run_label
    first = min(dates).strftime("%Y%m")
    last = max(dates).strftime("%Y%m")
    return first if first == last else f"{first}_{last}"


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    allowed_labels = set(config.get("events", {}).get("labels", [])) or {"other"}
    rows = normalize_rows(read_csv(args.events), allowed_labels)
    suffix = output_suffix(rows, args.run_label)
    output_path = args.out_dir / f"tdnet_events_{suffix}.csv"
    write_csv(output_path, rows, DEFAULT_FIELDS)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_tdnet_event_log",
            file_path=output_path,
            vendor="local",
            schema_version="tdnet_events_v0_1",
            date_range=suffix,
            notes=f"{len(rows)} event rows; labels only; no trading",
        )
    print(f"Wrote {len(rows)} TDnet event rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
