from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any

from research_common import append_manifest, read_table, write_table


LIFECYCLE_FIELDS = [
    "code",
    "first_master_seen_date",
    "last_master_seen_date",
    "next_absent_master_date",
    "master_snapshot_count",
    "first_price_date",
    "last_price_date",
    "tradable_price_count",
    "first_evidence_date",
    "last_evidence_date",
    "inferred_listed_date",
    "inferred_last_trading_date",
    "inferred_delisted_date",
    "lifecycle_status",
    "lifecycle_confidence",
    "lifecycle_date_source",
    "left_censored",
    "right_censored",
    "evidence_flags",
]

INFERRED_STATUS_BY_LIFECYCLE = {
    "active": "pit_inferred_lifecycle_active",
    "delisted": "pit_inferred_lifecycle_terminal",
    "unknown": "pit_inferred_lifecycle_unknown",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Infer listed/delisted/last-trading dates from source-dated listings "
            "snapshots and daily price evidence."
        )
    )
    parser.add_argument("--listings", required=True, type=Path, help="Source-dated listings CSV/Parquet.")
    parser.add_argument("--prices", required=True, type=Path, help="Daily prices CSV/Parquet.")
    parser.add_argument(
        "--out-lifecycle",
        required=True,
        type=Path,
        help="Output lifecycle table path. CSV or Parquet is inferred from suffix.",
    )
    parser.add_argument(
        "--out-listings",
        type=Path,
        help="Optional enriched listings panel path. CSV or Parquet is inferred from suffix.",
    )
    parser.add_argument("--listing-format", default="auto", choices=["auto", "csv", "parquet"])
    parser.add_argument("--price-format", default="auto", choices=["auto", "csv", "parquet"])
    parser.add_argument("--output-format", default="auto", choices=["auto", "csv", "parquet"])
    parser.add_argument(
        "--active-stale-calendar-days",
        type=int,
        default=30,
        help="Active names whose latest price is older than this are medium-confidence.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest/data_manifest.csv"),
        help="Manifest CSV to append derived outputs.",
    )
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def first_existing_column(columns: set[str], candidates: list[str]) -> str | None:
    for column in candidates:
        if column in columns:
            return column
    return None


def date_text(value: Any) -> str:
    if value is None:
        return ""
    pd = __import__("pandas")
    if pd.isna(value):
        return ""
    try:
        if value != value:
            return ""
    except TypeError:
        pass
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value)
    if not text or text.lower() in {"nat", "nan", "none"}:
        return ""
    return text[:10]


def count_text(value: Any) -> str:
    if value is None:
        return "0"
    pd = __import__("pandas")
    if pd.isna(value):
        return "0"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "0"


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def to_bool_series(frame: Any, column: str, *, default: bool) -> Any:
    pd = __import__("pandas")
    if column not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index)
    text = frame[column].fillna("").astype(str).str.strip().str.lower()
    true_values = {"1", "true", "t", "yes", "y"}
    false_values = {"0", "false", "f", "no", "n"}
    values = []
    for item in text:
        if item in true_values:
            values.append(True)
        elif item in false_values:
            values.append(False)
        else:
            values.append(default)
    return pd.Series(values, index=frame.index)


def numeric_series(frame: Any, candidates: list[str]) -> Any:
    pd = __import__("pandas")
    columns = [column for column in candidates if column in frame.columns]
    if not columns:
        return pd.Series([0.0] * len(frame), index=frame.index)
    values = [pd.to_numeric(frame[column], errors="coerce").fillna(0.0) for column in columns]
    result = values[0]
    for value in values[1:]:
        result = result.combine(value, max)
    return result


def min_date(*values: Any) -> Any:
    clean = [value for value in values if date_text(value)]
    return min(clean) if clean else None


def max_date(*values: Any) -> Any:
    clean = [value for value in values if date_text(value)]
    return max(clean) if clean else None


def next_snapshot_after(snapshot_dates: list[Any], value: Any) -> Any | None:
    if value is None:
        return None
    index = bisect_right(snapshot_dates, value)
    if index >= len(snapshot_dates):
        return None
    return snapshot_dates[index]


