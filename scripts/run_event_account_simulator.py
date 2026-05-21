from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from math import floor
from pathlib import Path
from typing import Any

from research_common import (
    append_manifest,
    parse_bool,
    parse_date,
    parse_float,
    read_csv,
    trading_day_offset,
    write_table,
)


SUMMARY_FIELDS = [
    "run_label",
    "initial_capital",
    "final_equity",
    "total_return",
    "cash",
    "open_positions",
    "closed_positions",
    "event_count",
    "entered_event_count",
    "skipped_event_count",
    "trade_count",
    "cumulative_commission",
    "cumulative_tax",
    "entry_lag_trading_days",
    "entry_price_mode",
    "exit_price_mode",
    "holding_trading_days",
    "target_event_weight",
    "max_concurrent_positions",
]

TRADE_FIELDS = [
    "event_id",
    "code",
    "side",
    "signal_datetime",
    "execution_date",
    "execution_price_mode",
    "price",
    "shares",
    "gross_value",
    "commission",
    "realized_gain",
    "estimated_tax",
    "cash_after",
]

POSITION_FIELDS = [
    "event_id",
    "code",
    "event_label",
    "announcement_datetime",
    "entry_date",
    "entry_price_mode",
    "entry_price",
    "exit_date",
    "exit_price_mode",
    "exit_price",
    "shares",
    "gross_entry_value",
    "gross_exit_value",
    "gross_return",
    "net_pnl_after_cost_tax",
    "status",
]

EQUITY_FIELDS = [
    "date",
    "cash",
    "gross_exposure",
    "equity",
    "open_positions",
    "cumulative_commission",
    "cumulative_tax",
]

FAILURE_FIELDS = [
    "event_id",
    "code",
    "failure_date",
    "failure_type",
    "detail",
    "announcement_datetime",
    "entry_date",
    "exit_date",
]


@dataclass(frozen=True)
class PricePoint:
    date: date
    open: float | None
    close: float | None
    tradable: bool


@dataclass
class PlannedEvent:
    event_id: str
    code: str
    announcement: datetime
    event_label: str
    entry_date: date | None
    exit_date: date | None
    row: dict[str, str]


