from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from research_common import parse_date, parse_float, read_table


GROUP_MEMBERSHIP_FIELDS = [
    "rebalance_date",
    "available_date",
    "code",
    "group_type",
    "group_id",
    "group_name",
    "membership_weight",
    "purity_score",
    "source",
    "notes",
]


@dataclass(frozen=True)
class Membership:
    effective_date: date
    date_field: str
    code: str
    group_type: str
    group_id: str
    group_name: str
    membership_weight: float
    purity_score: float | None
    source: str
    notes: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class MembershipPanel:
    rows: list[Membership]
    date_field: str
    mode: str


@dataclass(frozen=True)
class PricePoint:
    date: date
    adjusted_close: float | None
    trading_value: float | None
    volume: float | None
    market_cap: float | None


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:.10g}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def parse_optional_date(value: Any, field_name: str) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_text(value)
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    return parse_date(text, field_name=field_name)


def first_present(row: dict[str, Any], fields: list[str]) -> Any:
    for field in fields:
        if field in row and normalize_text(row.get(field)) != "":
            return row.get(field)
    return ""


def first_number(row: dict[str, Any], fields: list[str]) -> float | None:
    for field in fields:
        value = parse_float(row.get(field))
        if value is not None:
            return value
    return None


def infer_membership_date_field(columns: set[str], requested: str = "auto") -> str:
    if requested != "auto":
        if requested not in columns:
            raise ValueError(f"Group membership panel missing requested date field: {requested}")
        return requested
    if "rebalance_date" in columns:
        return "rebalance_date"
    if "available_date" in columns:
        return "available_date"
    raise ValueError("Group membership panel requires rebalance_date or available_date.")


def membership_mode(date_field: str) -> str:
    return "exact" if date_field == "rebalance_date" else "asof"


def load_group_membership_panel(
    path: Path,
    *,
    input_format: str = "auto",
    date_field: str = "auto",
    duplicate_policy: str = "fail",
) -> MembershipPanel:
    if duplicate_policy not in {"fail", "aggregate"}:
        raise ValueError("duplicate_policy must be fail or aggregate.")
    frame = read_table(path, format=input_format)
    rows = frame.to_dict(orient="records")
    if not rows:
        raise ValueError("Group membership panel is empty.")
    columns = set(str(column) for column in frame.columns)
    resolved_date_field = infer_membership_date_field(columns, date_field)
    required = {"code", "group_type", "group_id", resolved_date_field}
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"Group membership panel missing required field(s): {', '.join(missing)}")

    parsed: dict[tuple[str, str, str, str], Membership] = {}
    for row in rows:
        row_date = parse_optional_date(row.get(resolved_date_field), f"group_membership.{resolved_date_field}")
        if row_date is None:
            raise ValueError(f"Group membership row missing {resolved_date_field}.")
        code = normalize_text(row.get("code"))
        group_type = normalize_text(row.get("group_type"))
        group_id = normalize_text(row.get("group_id"))
        if not code:
            raise ValueError("Group membership row missing code.")
        if not group_type or not group_id:
            raise ValueError("Group membership row has missing group membership.")
        raw_weight = normalize_text(row.get("membership_weight"))
        weight = 1.0 if raw_weight == "" else parse_float(raw_weight)
        if weight is None or weight <= 0:
            raise ValueError(f"Invalid membership_weight for code={code};group_type={group_type};group_id={group_id}.")
        raw_purity = normalize_text(row.get("purity_score"))
        purity = None if raw_purity == "" else parse_float(raw_purity)
        if raw_purity and (purity is None or purity < 0 or purity > 1):
            raise ValueError(f"Invalid purity_score for code={code};group_type={group_type};group_id={group_id}.")

        key = (row_date.isoformat(), code, group_type, group_id)
        membership = Membership(
            effective_date=row_date,
            date_field=resolved_date_field,
            code=code,
            group_type=group_type,
            group_id=group_id,
            group_name=normalize_text(row.get("group_name")),
            membership_weight=float(weight),
            purity_score=purity,
            source=normalize_text(row.get("source")),
            notes=normalize_text(row.get("notes")),
            raw={str(key): value for key, value in row.items()},
        )
        if key in parsed:
            if duplicate_policy == "fail":
                raise ValueError(
                    "Duplicate group membership row for "
                    f"date={row_date};code={code};group_type={group_type};group_id={group_id}."
                )
            existing = parsed[key]
            membership = Membership(
                effective_date=existing.effective_date,
                date_field=existing.date_field,
                code=existing.code,
                group_type=existing.group_type,
                group_id=existing.group_id,
                group_name=existing.group_name or membership.group_name,
                membership_weight=existing.membership_weight + membership.membership_weight,
                purity_score=existing.purity_score if existing.purity_score is not None else membership.purity_score,
                source=existing.source or membership.source,
                notes=existing.notes or membership.notes,
                raw={**existing.raw, **membership.raw},
            )
        parsed[key] = membership
    return MembershipPanel(
        rows=sorted(parsed.values(), key=lambda item: (item.effective_date, item.code, item.group_type, item.group_id)),
        date_field=resolved_date_field,
        mode=membership_mode(resolved_date_field),
    )


