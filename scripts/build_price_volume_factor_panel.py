from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from research_common import append_manifest, parse_date, read_table, require_pandas, write_table


OPERATOR_VERSION = "price_volume_proxy_v0_1"
PRICE_VOLUME_LOOKBACK_CALENDAR_DAYS = 220
ALPHA_FIELDS = [
    "wq_alpha_005_proxy",
    "wq_alpha_011_proxy",
    "wq_alpha_012_proxy",
    "wq_alpha_024_proxy",
    "wq_alpha_028_proxy",
    "wq_alpha_032_proxy",
    "wq_alpha_033_proxy",
    "wq_alpha_034_proxy",
    "wq_alpha_041_proxy",
    "wq_alpha_042_proxy",
    "wq_alpha_043_proxy",
    "wq_alpha_047_proxy",
    "wq_alpha_053_proxy",
    "wq_alpha_057_proxy",
    "wq_alpha_060_proxy",
    "wq_alpha_083_proxy",
    "wq_alpha_101_proxy",
]
BASE_OUTPUT_FIELDS = [
    "rebalance_date",
    "code",
    "latest_price_date",
    "price_staleness_calendar_days",
    "effective_close",
    "effective_close_source",
    "effective_close_flag",
    "returns",
    "dollar_volume",
    "adv20",
    "adv60",
    "vwap_proxy",
    "intraday_return",
    "range_position",
    "candle_pressure",
    "close_to_vwap",
    "high_low_range",
]
TRAILING_FIELDS = ["missing_flags", "coverage_flags", "vwap_proxy_flag", "operator_version"]
FIELDNAMES = [*BASE_OUTPUT_FIELDS, *ALPHA_FIELDS, *TRAILING_FIELDS]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build generic WQ-style price-volume proxy factor panels.")
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--universe-panel", type=Path, help="Optional rebalance_date+code panel used to restrict output rows.")
    parser.add_argument("--rebalance-dates", type=Path, help="CSV/Parquet with rebalance_date or date column.")
    parser.add_argument("--rebalance-date", action="append", dest="rebalance_date_values", help="YYYY-MM-DD; can be repeated.")
    parser.add_argument("--group-field", help="Optional discrete field to preserve from prices or universe panel. No neutralization in v0.1.")
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["csv", "parquet"], default="parquet")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-label", default="price_volume_factors")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def first_column(frame: Any, aliases: list[str], *, required: bool = False) -> str | None:
    for name in aliases:
        if name in frame.columns:
            return name
    if required:
        raise ValueError(f"Missing required column from aliases: {', '.join(aliases)}")
    return None


def numeric_series(frame: Any, aliases: list[str]):
    pd = require_pandas()
    column = first_column(frame, aliases)
    if column is None:
        return pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Float64")
    values = frame[column].astype(str).str.replace(",", "", regex=False).replace({"": pd.NA, "-": pd.NA})
    return pd.to_numeric(values, errors="coerce")


def coalesced_numeric_series(frame: Any, aliases: list[str]):
    pd = require_pandas()
    result = pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Float64")
    source = pd.Series([""] * len(frame), index=frame.index, dtype="string")
    for alias in aliases:
        if alias not in frame.columns:
            continue
        values = numeric_series(frame, [alias])
        fill = result.isna() & values.notna()
        result.loc[fill] = values.loc[fill]
        source.loc[fill] = alias
    return result, source