@dataclass(frozen=True)
class Position:
    event_id: str
    code: str
    event_label: str
    announcement: datetime
    entry_date: date
    exit_date: date
    entry_price: float
    shares: int
    gross_entry_value: float
    entry_commission: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a generic long-only event-driven account-level simulator using daily bars."
    )
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/events"))
    parser.add_argument("--run-label", default="event_account")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--target-event-weight", type=float, default=0.1)
    parser.add_argument("--max-concurrent-positions", type=int, default=10)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--tax-rate", type=float, default=0.0)
    parser.add_argument("--entry-lag-trading-days", type=int, default=1)
    parser.add_argument("--entry-price-mode", choices=["next_open", "next_close"], default="next_open")
    parser.add_argument("--holding-trading-days", type=int, default=20)
    parser.add_argument("--exit-price-mode", choices=["open", "close"], default="close")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="csv")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def fmt(value: float | int | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def first_text(row: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def first_number(row: dict[str, Any], *fields: str) -> float | None:
    for field in fields:
        value = parse_float(row.get(field))
        if value is not None:
            return value
    return None


def price_code(row: dict[str, Any]) -> str:
    return first_text(row, "code", "Code", "LocalCode")


def price_date(row: dict[str, Any]) -> date | None:
    return parse_date(first_text(row, "date", "Date"), field_name="prices.date")


def price_open(row: dict[str, Any]) -> float | None:
    return first_number(row, "adjusted_open", "AdjustmentOpen", "open", "Open")


def price_close(row: dict[str, Any]) -> float | None:
    return first_number(row, "adjusted_close", "AdjustmentClose", "close", "Close")


def tradable_flag(row: dict[str, Any]) -> bool:
    parsed = parse_bool(first_text(row, "tradable_flag", "TradableFlag"))
    if parsed is not None:
        return parsed
    return True


def build_price_index(rows: list[dict[str, str]]) -> dict[str, dict[date, PricePoint]]:
    output: dict[str, dict[date, PricePoint]] = defaultdict(dict)
    seen: set[tuple[str, date]] = set()
    for row in rows:
        code = price_code(row)
        row_date = price_date(row)
        if not code or row_date is None:
            continue
        key = (code, row_date)
        if key in seen:
            raise ValueError(f"Duplicate price row for code/date: {code} {row_date.isoformat()}")
        seen.add(key)
        output[code][row_date] = PricePoint(
            date=row_date,
            open=price_open(row),
            close=price_close(row),
            tradable=tradable_flag(row),
        )
    return output


def price_calendar_from_rows(rows: list[dict[str, str]]) -> list[date]:
    values = {value for row in rows if (value := price_date(row)) is not None}
    return sorted(values)


def parse_event_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise ValueError(f"Invalid announcement_datetime: {value!r}")


def event_datetime(row: dict[str, str]) -> datetime | None:
    text = first_text(row, "announcement_datetime")
    if text:
        return parse_event_datetime(text)
    event_date = first_text(row, "announcement_date", "available_date", "DisclosedDate")
    event_time = first_text(row, "announcement_time", "available_time", "DisclosedTime")
    if not event_date:
        return None
    return parse_event_datetime(f"{event_date} {event_time}".strip())


def event_id(row: dict[str, str], index: int) -> str:
    return first_text(row, "event_id") or f"event_{index:06d}"


def price_for_mode(point: PricePoint | None, mode: str) -> float | None:
    if point is None or not point.tradable:
        return None
    if mode in {"next_open", "open"}:
        return point.open
    if mode in {"next_close", "close"}:
        return point.close
    raise ValueError(f"Unsupported price mode: {mode}")


def plan_events(
    rows: list[dict[str, str]],
    calendar: list[date],
    *,
    entry_lag_trading_days: int,
    holding_trading_days: int,
) -> list[PlannedEvent]:
    planned: list[PlannedEvent] = []
    seen_event_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        announcement = event_datetime(row)
        code = first_text(row, "code", "Code", "LocalCode")
        if announcement is None or not code:
            continue
        planned_event_id = event_id(row, index)
        if planned_event_id in seen_event_ids:
            raise ValueError(f"Duplicate event_id in event panel: {planned_event_id}")
        seen_event_ids.add(planned_event_id)
        entry_date = trading_day_offset(
            calendar,
            announcement.date(),
            entry_lag_trading_days - 1,
            mode="after",
        )
        exit_date = (
            trading_day_offset(calendar, entry_date, holding_trading_days - 1, mode="after")
            if entry_date
            else None
        )
        planned.append(
            PlannedEvent(
                event_id=planned_event_id,
                code=code,
                announcement=announcement,
                event_label=first_text(row, "event_label"),
                entry_date=entry_date,
                exit_date=exit_date,
                row=row,
            )
        )
    planned.sort(key=lambda item: (item.entry_date or date.max, item.announcement, item.event_id))
    return planned


def mark_value(positions: dict[str, Position], prices: dict[str, dict[date, PricePoint]], value_date: date) -> float:
    total = 0.0
    for position in positions.values():
        point = prices.get(position.code, {}).get(value_date)
        value = price_for_mode(point, "close")
        if value is None:
            value = position.entry_price
        total += position.shares * value
    return total


def commission(value: float, bps: float) -> float:
    return value * bps / 10_000.0


def failure_row(event: PlannedEvent, failure_date: date | None, failure_type: str, detail: str) -> dict[str, str]:
    return {
        "event_id": event.event_id,
        "code": event.code,
        "failure_date": failure_date.isoformat() if failure_date else "",
        "failure_type": failure_type,
        "detail": detail,
        "announcement_datetime": event.announcement.isoformat(sep=" "),
        "entry_date": event.entry_date.isoformat() if event.entry_date else "",
        "exit_date": event.exit_date.isoformat() if event.exit_date else "",
    }


def run_simulation(
    event_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    *,
    run_label: str,
    initial_capital: float,
    target_event_weight: float,
    max_concurrent_positions: int,
    lot_size: int,
    commission_bps: float,
    tax_rate: float,
    entry_lag_trading_days: int,
    entry_price_mode: str,
    holding_trading_days: int,
    exit_price_mode: str,
) -> dict[str, list[dict[str, str]]]:
    if initial_capital <= 0:
        raise ValueError("--initial-capital must be positive.")
    if not (0 < target_event_weight <= 1):
        raise ValueError("--target-event-weight must be in (0, 1].")
    if max_concurrent_positions <= 0:
        raise ValueError("--max-concurrent-positions must be positive.")
    if lot_size <= 0:
        raise ValueError("--lot-size must be positive.")
    if entry_lag_trading_days < 1:
        raise ValueError("--entry-lag-trading-days must be at least 1 for Standard daily-bar event simulation.")
    if holding_trading_days <= 0:
        raise ValueError("--holding-trading-days must be positive.")
    if commission_bps < 0:
        raise ValueError("--commission-bps cannot be negative.")
    if tax_rate < 0:
        raise ValueError("--tax-rate cannot be negative.")

    prices = build_price_index(price_rows)
    calendar = price_calendar_from_rows(price_rows)
    if price_rows and not calendar:
        raise ValueError("prices has rows but no valid date/Date values for the trading calendar.")
    events = plan_events(
        event_rows,
        calendar,
        entry_lag_trading_days=entry_lag_trading_days,
        holding_trading_days=holding_trading_days,
    )
    entries_by_date: dict[date, list[PlannedEvent]] = defaultdict(list)
    for event in events:
        if event.entry_date is not None:
            entries_by_date[event.entry_date].append(event)

    if events:
        first_date = min(event.announcement.date() for event in events)
        last_dates = [event.exit_date for event in events if event.exit_date is not None]
        last_date = max(last_dates) if last_dates else max(calendar) if calendar else first_date
        simulation_calendar = [value for value in calendar if first_date <= value <= last_date]
    else:
        simulation_calendar = []

    cash = initial_capital
    cumulative_commission = 0.0
    cumulative_tax = 0.0
    positions: dict[str, Position] = {}
    open_code_index: set[str] = set()
    trade_rows: list[dict[str, str]] = []
    position_rows: list[dict[str, str]] = []
    equity_rows: list[dict[str, str]] = []
    failure_rows: list[dict[str, str]] = []

    for event in events:
        if event.entry_date is None:
            failure_rows.append(failure_row(event, None, "missing_entry_date", "No trading day after announcement."))
        elif event.exit_date is None:
            failure_rows.append(failure_row(event, event.entry_date, "insufficient_exit_window", "No exit date for holding window."))

    for current_date in simulation_calendar:
        exits = [position for position in positions.values() if position.exit_date == current_date]
        for position in sorted(exits, key=lambda item: item.event_id):
            point = prices.get(position.code, {}).get(current_date)
            exit_price = price_for_mode(point, exit_price_mode)
            synthetic_event = PlannedEvent(
                event_id=position.event_id,
                code=position.code,
                announcement=position.announcement,
                event_label=position.event_label,
                entry_date=position.entry_date,
                exit_date=position.exit_date,
                row={},
            )
            if exit_price is None:
                failure_rows.append(failure_row(synthetic_event, current_date, "missing_exit_price", f"No {exit_price_mode} price."))
                continue
            gross_exit = position.shares * exit_price
            sell_commission = commission(gross_exit, commission_bps)
            realized_gain = gross_exit - position.gross_entry_value
            tax = max(realized_gain, 0.0) * tax_rate
            cash += gross_exit - sell_commission - tax
            cumulative_commission += sell_commission
            cumulative_tax += tax
            trade_rows.append(
                {
                    "event_id": position.event_id,
                    "code": position.code,
                    "side": "SELL",
                    "signal_datetime": position.announcement.isoformat(sep=" "),
                    "execution_date": current_date.isoformat(),
                    "execution_price_mode": exit_price_mode,
                    "price": fmt(exit_price),
                    "shares": str(position.shares),
                    "gross_value": fmt(gross_exit),
                    "commission": fmt(sell_commission),
                    "realized_gain": fmt(realized_gain),
                    "estimated_tax": fmt(tax),
                    "cash_after": fmt(cash),
                }
            )
            position_rows.append(
                {
                    "event_id": position.event_id,
                    "code": position.code,
                    "event_label": position.event_label,
                    "announcement_datetime": position.announcement.isoformat(sep=" "),
                    "entry_date": position.entry_date.isoformat(),
                    "entry_price_mode": entry_price_mode,
                    "entry_price": fmt(position.entry_price),
                    "exit_date": current_date.isoformat(),
                    "exit_price_mode": exit_price_mode,
                    "exit_price": fmt(exit_price),
                    "shares": str(position.shares),
                    "gross_entry_value": fmt(position.gross_entry_value),
                    "gross_exit_value": fmt(gross_exit),
                    "gross_return": fmt(gross_exit / position.gross_entry_value - 1.0 if position.gross_entry_value else None),
                    "net_pnl_after_cost_tax": fmt(gross_exit - position.gross_entry_value - position.entry_commission - sell_commission - tax),
                    "status": "closed",
                }
            )
            del positions[position.event_id]
            open_code_index.discard(position.code)

        for event in sorted(entries_by_date.get(current_date, []), key=lambda item: (item.announcement, item.event_id)):
            if event.exit_date is None:
                continue
            if len(positions) >= max_concurrent_positions:
                failure_rows.append(failure_row(event, current_date, "max_concurrent_positions", "Position limit reached."))
                continue
            if event.code in open_code_index:
                failure_rows.append(failure_row(event, current_date, "duplicate_open_position", "Code already has an open event position."))
                continue
            point = prices.get(event.code, {}).get(current_date)
            entry_price = price_for_mode(point, entry_price_mode)
            if entry_price is None:
                failure_rows.append(failure_row(event, current_date, "missing_entry_price", f"No {entry_price_mode} price."))
                continue
            current_equity = cash + mark_value(positions, prices, current_date)
            target_notional = current_equity * target_event_weight
            affordable_notional = min(target_notional, cash / (1.0 + commission_bps / 10_000.0))
            shares = floor(affordable_notional / entry_price / lot_size) * lot_size
            if shares <= 0:
                failure_rows.append(failure_row(event, current_date, "zero_lot", "Target notional cannot buy one lot."))
                continue
            gross_entry = shares * entry_price
            buy_commission = commission(gross_entry, commission_bps)
            total_cash_required = gross_entry + buy_commission
            if total_cash_required > cash + 1e-9:
                failure_rows.append(failure_row(event, current_date, "insufficient_cash", "Cash cannot cover entry value plus commission."))
                continue
            cash -= total_cash_required
            cumulative_commission += buy_commission
            positions[event.event_id] = Position(
                event_id=event.event_id,
                code=event.code,
                event_label=event.event_label,
                announcement=event.announcement,
                entry_date=current_date,
                exit_date=event.exit_date,
                entry_price=entry_price,
                shares=shares,
                gross_entry_value=gross_entry,
                entry_commission=buy_commission,
            )
            open_code_index.add(event.code)
            trade_rows.append(
                {
                    "event_id": event.event_id,
                    "code": event.code,
                    "side": "BUY",
                    "signal_datetime": event.announcement.isoformat(sep=" "),
                    "execution_date": current_date.isoformat(),
                    "execution_price_mode": entry_price_mode,
                    "price": fmt(entry_price),
                    "shares": str(shares),
                    "gross_value": fmt(gross_entry),
                    "commission": fmt(buy_commission),
                    "realized_gain": "0",
                    "estimated_tax": "0",
                    "cash_after": fmt(cash),
                }
            )

        gross_exposure = mark_value(positions, prices, current_date)
        equity = cash + gross_exposure
        equity_rows.append(
            {
                "date": current_date.isoformat(),
                "cash": fmt(cash),
                "gross_exposure": fmt(gross_exposure),
                "equity": fmt(equity),
                "open_positions": str(len(positions)),
                "cumulative_commission": fmt(cumulative_commission),
                "cumulative_tax": fmt(cumulative_tax),
            }
        )

    if simulation_calendar:
        final_mark_date = simulation_calendar[-1]
        final_gross = mark_value(positions, prices, final_mark_date)
    else:
        final_gross = 0.0
    final_equity = cash + final_gross
    entered_event_ids = {row["event_id"] for row in trade_rows if row["side"] == "BUY"}
    failed_event_ids = {row["event_id"] for row in failure_rows}
    summary_rows = [
        {
            "run_label": run_label,
            "initial_capital": fmt(initial_capital),
            "final_equity": fmt(final_equity),
            "total_return": fmt(final_equity / initial_capital - 1.0),
            "cash": fmt(cash),
            "open_positions": str(len(positions)),
            "closed_positions": str(len(position_rows)),
            "event_count": str(len(events)),
            "entered_event_count": str(len(entered_event_ids)),
            "skipped_event_count": str(len(failed_event_ids - entered_event_ids)),
            "trade_count": str(len(trade_rows)),
            "cumulative_commission": fmt(cumulative_commission),
            "cumulative_tax": fmt(cumulative_tax),
            "entry_lag_trading_days": str(entry_lag_trading_days),
            "entry_price_mode": entry_price_mode,
            "exit_price_mode": exit_price_mode,
            "holding_trading_days": str(holding_trading_days),
            "target_event_weight": fmt(target_event_weight),
            "max_concurrent_positions": str(max_concurrent_positions),
        }
    ]
    return {
        "summary": summary_rows,
        "trades": trade_rows,
        "positions": position_rows,
        "equity": equity_rows,
        "failures": failure_rows,
    }


def write_outputs(rows_by_name: dict[str, list[dict[str, str]]], args: argparse.Namespace) -> dict[str, Path]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    token = args.run_label
    outputs = {
        "summary": args.out_dir / f"event_account_summary_{token}.{args.output_format}",
        "trades": args.out_dir / f"event_account_trades_{token}.{args.output_format}",
        "positions": args.out_dir / f"event_account_positions_{token}.{args.output_format}",
        "equity": args.out_dir / f"event_account_equity_{token}.{args.output_format}",
        "failures": args.out_dir / f"event_account_failure_cases_{token}.{args.output_format}",
    }
    fieldnames = {
        "summary": SUMMARY_FIELDS,
        "trades": TRADE_FIELDS,
        "positions": POSITION_FIELDS,
        "equity": EQUITY_FIELDS,
        "failures": FAILURE_FIELDS,
    }
    for name, path in outputs.items():
        write_table(rows_by_name[name], path, format=args.output_format, fieldnames=fieldnames[name])
    return outputs


def output_date_range(rows_by_name: dict[str, list[dict[str, str]]], run_label: str) -> str:
    dates = [row["date"] for row in rows_by_name.get("equity", []) if row.get("date")]
    if not dates:
        return run_label
    return f"{min(dates)}..{max(dates)}"


def main() -> int:
    args = build_parser().parse_args()
    rows = run_simulation(
        read_csv(args.events),
        read_csv(args.prices),
        run_label=args.run_label,
        initial_capital=args.initial_capital,
        target_event_weight=args.target_event_weight,
        max_concurrent_positions=args.max_concurrent_positions,
        lot_size=args.lot_size,
        commission_bps=args.commission_bps,
        tax_rate=args.tax_rate,
        entry_lag_trading_days=args.entry_lag_trading_days,
        entry_price_mode=args.entry_price_mode,
        holding_trading_days=args.holding_trading_days,
        exit_price_mode=args.exit_price_mode,
    )
    outputs = write_outputs(rows, args)
    if not args.no_manifest:
        date_range = output_date_range(rows, args.run_label)
        for name, path in outputs.items():
            append_manifest(
                args.manifest,
                source=f"derived_event_account_{name}",
                file_path=path,
                vendor="local",
                schema_version=f"event_account_{name}_v0_1",
                date_range=date_range,
                notes=(
                    f"Standard daily-bar event account simulation; entry_lag={args.entry_lag_trading_days}; "
                    f"entry={args.entry_price_mode}; holding_days={args.holding_trading_days}"
                ),
            )
    print(f"Wrote event account simulation outputs to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