def memberships_for_date(panel: MembershipPanel, target_date: date) -> list[Membership]:
    eligible = [row for row in panel.rows if row.effective_date <= target_date]
    if panel.mode == "exact":
        snapshot_dates = sorted({row.effective_date for row in eligible})
        if not snapshot_dates:
            return []
        snapshot_date = snapshot_dates[-1]
        return [row for row in eligible if row.effective_date == snapshot_date]

    latest: dict[tuple[str, str, str], Membership] = {}
    for row in eligible:
        latest[(row.code, row.group_type, row.group_id)] = row
    return list(latest.values())


def price_code(row: dict[str, Any]) -> str:
    return normalize_text(first_present(row, ["code", "Code", "LocalCode"]))


def price_date(row: dict[str, Any]) -> date | None:
    return parse_optional_date(first_present(row, ["date", "Date", "price_date"]), "prices.date")


def build_price_index(rows: list[dict[str, Any]]) -> dict[str, list[PricePoint]]:
    grouped: dict[str, list[PricePoint]] = {}
    seen: set[tuple[str, date]] = set()
    for row in rows:
        code = price_code(row)
        row_date = price_date(row)
        if not code or row_date is None:
            continue
        key = (code, row_date)
        if key in seen:
            raise ValueError(f"Duplicate price row for code={code};date={row_date}.")
        seen.add(key)
        close = first_number(row, ["adjusted_close", "AdjustmentClose", "close", "Close", "unadjusted_close"])
        grouped.setdefault(code, []).append(
            PricePoint(
                date=row_date,
                adjusted_close=close,
                trading_value=first_number(row, ["trading_value", "TradingValue", "turnover_value", "TurnoverValue"]),
                volume=first_number(row, ["volume", "Volume", "AdjustmentVolume"]),
                market_cap=first_number(row, ["market_cap", "MarketCapitalization", "market_capitalization"]),
            )
        )
    for values in grouped.values():
        values.sort(key=lambda item: item.date)
    return grouped


def latest_price_point(points: list[PricePoint], target_date: date) -> PricePoint | None:
    dates = [point.date for point in points]
    index = bisect_right(dates, target_date) - 1
    if index < 0:
        return None
    return points[index]


def load_dates(path: Path | None, values: list[str] | None, *, field_name: str = "date") -> list[date]:
    dates: set[date] = set()
    for value in values or []:
        parsed = parse_optional_date(value, field_name)
        if parsed is not None:
            dates.add(parsed)
    if path is not None:
        frame = read_table(path, format="auto")
        for row in frame.to_dict(orient="records"):
            parsed = parse_optional_date(row.get("rebalance_date") or row.get("date") or row.get("Date"), field_name)
            if parsed is not None:
                dates.add(parsed)
    return sorted(dates)