def latest_rows_by_code(frame: Any, date_column: str) -> Any:
    if frame.empty:
        return frame
    sorted_frame = frame.sort_values(["code", date_column])
    return sorted_frame.drop_duplicates("code", keep="last").set_index("code")


def summarize_listings(listings: Any) -> tuple[Any, Any, list[Any], Any | None, Any | None]:
    pd = __import__("pandas")
    frame = listings.copy()
    frame["code"] = frame["code"].astype(str).str.strip()
    source_column = first_existing_column(set(frame.columns), ["source_date", "snapshot_date", "Date", "date"])
    if source_column is None:
        frame["_source_dt"] = pd.NaT
    else:
        frame["_source_dt"] = pd.to_datetime(frame[source_column], errors="coerce")
    frame = frame[(frame["code"] != "") & frame["_source_dt"].notna()].copy()
    if frame.empty:
        empty = pd.DataFrame(columns=["code"])
        return empty, empty, [], None, None

    grouped = (
        frame.groupby("code", as_index=False)
        .agg(
            first_master_seen_date=("_source_dt", "min"),
            last_master_seen_date=("_source_dt", "max"),
            master_snapshot_count=("_source_dt", "nunique"),
        )
    )
    latest = latest_rows_by_code(frame, "_source_dt")
    snapshot_dates = sorted(frame["_source_dt"].dropna().unique())
    first_snapshot = snapshot_dates[0] if snapshot_dates else None
    last_snapshot = snapshot_dates[-1] if snapshot_dates else None
    grouped["next_absent_master_date"] = grouped["last_master_seen_date"].map(
        lambda value: next_snapshot_after(snapshot_dates, value)
    )
    return grouped, latest, snapshot_dates, first_snapshot, last_snapshot


