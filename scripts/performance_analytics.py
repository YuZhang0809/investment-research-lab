from __future__ import annotations

import math
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean, stdev
from typing import Any
from xml.sax.saxutils import escape

from research_common import parse_date, parse_float


METRIC_FIELDS = ["category", "metric", "value", "formatted_value"]


def periods_per_year(frequency: str) -> float:
    normalized = (frequency or "").strip().lower()
    if normalized == "daily":
        return 252.0
    if normalized == "weekly":
        return 52.0
    if normalized == "monthly":
        return 12.0
    if normalized == "quarterly":
        return 4.0
    if normalized == "yearly":
        return 1.0
    return 1.0


def clean(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None and math.isfinite(value)]


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value * 100:.2f}%"


def number(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.{digits}f}"


def money(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"JPY {value:,.0f}"


def metric_row(category: str, metric: str, value: Any, formatted_value: str | None = None) -> dict[str, str]:
    if isinstance(value, float):
        raw = f"{value:.10g}"
    elif value is None:
        raw = ""
    else:
        raw = str(value)
    return {
        "category": category,
        "metric": metric,
        "value": raw,
        "formatted_value": formatted_value if formatted_value is not None else raw,
    }


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def sample_std(values: list[float]) -> float | None:
    return stdev(values) if len(values) > 1 else None


def infer_initial_capital(rows: list[dict[str, str]], equity_column: str = "portfolio_equity_after_cost") -> float | None:
    if not rows:
        return None
    first = rows[0]
    for column in ["portfolio_equity_pre", "capital_jpy"]:
        value = parse_float(first.get(column))
        if value is not None and value > 0:
            return value
    first_equity = parse_float(first.get(equity_column))
    first_return = parse_float(first.get("portfolio_return_after_cost"))
    if first_equity is not None and first_equity > 0 and first_return is not None and first_return > -1:
        return first_equity / (1 + first_return)
    return first_equity


def equity_series(rows: list[dict[str, str]], column: str) -> list[tuple[date, float]]:
    values: list[tuple[date, float]] = []
    for row in rows:
        row_date = parse_date(row.get("rebalance_date") or row.get("date"), field_name="performance.date")
        value = parse_float(row.get(column))
        if row_date is not None and value is not None and value > 0:
            values.append((row_date, value))
    return values


def period_returns(
    rows: list[dict[str, str]],
    *,
    equity_column: str,
    initial_capital: float | None,
    return_column: str | None = None,
) -> list[tuple[date, float]]:
    values: list[tuple[date, float]] = []
    previous = initial_capital
    for row in rows:
        row_date = parse_date(row.get("rebalance_date") or row.get("date"), field_name="performance.date")
        if row_date is None:
            continue
        if return_column:
            explicit = parse_float(row.get(return_column))
            if explicit is not None:
                values.append((row_date, explicit))
                equity = parse_float(row.get(equity_column))
                if equity is not None and equity > 0:
                    previous = equity
                continue
        equity = parse_float(row.get(equity_column))
        if equity is None or equity <= 0 or previous is None or previous <= 0:
            if equity is not None and equity > 0:
                previous = equity
            continue
        values.append((row_date, equity / previous - 1.0))
        previous = equity
    return values


def cumulative_return(equity_values: list[tuple[date, float]], initial_capital: float | None) -> float | None:
    if not equity_values or initial_capital is None or initial_capital <= 0:
        return None
    return equity_values[-1][1] / initial_capital - 1.0


def annualized_return(total_return: float | None, periods: int, annualization: float) -> float | None:
    if total_return is None or periods <= 0 or total_return <= -1:
        return None
    return (1 + total_return) ** (annualization / periods) - 1


def annualized_volatility(returns: list[float], annualization: float) -> float | None:
    deviation = sample_std(returns)
    if deviation is None:
        return None
    return deviation * math.sqrt(annualization)


def sharpe_ratio(returns: list[float], annualization: float) -> float | None:
    deviation = sample_std(returns)
    if deviation is None or deviation <= 0:
        return None
    return (mean(returns) / deviation) * math.sqrt(annualization)


def downside_deviation(returns: list[float], annualization: float) -> float | None:
    if not returns:
        return None
    downside = [min(value, 0.0) for value in returns]
    return math.sqrt(sum(value * value for value in downside) / len(downside)) * math.sqrt(annualization)


def sortino_ratio(returns: list[float], annualization: float) -> float | None:
    downside = downside_deviation(returns, annualization)
    if downside is None or downside <= 0:
        return None
    return mean(returns) * annualization / downside


def drawdown_series(equity_values: list[tuple[date, float]]) -> list[tuple[date, float]]:
    peak = None
    values: list[tuple[date, float]] = []
    for row_date, value in equity_values:
        peak = value if peak is None else max(peak, value)
        drawdown = value / peak - 1.0 if peak and peak > 0 else 0.0
        values.append((row_date, drawdown))
    return values


def max_drawdown(equity_values: list[tuple[date, float]]) -> float | None:
    drawdowns = [value for _date, value in drawdown_series(equity_values)]
    return min(drawdowns) if drawdowns else None


def longest_drawdown_periods(equity_values: list[tuple[date, float]]) -> int:
    peak = None
    current = 0
    longest = 0
    for _row_date, value in equity_values:
        if peak is None or value >= peak:
            peak = value
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def calmar_ratio(total_return: float | None, periods: int, annualization: float, max_dd: float | None) -> float | None:
    ann_return = annualized_return(total_return, periods, annualization)
    if ann_return is None or max_dd is None or max_dd >= 0:
        return None
    return ann_return / abs(max_dd)


def paired_returns(
    portfolio_returns: list[tuple[date, float]],
    benchmark_returns: list[tuple[date, float]],
) -> list[tuple[float, float]]:
    benchmark_by_date = {row_date: value for row_date, value in benchmark_returns}
    return [(value, benchmark_by_date[row_date]) for row_date, value in portfolio_returns if row_date in benchmark_by_date]


def covariance(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = mean(xs)
    mean_y = mean(ys)
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / (len(xs) - 1)


def correlation(xs: list[float], ys: list[float]) -> float | None:
    cov = covariance(xs, ys)
    std_x = sample_std(xs)
    std_y = sample_std(ys)
    if cov is None or std_x is None or std_y is None or std_x <= 0 or std_y <= 0:
        return None
    return cov / (std_x * std_y)


def relative_metrics(
    portfolio_returns: list[tuple[date, float]],
    benchmark_returns: list[tuple[date, float]],
    annualization: float,
) -> dict[str, float | None]:
    pairs = paired_returns(portfolio_returns, benchmark_returns)
    if len(pairs) < 2:
        return {
            "beta": None,
            "alpha": None,
            "tracking_error": None,
            "information_ratio": None,
            "correlation": None,
            "up_capture": None,
            "down_capture": None,
        }
    portfolio = [item[0] for item in pairs]
    benchmark = [item[1] for item in pairs]
    active = [left - right for left, right in pairs]
    variance = sample_std(benchmark)
    variance = variance * variance if variance is not None else None
    beta = None
    if variance is not None and variance > 0:
        cov = covariance(portfolio, benchmark)
        beta = cov / variance if cov is not None else None
    alpha = None if beta is None else (mean(portfolio) - beta * mean(benchmark)) * annualization
    active_std = sample_std(active)
    tracking_error = active_std * math.sqrt(annualization) if active_std is not None else None
    information_ratio = None
    if tracking_error is not None and tracking_error > 0:
        information_ratio = mean(active) * annualization / tracking_error
    up_pairs = [(left, right) for left, right in pairs if right > 0]
    down_pairs = [(left, right) for left, right in pairs if right < 0]
    up_capture = capture_ratio(up_pairs)
    down_capture = capture_ratio(down_pairs)
    return {
        "beta": beta,
        "alpha": alpha,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "correlation": correlation(portfolio, benchmark),
        "up_capture": up_capture,
        "down_capture": down_capture,
    }


def capture_ratio(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    portfolio_mean = mean([left for left, _right in pairs])
    benchmark_mean = mean([right for _left, right in pairs])
    if benchmark_mean == 0:
        return None
    return portfolio_mean / benchmark_mean


def failure_counts(failure_rows: list[dict[str, str]]) -> Counter[str]:
    return Counter(row.get("failure_type", "") for row in failure_rows if row.get("failure_type"))


def summarize_walkforward(
    summary_rows: list[dict[str, str]],
    failure_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if not summary_rows:
        raise ValueError("summary_rows is empty.")
    first = summary_rows[0]
    final = summary_rows[-1]
    frequency = final.get("frequency") or first.get("frequency") or "unknown"
    annualization = periods_per_year(frequency)
    initial_capital = infer_initial_capital(summary_rows)
    portfolio_equity = equity_series(summary_rows, "portfolio_equity_after_cost")
    portfolio_returns = period_returns(
        summary_rows,
        equity_column="portfolio_equity_after_cost",
        initial_capital=initial_capital,
        return_column="portfolio_return_after_cost",
    )
    portfolio_return_values = [value for _date, value in portfolio_returns]
    total = cumulative_return(portfolio_equity, initial_capital)
    max_dd = max_drawdown(portfolio_equity)
    benchmark_equity = equity_series(summary_rows, "benchmark_equity")
    benchmark_returns = period_returns(
        summary_rows,
        equity_column="benchmark_equity",
        initial_capital=initial_capital,
    )
    market_equity = equity_series(summary_rows, "market_benchmark_equity")
    market_returns = period_returns(
        summary_rows,
        equity_column="market_benchmark_equity",
        initial_capital=initial_capital,
        return_column="market_benchmark_return",
    )
    benchmark_label = "market_benchmark" if market_returns else "filtered_universe_benchmark"
    selected_benchmark_returns = market_returns or benchmark_returns
    relative = relative_metrics(portfolio_returns, selected_benchmark_returns, annualization)
    final_benchmark_equity = (market_equity or benchmark_equity)[-1][1] if (market_equity or benchmark_equity) else None
    benchmark_total = (
        final_benchmark_equity / initial_capital - 1.0
        if final_benchmark_equity is not None and initial_capital is not None and initial_capital > 0
        else None
    )
    cost = parse_float(final.get("cumulative_cost_base"))
    tax = parse_float(final.get("cumulative_tax"))
    rows = {
        "period_start": first.get("rebalance_date", ""),
        "period_end": final.get("rebalance_date", ""),
        "frequency": frequency,
        "annualization": annualization,
        "period_count": len(summary_rows),
        "initial_capital": initial_capital,
        "final_equity": portfolio_equity[-1][1] if portfolio_equity else None,
        "total_return": total,
        "annualized_return": annualized_return(total, len(portfolio_returns), annualization),
        "annualized_volatility": annualized_volatility(portfolio_return_values, annualization),
        "sharpe_ratio": sharpe_ratio(portfolio_return_values, annualization),
        "sortino_ratio": sortino_ratio(portfolio_return_values, annualization),
        "max_drawdown": max_dd,
        "calmar_ratio": calmar_ratio(total, len(portfolio_returns), annualization, max_dd),
        "longest_drawdown_periods": longest_drawdown_periods(portfolio_equity),
        "win_rate": average([1.0 if value > 0 else 0.0 for value in portfolio_return_values]),
        "best_period_return": max(portfolio_return_values) if portfolio_return_values else None,
        "worst_period_return": min(portfolio_return_values) if portfolio_return_values else None,
        "benchmark_label": benchmark_label,
        "benchmark_total_return": benchmark_total,
        "active_total_return": (1 + total) / (1 + benchmark_total) - 1.0
        if total is not None and benchmark_total is not None and benchmark_total > -1
        else None,
        "beta": relative["beta"],
        "alpha": relative["alpha"],
        "tracking_error": relative["tracking_error"],
        "information_ratio": relative["information_ratio"],
        "correlation": relative["correlation"],
        "up_capture": relative["up_capture"],
        "down_capture": relative["down_capture"],
        "avg_cash_pct": average(clean([parse_float(row.get("cash_pct")) for row in summary_rows])),
        "avg_turnover": average(clean([parse_float(row.get("turnover")) for row in summary_rows])),
        "avg_holdings": average(clean([parse_float(row.get("holdings_count")) for row in summary_rows])),
        "avg_zero_lot_targets": average(clean([parse_float(row.get("zero_lot_targets")) for row in summary_rows])),
        "avg_skipped_orders": average(clean([parse_float(row.get("skipped_orders")) for row in summary_rows])),
        "cost_drag": cost / initial_capital if cost is not None and initial_capital else None,
        "tax_drag": tax / initial_capital if tax is not None and initial_capital else None,
        "lifecycle_data_status": final.get("lifecycle_data_status", ""),
        "performance_conclusion_allowed": final.get("performance_conclusion_allowed", ""),
        "failure_counts": failure_counts(failure_rows or []),
        "portfolio_equity": portfolio_equity,
        "benchmark_equity": market_equity or benchmark_equity,
        "portfolio_returns": portfolio_returns,
        "benchmark_returns": selected_benchmark_returns,
        "drawdowns": drawdown_series(portfolio_equity),
    }
    return rows


def metric_rows(summary: dict[str, Any]) -> list[dict[str, str]]:
    values = [
        metric_row("run", "period_start", summary["period_start"]),
        metric_row("run", "period_end", summary["period_end"]),
        metric_row("run", "frequency", summary["frequency"]),
        metric_row("run", "period_count", summary["period_count"]),
        metric_row("run", "initial_capital", summary["initial_capital"], money(summary["initial_capital"])),
        metric_row("run", "final_equity", summary["final_equity"], money(summary["final_equity"])),
        metric_row("performance", "total_return", summary["total_return"], pct(summary["total_return"])),
        metric_row("performance", "annualized_return", summary["annualized_return"], pct(summary["annualized_return"])),
        metric_row("risk", "annualized_volatility", summary["annualized_volatility"], pct(summary["annualized_volatility"])),
        metric_row("risk", "max_drawdown", summary["max_drawdown"], pct(summary["max_drawdown"])),
        metric_row("risk", "longest_drawdown_periods", summary["longest_drawdown_periods"]),
        metric_row("risk_adjusted", "sharpe_ratio", summary["sharpe_ratio"], number(summary["sharpe_ratio"])),
        metric_row("risk_adjusted", "sortino_ratio", summary["sortino_ratio"], number(summary["sortino_ratio"])),
        metric_row("risk_adjusted", "calmar_ratio", summary["calmar_ratio"], number(summary["calmar_ratio"])),
        metric_row("performance", "win_rate", summary["win_rate"], pct(summary["win_rate"])),
        metric_row("performance", "best_period_return", summary["best_period_return"], pct(summary["best_period_return"])),
        metric_row("performance", "worst_period_return", summary["worst_period_return"], pct(summary["worst_period_return"])),
        metric_row("benchmark", "benchmark_label", summary["benchmark_label"]),
        metric_row("benchmark", "benchmark_total_return", summary["benchmark_total_return"], pct(summary["benchmark_total_return"])),
        metric_row("benchmark", "active_total_return", summary["active_total_return"], pct(summary["active_total_return"])),
        metric_row("benchmark", "beta", summary["beta"], number(summary["beta"])),
        metric_row("benchmark", "alpha", summary["alpha"], pct(summary["alpha"])),
        metric_row("benchmark", "tracking_error", summary["tracking_error"], pct(summary["tracking_error"])),
        metric_row("benchmark", "information_ratio", summary["information_ratio"], number(summary["information_ratio"])),
        metric_row("benchmark", "correlation", summary["correlation"], number(summary["correlation"])),
        metric_row("benchmark", "up_capture", summary["up_capture"], pct(summary["up_capture"])),
        metric_row("benchmark", "down_capture", summary["down_capture"], pct(summary["down_capture"])),
        metric_row("implementation", "avg_cash_pct", summary["avg_cash_pct"], pct(summary["avg_cash_pct"])),
        metric_row("implementation", "avg_turnover", summary["avg_turnover"], pct(summary["avg_turnover"])),
        metric_row("implementation", "avg_holdings", summary["avg_holdings"], number(summary["avg_holdings"])),
        metric_row("implementation", "avg_zero_lot_targets", summary["avg_zero_lot_targets"], number(summary["avg_zero_lot_targets"])),
        metric_row("implementation", "avg_skipped_orders", summary["avg_skipped_orders"], number(summary["avg_skipped_orders"])),
        metric_row("implementation", "cost_drag", summary["cost_drag"], pct(summary["cost_drag"])),
        metric_row("implementation", "tax_drag", summary["tax_drag"], pct(summary["tax_drag"])),
        metric_row("data", "lifecycle_data_status", summary["lifecycle_data_status"]),
        metric_row("data", "performance_conclusion_allowed", summary["performance_conclusion_allowed"]),
    ]
    for name, count in summary["failure_counts"].most_common():
        values.append(metric_row("failure_case", name, count))
    return values


def write_svg_line_chart(
    path: Path,
    series: list[tuple[str, list[tuple[date, float]], str]],
    *,
    title: str,
    value_format: str = "number",
) -> None:
    width = 900
    height = 360
    margin_left = 72
    margin_right = 24
    margin_top = 48
    margin_bottom = 56
    points = [(row_date, value) for _label, rows, _color in series for row_date, value in rows]
    if not points:
        path.write_text("", encoding="utf-8")
        return
    dates = sorted({row_date for row_date, _value in points})
    values = [value for _row_date, value in points]
    min_value = min(values)
    max_value = max(values)
    if min_value == max_value:
        min_value *= 0.95
        max_value *= 1.05
        if min_value == max_value:
            min_value -= 1
            max_value += 1
    x_by_date = {
        row_date: margin_left
        + index * (width - margin_left - margin_right) / max(len(dates) - 1, 1)
        for index, row_date in enumerate(dates)
    }

    def y(value: float) -> float:
        return margin_top + (max_value - value) * (height - margin_top - margin_bottom) / (max_value - min_value)

    def fmt_axis(value: float) -> str:
        return pct(value) if value_format == "pct" else f"{value:,.0f}"

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{margin_left}" y="28" font-family="Arial" font-size="18" font-weight="700">{escape(title)}</text>',
    ]
    for fraction in [0, 0.25, 0.5, 0.75, 1.0]:
        value = max_value - fraction * (max_value - min_value)
        yy = y(value)
        lines.append(f'<line x1="{margin_left}" y1="{yy:.2f}" x2="{width - margin_right}" y2="{yy:.2f}" stroke="#e5e7eb"/>')
        lines.append(
            f'<text x="{margin_left - 8}" y="{yy + 4:.2f}" text-anchor="end" font-family="Arial" font-size="11" fill="#4b5563">{escape(fmt_axis(value))}</text>'
        )
    for label, rows, color in series:
        if not rows:
            continue
        path_points = " ".join(f"{x_by_date[row_date]:.2f},{y(value):.2f}" for row_date, value in rows)
        lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{path_points}"/>')
    legend_x = margin_left
    legend_y = height - 24
    for label, _rows, color in series:
        lines.append(f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 22}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>')
        lines.append(
            f'<text x="{legend_x + 30}" y="{legend_y + 4}" font-family="Arial" font-size="12" fill="#111827">{escape(label)}</text>'
        )
        legend_x += 190
    lines.append(
        f'<text x="{margin_left}" y="{height - 42}" font-family="Arial" font-size="11" fill="#4b5563">{dates[0].isoformat()} to {dates[-1].isoformat()}</text>'
    )
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