def normalize_prices(path: Path, input_format: str = "auto", group_field: str | None = None):
    pd = require_pandas()
    frame = read_table(path, format=input_format)
    code_col = first_column(frame, ["code", "Code", "LocalCode"], required=True)
    date_col = first_column(frame, ["date", "Date", "price_date"], required=True)
    price_limit_col = first_column(frame, ["price_limit_flag", "PriceLimitFlag"])
    effective_close, effective_close_source = coalesced_numeric_series(
        frame,
        ["adjusted_close", "AdjustmentClose", "close", "Close", "unadjusted_close"],
    )
    effective_close_flag = effective_close_source.where(
        effective_close_source.isin(["", "adjusted_close", "AdjustmentClose"]),
        "adjusted_close_fallback_used",
    )
    effective_close_flag = effective_close_flag.where(effective_close.notna(), "effective_close_missing").replace(
        {"adjusted_close": "", "AdjustmentClose": ""}
    )
    output_values: dict[str, Any] = {
        "code": frame[code_col].astype(str).str.strip(),
        "date": pd.to_datetime(frame[date_col], errors="coerce").dt.date,
        "open": numeric_series(frame, ["unadjusted_open", "open", "Open", "AdjustmentOpen", "adjusted_open"]),
        "high": numeric_series(frame, ["unadjusted_high", "high", "High", "AdjustmentHigh", "adjusted_high"]),
        "low": numeric_series(frame, ["unadjusted_low", "low", "Low", "AdjustmentLow", "adjusted_low"]),
        "close": numeric_series(frame, ["unadjusted_close", "close", "Close", "AdjustmentClose", "adjusted_close"]),
        "adjusted_close": effective_close,
        "effective_close": effective_close,
        "effective_close_source": effective_close_source,
        "effective_close_flag": effective_close_flag,
        "volume": numeric_series(frame, ["volume", "Volume", "AdjustmentVolume"]),
        "trading_value": numeric_series(frame, ["trading_value", "TradingValue", "TurnoverValue", "turnover_value"]),
        "price_limit_flag": frame[price_limit_col].astype(str).str.lower() if price_limit_col is not None else "",
    }
    if group_field and group_field in frame.columns:
        output_values[group_field] = frame[group_field].astype(str)
    output = pd.DataFrame(output_values)
    output = output[(output["code"] != "") & output["date"].notna()].copy()
    duplicates = output.duplicated(["code", "date"], keep=False)
    if duplicates.any():
        duplicate = output.loc[duplicates, ["code", "date"]].iloc[0]
        raise ValueError(f"Duplicate price row for code/date: {duplicate['code']} {duplicate['date']}")
    output.sort_values(["code", "date"], inplace=True)
    output.reset_index(drop=True, inplace=True)
    return output


