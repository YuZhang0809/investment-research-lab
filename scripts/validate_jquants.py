from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from jquants_client import API_BASE, request_paginated


DEFAULT_CODE = "86970"
DEFAULT_DATE = "2026-05-15"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate J-Quants API V2 access and minimal schemas.")
    parser.add_argument("--api-key-env", default="JQUANTS_API_KEY", help="Environment variable containing API key.")
    parser.add_argument("--date", default=DEFAULT_DATE, help="Date for master and daily bars checks.")
    parser.add_argument("--code", default=DEFAULT_CODE, help="Issue code for financial summary check.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/jquants/validation"),
        help="Directory for optional sample CSV outputs. This path is ignored by Git.",
    )
    parser.add_argument("--write-samples", action="store_true", help="Write sample response rows to CSV.")
    parser.add_argument("--preflight-only", action="store_true", help="Only check local env/config prerequisites.")
    return parser


def summarize_rows(name: str, rows: list[dict[str, Any]], required_any: list[str]) -> dict[str, Any]:
    columns = sorted({key for row in rows[:100] for key in row.keys()})
    missing_all = [field for field in required_any if field not in columns]
    return {
        "dataset": name,
        "rows": len(rows),
        "columns_sample": columns[:40],
        "missing_expected_columns": missing_all,
        "ok": bool(rows) and not missing_all,
    }


def write_sample_csv(path: Path, rows: list[dict[str, Any]], limit: int = 20) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = rows[:limit]
    fieldnames = sorted({key for row in sample for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sample)


def validate(api_key: str, *, date: str, code: str, output_dir: Path, write_samples: bool) -> int:
    checks: list[dict[str, Any]] = []

    master = request_paginated(api_key, "/equities/master", {"date": date})
    checks.append(summarize_rows("equities_master", master, ["Code", "Date"]))
    if write_samples:
        write_sample_csv(output_dir / f"equities_master_{date}.csv", master)

    bars = request_paginated(api_key, "/equities/bars/daily", {"date": date})
    checks.append(summarize_rows("equities_bars_daily", bars, ["Code", "Date"]))
    if write_samples:
        write_sample_csv(output_dir / f"equities_bars_daily_{date}.csv", bars)

    fin_summary = request_paginated(api_key, "/fins/summary", {"code": code})
    checks.append(summarize_rows("fins_summary", fin_summary, ["Code"]))
    if write_samples:
        write_sample_csv(output_dir / f"fins_summary_{code}.csv", fin_summary)

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "api_base": API_BASE,
        "date": date,
        "code": code,
        "checks": checks,
        "overall_ok": all(check["ok"] for check in checks),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["overall_ok"] else 1


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.environ.get(args.api_key_env, "")

    preflight = {
        "api_base": API_BASE,
        "api_key_env": args.api_key_env,
        "api_key_present": bool(api_key),
        "uses_v2_api_key_auth": True,
        "writes_samples": bool(args.write_samples),
    }
    if args.preflight_only:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return 0

    if not api_key:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        print(
            f"J-Quants API key is not set. Set {args.api_key_env} before live validation.",
            file=sys.stderr,
        )
        return 2

    return validate(
        api_key,
        date=args.date,
        code=args.code,
        output_dir=args.output_dir,
        write_samples=args.write_samples,
    )


if __name__ == "__main__":
    raise SystemExit(main())