def summarize_prices(prices: Any) -> tuple[Any, Any | None, Any | None]:
    pd = __import__("pandas")
    frame = prices.copy()
    frame["code"] = frame["code"].astype(str).str.strip()
    frame["_date_dt"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[(frame["code"] != "") & frame["_date_dt"].notna()].copy()
    if frame.empty:
        empty = pd.DataFrame(columns=["code"])
        return empty, None, None

    price_value = numeric_series(frame, ["unadjusted_close", "adjusted_close", "close", "price"])
    liquidity_value = numeric_series(frame, ["trading_value", "volume"])
    tradable = to_bool_series(frame, "tradable_flag", default=True)
    has_liquidity_columns = any(column in frame.columns for column in ["trading_value", "volume"])
    if has_liquidity_columns:
        tradable = tradable & (liquidity_value > 0)
    frame = frame[(price_value > 0) & tradable].copy()
    if frame.empty:
        empty = pd.DataFrame(columns=["code"])
        return empty, None, None

    grouped = (
        frame.groupby("code", as_index=False)
        .agg(
            first_price_date=("_date_dt", "min"),
            last_price_date=("_date_dt", "max"),
            tradable_price_count=("_date_dt", "nunique"),
        )
    )
    first_price = frame["_date_dt"].min()
    last_price = frame["_date_dt"].max()
    return grouped, first_price, last_price


def infer_lifecycle_row(
    row: dict[str, Any],
    *,
    global_first_master: Any | None,
    global_last_master: Any | None,
    global_first_price: Any | None,
    global_last_price: Any | None,
    active_stale_calendar_days: int,
) -> dict[str, str]:
    first_master = row.get("first_master_seen_date")
    last_master = row.get("last_master_seen_date")
    next_absent = row.get("next_absent_master_date")
    first_price = row.get("first_price_date")
    last_price = row.get("last_price_date")
    has_master = bool(date_text(first_master))
    has_price = bool(date_text(first_price))
    flags: set[str] = set()

    if has_master and global_first_master is not None and first_master == global_first_master:
        flags.add("left_censored_master")
    if has_price and global_first_price is not None and first_price == global_first_price:
        flags.add("left_censored_price")
    if has_master and global_last_master is not None and last_master == global_last_master:
        flags.add("right_censored_master")
    if has_price and global_last_price is not None and last_price == global_last_price:
        flags.add("right_censored_price")
    if has_master and has_price and first_price < first_master:
        flags.add("first_price_before_first_master")
    if has_master and has_price and first_master < first_price:
        flags.add("first_master_before_first_price")

    if has_master and global_last_master is not None and last_master < global_last_master:
        status = "delisted"
        flags.add("master_disappeared_before_sample_end")
    elif has_master:
        status = "active"
    else:
        status = "unknown"
        flags.add("no_master_evidence")

    if not has_price:
        flags.add("no_price_evidence")
    elif global_last_price is not None and last_price < global_last_price:
        flags.add("price_stopped_before_sample_end")

    if status == "delisted" and has_price and next_absent is not None and last_price > next_absent:
        flags.add("price_after_master_disappearance")

    inferred_listed = min_date(first_master, first_price)
    inferred_last_trading = last_price if status == "delisted" and has_price else None
    inferred_delisted = next_absent if status == "delisted" else None
    last_evidence = max_date(last_master, last_price)
    first_evidence = min_date(first_master, first_price)

    if status == "delisted":
        if has_master and has_price and next_absent is not None:
            confidence = "high"
        elif has_master and next_absent is not None:
            confidence = "medium"
        else:
            confidence = "low"
    elif status == "active":
        if has_price and global_last_price is not None:
            stale_days = (global_last_price - last_price).days
            confidence = "high" if stale_days <= active_stale_calendar_days else "medium"
            if stale_days > active_stale_calendar_days:
                flags.add("active_price_stale")
        else:
            confidence = "medium" if has_master else "low"
    else:
        confidence = "low"

    left_censored = any(flag.startswith("left_censored") for flag in flags)
    right_censored = any(flag.startswith("right_censored") for flag in flags)

    return {
        "code": str(row.get("code") or ""),
        "first_master_seen_date": date_text(first_master),
        "last_master_seen_date": date_text(last_master),
        "next_absent_master_date": date_text(next_absent),
        "master_snapshot_count": count_text(row.get("master_snapshot_count")),
        "first_price_date": date_text(first_price),
        "last_price_date": date_text(last_price),
        "tradable_price_count": count_text(row.get("tradable_price_count")),
        "first_evidence_date": date_text(first_evidence),
        "last_evidence_date": date_text(last_evidence),
        "inferred_listed_date": date_text(inferred_listed),
        "inferred_last_trading_date": date_text(inferred_last_trading),
        "inferred_delisted_date": date_text(inferred_delisted),
        "lifecycle_status": status,
        "lifecycle_confidence": confidence,
        "lifecycle_date_source": "inferred_from_listings_snapshots_and_prices",
        "left_censored": bool_text(left_censored),
        "right_censored": bool_text(right_censored),
        "evidence_flags": "|".join(sorted(flags)),
    }


def build_inferred_lifecycle_from_frames(
    listings: Any,
    prices: Any,
    *,
    active_stale_calendar_days: int = 30,
) -> tuple[Any, Any]:
    pd = __import__("pandas")
    listing_summary, _latest_listing_rows, _snapshots, first_master, last_master = summarize_listings(listings)
    price_summary, first_price, last_price = summarize_prices(prices)
    if listing_summary.empty and price_summary.empty:
        lifecycle = pd.DataFrame(columns=LIFECYCLE_FIELDS)
        return lifecycle, listings.copy()

    merged = pd.merge(listing_summary, price_summary, how="outer", on="code")
    rows = [
        infer_lifecycle_row(
            row,
            global_first_master=first_master,
            global_last_master=last_master,
            global_first_price=first_price,
            global_last_price=last_price,
            active_stale_calendar_days=active_stale_calendar_days,
        )
        for row in merged.to_dict(orient="records")
    ]
    lifecycle = pd.DataFrame(rows, columns=LIFECYCLE_FIELDS).sort_values("code")
    enriched = enrich_listings_with_lifecycle(listings, lifecycle)
    return lifecycle, enriched


def enrich_listings_with_lifecycle(listings: Any, lifecycle: Any) -> Any:
    frame = listings.copy()
    if frame.empty:
        return frame
    lifecycle_by_code = lifecycle.set_index("code").to_dict(orient="index")

    extra_columns = [
        "last_trading_date",
        "lifecycle_confidence",
        "lifecycle_date_source",
        "lifecycle_evidence_flags",
        "left_censored",
        "right_censored",
    ]
    for column in extra_columns:
        if column not in frame.columns:
            frame[column] = ""

    enriched_rows: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        code = str(row.get("code") or "").strip()
        info = lifecycle_by_code.get(code)
        copied = dict(row)
        if info is None:
            enriched_rows.append(copied)
            continue
        if not str(copied.get("listed_date") or "").strip():
            copied["listed_date"] = info.get("inferred_listed_date", "")
        if info.get("lifecycle_status") == "delisted":
            copied["delisted_date"] = copied.get("delisted_date") or info.get("inferred_delisted_date", "")
            copied["last_trading_date"] = copied.get("last_trading_date") or info.get("inferred_last_trading_date", "")
        copied["listing_lifecycle_status"] = INFERRED_STATUS_BY_LIFECYCLE.get(
            info.get("lifecycle_status"), "pit_inferred_lifecycle_unknown"
        )
        copied["lifecycle_confidence"] = info.get("lifecycle_confidence", "")
        copied["lifecycle_date_source"] = info.get("lifecycle_date_source", "")
        copied["lifecycle_evidence_flags"] = info.get("evidence_flags", "")
        copied["left_censored"] = info.get("left_censored", "")
        copied["right_censored"] = info.get("right_censored", "")
        if str(copied.get("source") or "").strip():
            copied["source"] = f"{copied['source']}+inferred_lifecycle"
        else:
            copied["source"] = "inferred_lifecycle"
        enriched_rows.append(copied)
    return __import__("pandas").DataFrame(enriched_rows)


def main() -> int:
    args = build_parser().parse_args()
    listings = read_table(args.listings, args.listing_format)
    prices = read_table(args.prices, args.price_format)
    lifecycle, enriched = build_inferred_lifecycle_from_frames(
        listings,
        prices,
        active_stale_calendar_days=args.active_stale_calendar_days,
    )

    write_table(lifecycle, args.out_lifecycle, args.output_format, fieldnames=LIFECYCLE_FIELDS)
    if args.out_listings:
        write_table(enriched, args.out_listings, args.output_format)

    date_range = ""
    if not lifecycle.empty:
        date_range = f"{lifecycle['first_evidence_date'].min()}..{lifecycle['last_evidence_date'].max()}"
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_inferred_lifecycle",
            file_path=args.out_lifecycle,
            vendor="local",
            schema_version="inferred_lifecycle_v0_1",
            date_range=date_range,
            notes=f"{len(lifecycle)} codes; generated_at={datetime.now().isoformat(timespec='seconds')}",
        )
        if args.out_listings:
            append_manifest(
                args.manifest,
                source="derived_inferred_lifecycle_listings",
                file_path=args.out_listings,
                vendor="local",
                schema_version="listings_with_inferred_lifecycle_v0_1",
                date_range=date_range,
                notes=f"{len(enriched)} listing rows; generated_at={datetime.now().isoformat(timespec='seconds')}",
            )

    status_counts = lifecycle["lifecycle_status"].value_counts().to_dict() if not lifecycle.empty else {}
    confidence_counts = lifecycle["lifecycle_confidence"].value_counts().to_dict() if not lifecycle.empty else {}
    print(f"Wrote {len(lifecycle)} lifecycle rows to {args.out_lifecycle}")
    if args.out_listings:
        print(f"Wrote {len(enriched)} enriched listing rows to {args.out_listings}")
    print(f"lifecycle_status_counts={status_counts}")
    print(f"lifecycle_confidence_counts={confidence_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
