from __future__ import annotations

import argparse
from pathlib import Path

from group_beta_common import load_group_membership_panel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a generic group membership panel.")
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument(
        "--date-field",
        default="auto",
        help="auto, rebalance_date, available_date, or another explicit availability date field.",
    )
    parser.add_argument("--duplicate-policy", choices=["fail", "aggregate"], default="fail")
    return parser


def validate_panel(
    panel: Path,
    *,
    input_format: str = "auto",
    date_field: str = "auto",
    duplicate_policy: str = "fail",
) -> int:
    parsed = load_group_membership_panel(
        panel,
        input_format=input_format,
        date_field=date_field,
        duplicate_policy=duplicate_policy,
    )
    return len(parsed.rows)


def main() -> int:
    args = build_parser().parse_args()
    row_count = validate_panel(
        args.panel,
        input_format=args.input_format,
        date_field=args.date_field,
        duplicate_policy=args.duplicate_policy,
    )
    print(f"Validated {row_count} group membership rows in {args.panel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