def parse_optional_date(value: Any, field_name: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    return parse_date(text, field_name=field_name)


def load_rebalance_dates(path: Path | None, values: list[str] | None, prices: Any) -> list[date]:
    dates: set[date] = set()
    for value in values or []:
        parsed = parse_optional_date(value, "rebalance_date")
        if parsed is not None:
            dates.add(parsed)
    if path is not None:
        rows = read_table(path, format="auto").to_dict(orient="records")
        for row in rows:
            parsed = parse_optional_date(
                row.get("rebalance_date") or row.get("date") or row.get("Date"),
                "rebalance_dates.rebalance_date",
            )
            if parsed is not None:
                dates.add(parsed)
    if not dates:
        dates = set(prices["date"].dropna().unique())
    return sorted(dates)


def normalize_universe_panel(path: Path, group_field: str | None, input_format: str = "auto"):
    pd = require_pandas()
    frame = read_table(path, format=input_format)
    if "rebalance_date" not in frame.columns or "code" not in frame.columns:
        raise ValueError("--universe-panel must contain rebalance_date and code columns.")
    output = pd.DataFrame(
        {
            "rebalance_date": pd.to_datetime(frame["rebalance_date"], errors="coerce").dt.date,
            "code": frame["code"].astype(str).str.strip(),
        }
    )
    if "included_flag" in frame.columns:
        included = frame["included_flag"].astype(str).str.lower().isin({"1", "true", "yes", "y"})
        output = output[included].copy()
    if group_field and group_field in frame.columns:
        output[group_field] = frame[group_field].astype(str)
    duplicates = output.duplicated(["rebalance_date", "code"], keep=False)
    if duplicates.any():
        duplicate = output.loc[duplicates, ["rebalance_date", "code"]].iloc[0]
        raise ValueError(f"Duplicate universe panel row for rebalance/code: {duplicate['rebalance_date']} {duplicate['code']}")
    return output


def active_rebalance_dates(rebalance_dates: list[date], universe_panel: Any | None = None) -> list[date]:
    if universe_panel is None:
        return rebalance_dates
    values = sorted(value for value in universe_panel["rebalance_date"].dropna().unique())
    return values


def trim_price_history(prices: Any, rebalance_dates: list[date], universe_panel: Any | None = None):
    if not rebalance_dates:
        return prices
    trimmed = prices
    if universe_panel is not None:
        codes = set(universe_panel["code"].dropna().astype(str))
        trimmed = trimmed[trimmed["code"].isin(codes)].copy()
    start = min(rebalance_dates) - timedelta(days=PRICE_VOLUME_LOOKBACK_CALENDAR_DAYS)
    end = max(rebalance_dates)
    return trimmed[(trimmed["date"] >= start) & (trimmed["date"] <= end)].copy()


def delay(frame: Any, field: str, periods: int):
    return frame.groupby("code", sort=False)[field].shift(periods)


def delta(frame: Any, field: str, periods: int):
    return frame[field] - delay(frame, field, periods)


def rolling(frame: Any, field: str, window: int, method: str, *, min_periods: int | None = None):
    grouped = frame.groupby("code", sort=False)[field]
    min_periods = window if min_periods is None else min_periods
    roller = grouped.rolling(window, min_periods=min_periods)
    result = getattr(roller, method)()
    return result.reset_index(level=0, drop=True)


def rolling_arg(frame: Any, field: str, window: int, *, which: str):
    import numpy as np

    def apply(values: Any) -> float:
        clean = np.asarray(values, dtype=float)
        if np.isnan(clean).any():
            return np.nan
        index = np.nanargmax(clean) if which == "max" else np.nanargmin(clean)
        return float(index + 1)

    return (
        frame.groupby("code", sort=False)[field]
        .rolling(window, min_periods=window)
        .apply(apply, raw=True)
        .reset_index(level=0, drop=True)
    )


def ts_rank(frame: Any, field: str, window: int):
    import numpy as np

    def apply(values: Any) -> float:
        clean = [value for value in values if not np.isnan(value)]
        if len(clean) < 2:
            return np.nan
        last = clean[-1]
        less = sum(value < last for value in clean)
        equal = sum(value == last for value in clean)
        rank = less + (equal - 1) / 2
        return rank / (len(clean) - 1)

    return (
        frame.groupby("code", sort=False)[field]
        .rolling(window, min_periods=window)
        .apply(apply, raw=True)
        .reset_index(level=0, drop=True)
    )


def cross_sectional_rank_pct(series: Any, dates: Any):
    pd = require_pandas()
    frame = pd.DataFrame({"date": dates, "value": series})

    def rank_group(values: Any):
        if values.notna().sum() < 2:
            return values * pd.NA
        return (values.rank(method="average") - 1) / (values.notna().sum() - 1)

    return frame.groupby("date", sort=False)["value"].transform(rank_group)


def rolling_corr(frame: Any, left: str, right: str, window: int):
    pd = require_pandas()
    result = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    for _code, group in frame.groupby("code", sort=False):
        result.loc[group.index] = group[left].rolling(window, min_periods=window).corr(group[right])
    return result


def rolling_cov(frame: Any, left: str, right: str, window: int):
    pd = require_pandas()
    result = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    for _code, group in frame.groupby("code", sort=False):
        result.loc[group.index] = group[left].rolling(window, min_periods=window).cov(group[right])
    return result


def safe_divide(numerator: Any, denominator: Any):
    pd = require_pandas()
    result = numerator / denominator.where(denominator != 0)
    return result.replace([float("inf"), float("-inf")], pd.NA)


def sign_series(series: Any):
    pd = require_pandas()
    result = pd.Series(pd.NA, index=series.index, dtype="Float64")
    result.loc[series >= 0] = 1.0
    result.loc[series < 0] = -1.0
    return result


def decay_linear(frame: Any, field: str, window: int):
    import numpy as np

    weights = np.arange(1, window + 1, dtype=float)
    denominator = weights.sum()

    def apply(values: Any) -> float:
        clean = np.asarray(values, dtype=float)
        if np.isnan(clean).any():
            return np.nan
        return float((clean * weights).sum() / denominator)

    return (
        frame.groupby("code", sort=False)[field]
        .rolling(window, min_periods=window)
        .apply(apply, raw=True)
        .reset_index(level=0, drop=True)
    )


def add_daily_features(frame: Any):
    pd = require_pandas()
    frame = frame.copy()
    frame["returns"] = safe_divide(frame["effective_close"], delay(frame, "effective_close", 1)) - 1
    frame["dollar_volume"] = frame["trading_value"].where(frame["trading_value"].notna(), frame["close"] * frame["volume"])
    frame["adv20"] = rolling(frame, "dollar_volume", 20, "mean", min_periods=20)
    frame["adv60"] = rolling(frame, "dollar_volume", 60, "mean", min_periods=60)
    frame["vwap_proxy"] = safe_divide(frame["trading_value"], frame["volume"])
    frame["intraday_return"] = safe_divide(frame["close"], frame["open"]) - 1
    range_denominator = frame["high"] - frame["low"]
    frame["range_position"] = safe_divide(frame["close"] - frame["low"], range_denominator)
    frame["candle_pressure"] = safe_divide(frame["close"] - frame["open"], range_denominator)
    frame["close_to_vwap"] = safe_divide(frame["close"], frame["vwap_proxy"]) - 1
    frame["high_low_range"] = safe_divide(frame["high"], frame["low"]) - 1
    frame["vwap_proxy_flag"] = ""
    frame.loc[frame["volume"].isna(), "vwap_proxy_flag"] = "missing_volume"
    frame.loc[frame["volume"] == 0, "vwap_proxy_flag"] = "zero_volume"
    trading_value_missing = frame["trading_value"].isna()
    frame.loc[trading_value_missing & frame["vwap_proxy_flag"].eq(""), "vwap_proxy_flag"] = "missing_trading_value"
    frame["rank_open_minus_vwap"] = cross_sectional_rank_pct(frame["open"] - frame["vwap_proxy"], frame["date"])
    frame["rank_abs_close_minus_vwap"] = cross_sectional_rank_pct((frame["close"] - frame["vwap_proxy"]).abs(), frame["date"])
    frame["rank_vwap_minus_close"] = cross_sectional_rank_pct(frame["vwap_proxy"] - frame["close"], frame["date"])
    frame["rank_vwap_plus_close"] = cross_sectional_rank_pct(frame["vwap_proxy"] + frame["close"], frame["date"])
    frame["rank_neg_returns"] = cross_sectional_rank_pct(-frame["returns"], frame["date"])
    frame["rank_delta_close_1"] = cross_sectional_rank_pct(delta(frame, "close", 1), frame["date"])
    frame["rank_inv_close"] = cross_sectional_rank_pct(safe_divide(1, frame["close"]), frame["date"])
    frame["rank_high_minus_close"] = cross_sectional_rank_pct(frame["high"] - frame["close"], frame["date"])
    frame["rank_candle_volume"] = cross_sectional_rank_pct(frame["candle_pressure"] * frame["volume"], frame["date"])
    frame["ts_max_vwap_close_3"] = rolling(frame.assign(vwap_minus_close=frame["vwap_proxy"] - frame["close"]), "vwap_minus_close", 3, "max", min_periods=3)
    frame["ts_min_vwap_close_3"] = rolling(frame.assign(vwap_minus_close=frame["vwap_proxy"] - frame["close"]), "vwap_minus_close", 3, "min", min_periods=3)
    frame["rank_ts_max_vwap_close_3"] = cross_sectional_rank_pct(frame["ts_max_vwap_close_3"], frame["date"])
    frame["rank_ts_min_vwap_close_3"] = cross_sectional_rank_pct(frame["ts_min_vwap_close_3"], frame["date"])
    frame["rank_delta_volume_3"] = cross_sectional_rank_pct(delta(frame, "volume", 3), frame["date"])
    frame["volume_to_adv20"] = safe_divide(frame["volume"] * frame["close"], frame["adv20"])
    frame["mean_close_20"] = rolling(frame, "close", 20, "mean", min_periods=20)
    frame["std_returns_5"] = rolling(frame, "returns", 5, "std", min_periods=5)
    frame["std_returns_20"] = rolling(frame, "returns", 20, "std", min_periods=20)
    frame["corr_adv_low_5"] = rolling_corr(frame, "adv20", "low", 5)
    frame["corr_vwap_close_delay_20"] = rolling_corr(
        frame.assign(close_delay_5=delay(frame, "close", 5)),
        "vwap_proxy",
        "close_delay_5",
        20,
    )
    frame["argmax_close_10"] = rolling_arg(frame, "close", 10, which="max")
    frame["argmax_close_30"] = rolling_arg(frame, "close", 30, which="max")
    frame["ts_rank_volume_to_adv20_20"] = ts_rank(frame, "volume_to_adv20", 20)
    frame["ts_rank_neg_delta_close_7_8"] = ts_rank(frame.assign(neg_delta_close_7=-delta(frame, "close", 7)), "neg_delta_close_7", 8)
    frame["ts_rank_range_position_5"] = ts_rank(frame, "range_position", 5)
    frame["ts_rank_volume_5"] = ts_rank(frame, "volume", 5)
    frame["ts_rank_vwap_5"] = ts_rank(frame, "vwap_proxy", 5)
    frame["decay_argmax_close_30_2"] = decay_linear(frame.assign(argmax_close_30=frame["argmax_close_30"]), "argmax_close_30", 2)
    frame["rank_argmax_close_10"] = cross_sectional_rank_pct(frame["argmax_close_10"], frame["date"])
    return frame.replace([float("inf"), float("-inf")], pd.NA)


def add_proxy_alphas(frame: Any):
    frame = frame.copy()
    frame["wq_alpha_005_proxy"] = -frame["rank_open_minus_vwap"] * frame["rank_abs_close_minus_vwap"]
    frame["wq_alpha_011_proxy"] = (
        (frame["rank_ts_max_vwap_close_3"] + frame["rank_ts_min_vwap_close_3"]) * frame["rank_delta_volume_3"]
    )
    delta_close_1 = delta(frame, "close", 1)
    delta_volume_1 = delta(frame, "volume", 1)
    frame["wq_alpha_012_proxy"] = -delta_close_1 * sign_series(delta_volume_1)
    frame["wq_alpha_024_proxy"] = -(frame["close"] - frame["mean_close_20"])
    frame["wq_alpha_028_proxy"] = -frame["corr_adv_low_5"] + frame["close_to_vwap"]
    frame["wq_alpha_032_proxy"] = frame["mean_close_20"] - frame["close"] + frame["corr_vwap_close_delay_20"]
    frame["wq_alpha_033_proxy"] = frame["rank_neg_returns"] + frame["candle_pressure"]
    frame["wq_alpha_034_proxy"] = cross_sectional_rank_pct(safe_divide(frame["std_returns_5"], frame["std_returns_20"]), frame["date"]) + frame["rank_delta_close_1"]
    frame["wq_alpha_041_proxy"] = (frame["high"] * frame["low"]).pow(0.5) - frame["vwap_proxy"]
    frame["wq_alpha_042_proxy"] = safe_divide(frame["rank_vwap_minus_close"], frame["rank_vwap_plus_close"])
    frame["wq_alpha_043_proxy"] = frame["ts_rank_volume_to_adv20_20"] * frame["ts_rank_neg_delta_close_7_8"]
    frame["wq_alpha_047_proxy"] = frame["rank_inv_close"] * frame["volume_to_adv20"] * frame["rank_high_minus_close"]
    frame["wq_alpha_053_proxy"] = -delta(frame, "candle_pressure", 9)
    frame["wq_alpha_057_proxy"] = -safe_divide(frame["close"] - frame["vwap_proxy"], frame["decay_argmax_close_30_2"])
    frame["wq_alpha_060_proxy"] = -(2 * frame["rank_candle_volume"] - frame["rank_argmax_close_10"])
    frame["wq_alpha_083_proxy"] = safe_divide(
        frame["ts_rank_range_position_5"] * frame["ts_rank_volume_5"],
        frame["ts_rank_vwap_5"],
    )
    frame["wq_alpha_101_proxy"] = frame["candle_pressure"]
    return frame


def build_base_panel(prices: Any, rebalance_dates: list[date], universe_panel: Any | None = None, group_field: str | None = None):
    pd = require_pandas()
    codes = sorted(prices["code"].dropna().unique())
    if universe_panel is not None:
        left = universe_panel.copy()
    else:
        left = pd.MultiIndex.from_product([rebalance_dates, codes], names=["rebalance_date", "code"]).to_frame(index=False)
    left["rebalance_date"] = pd.to_datetime(left["rebalance_date"])
    right = prices.copy()
    right["date"] = pd.to_datetime(right["date"])
    merged_parts = []
    for code, left_group in left.groupby("code", sort=False):
        right_group = right[right["code"] == code].sort_values("date")
        if right_group.empty:
            missing_group = left_group.copy()
            for column in right.columns:
                if column != "code" and column not in missing_group.columns:
                    if str(right[column].dtype).startswith("datetime64"):
                        missing_group[column] = pd.Series(pd.NaT, index=missing_group.index, dtype=right[column].dtype)
                    elif getattr(right[column].dtype, "kind", "") in {"f", "i", "u"}:
                        missing_group[column] = pd.Series(float("nan"), index=missing_group.index, dtype="float64")
                    else:
                        missing_group[column] = pd.Series(pd.NA, index=missing_group.index, dtype=right[column].dtype)
            merged_parts.append(missing_group)
            continue
        right_for_merge = right_group.drop(columns=["code"])
        if group_field and group_field in left_group.columns and group_field in right_for_merge.columns:
            right_for_merge = right_for_merge.drop(columns=[group_field])
        merged_parts.append(
            pd.merge_asof(
                left_group.sort_values("rebalance_date"),
                right_for_merge,
                left_on="rebalance_date",
                right_on="date",
                direction="backward",
            )
        )
    merged = pd.concat(merged_parts, ignore_index=True) if merged_parts else left.copy()
    if "date" not in merged.columns:
        merged["date"] = pd.NaT
    latest_dates = pd.to_datetime(merged["date"], errors="coerce")
    rebalance_datetimes = pd.to_datetime(merged["rebalance_date"], errors="coerce")
    merged["latest_price_date"] = latest_dates.dt.strftime("%Y-%m-%d").fillna("")
    staleness_days = (rebalance_datetimes - latest_dates).dt.days
    merged["price_staleness_calendar_days"] = staleness_days.astype("Float64")
    merged["rebalance_date"] = rebalance_datetimes.dt.strftime("%Y-%m-%d")
    if group_field and group_field not in merged.columns and group_field in prices.columns:
        group_values = prices[["code", group_field]].drop_duplicates("code")
        merged = merged.merge(group_values, on="code", how="left")
    return merged


def add_flags(frame: Any):
    frame = frame.copy()
    latest_price_missing = frame["latest_price_date"].fillna("").astype(str).isin({"", "NaT"})
    effective_close_missing = frame["effective_close"].isna()
    frame.loc[effective_close_missing & frame["effective_close_flag"].fillna("").eq(""), "effective_close_flag"] = "effective_close_missing"
    effective_close_flags = frame["effective_close_flag"].fillna("").astype(str)
    vwap_flags = frame["vwap_proxy_flag"].fillna("").astype(str)
    alpha_missing = frame[ALPHA_FIELDS].isna()
    price_limit_values = frame["price_limit_flag"].fillna("").astype(str).str.lower()
    price_limit_flags = price_limit_values.isin({"1", "true", "yes", "y"})
    adv20_missing = frame["adv20"].isna()
    adv60_missing = frame["adv60"].isna()

    missing_flags: list[str] = []
    coverage_flags: list[str] = []
    for index, missing_alpha_row in enumerate(alpha_missing.to_numpy()):
        missing = []
        if bool(latest_price_missing.iloc[index]):
            missing.append("missing_price_on_or_before_rebalance")
        effective_close_flag = effective_close_flags.iloc[index]
        if effective_close_flag == "effective_close_missing":
            missing.append(effective_close_flag)
        vwap_flag = vwap_flags.iloc[index]
        if vwap_flag:
            missing.append(vwap_flag)
        missing.extend(field for field, is_missing in zip(ALPHA_FIELDS, missing_alpha_row) if bool(is_missing))
        coverage = []
        if bool(price_limit_flags.iloc[index]):
            coverage.append("price_limit_flag")
        if bool(adv20_missing.iloc[index]):
            coverage.append("insufficient_adv20")
        if bool(adv60_missing.iloc[index]):
            coverage.append("insufficient_adv60")
        if effective_close_flag == "adjusted_close_fallback_used":
            coverage.append(effective_close_flag)
        missing_flags.append(";".join(dict.fromkeys(missing)))
        coverage_flags.append(";".join(dict.fromkeys(coverage)))
    frame["missing_flags"] = missing_flags
    frame["coverage_flags"] = coverage_flags
    frame["operator_version"] = OPERATOR_VERSION
    return frame


def build_panel(
    prices_path: Path,
    *,
    rebalance_dates_path: Path | None = None,
    rebalance_date_values: list[str] | None = None,
    universe_panel_path: Path | None = None,
    group_field: str | None = None,
    input_format: str = "auto",
):
    prices = normalize_prices(prices_path, input_format, group_field=group_field)
    rebalance_dates = load_rebalance_dates(rebalance_dates_path, rebalance_date_values, prices)
    universe_panel = normalize_universe_panel(universe_panel_path, group_field, input_format) if universe_panel_path else None
    output_rebalance_dates = active_rebalance_dates(rebalance_dates, universe_panel)
    prices = trim_price_history(prices, output_rebalance_dates, universe_panel)
    daily = add_proxy_alphas(add_daily_features(prices))
    panel = build_base_panel(daily, rebalance_dates, universe_panel, group_field)
    panel = add_flags(panel)
    pd = require_pandas()
    fields = [*FIELDNAMES]
    if group_field and group_field in panel.columns:
        fields.insert(3, group_field)
    for field in fields:
        if field not in panel.columns:
            panel[field] = pd.NA
    return panel[fields].sort_values(["rebalance_date", "code"]).reset_index(drop=True), fields


def output_date_range(frame: Any) -> str:
    values = sorted(str(value) for value in frame["rebalance_date"].dropna().unique())
    if not values:
        return ""
    return f"{values[0]}..{values[-1]}"


def main() -> int:
    args = build_parser().parse_args()
    panel, fieldnames = build_panel(
        args.prices,
        rebalance_dates_path=args.rebalance_dates,
        rebalance_date_values=args.rebalance_date_values,
        universe_panel_path=args.universe_panel,
        group_field=args.group_field,
        input_format=args.input_format,
    )
    write_table(panel, args.out, format=args.output_format, fieldnames=fieldnames)
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="price_volume_factor_panel",
            file_path=args.out,
            vendor="local",
            schema_version=OPERATOR_VERSION,
            date_range=output_date_range(panel) or args.run_label,
            notes=f"{len(panel)} WQ-style proxy rows; not a full WorldQuant 101 replication",
        )
    print(f"Wrote {len(panel)} price-volume factor rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
