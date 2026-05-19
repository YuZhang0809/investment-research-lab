from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from build_factors import BASE_FACTOR_FIELDS, build_factors, factor_output_fields
from build_rebalance_price_universe_panel import optional_column, register_scan
from build_scores import (
    FACTOR_GROUPS,
    GROUP_SCORE_FIELDS,
    STRATEGY_VERSION_CHOICES,
    build_scores,
    configured_filters,
    configured_group_weights,
    strategy_factor_groups,
)
from duckdb_query import require_duckdb
from research_common import (
    append_manifest,
    checksum,
    format_csv_value,
    load_yaml,
    normalize_row_value,
    parse_bool,
    parse_date,
    read_csv,
    read_table,
    trading_calendar_from_rows,
    write_table,
)
from run_qvm_walkforward import UNIVERSE_CACHE_FIELDS, rebalance_dates, score_cache_fields


PANEL_EXTRA_FIELDS = [
    "included_flag",
    "exclusion_reason",
    "adjusted_close",
    "fundamental_available_date",
    "rank_score",
    "candidate_rank",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a rebalance-level factor/score panel from a price/universe panel.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--price-universe-panel", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="monthly")
    parser.add_argument("--strategy-version", choices=STRATEGY_VERSION_CHOICES, default="qvm")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--engine",
        choices=["legacy", "duckdb"],
        default="legacy",
        help="legacy reuses build_factors/build_scores; duckdb uses the optimized base QVM path.",
    )
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def text_date(value: Any, *, field_name: str) -> str:
    parsed = parse_date(value, field_name=field_name)
    return parsed.isoformat() if parsed else ""


def unique_fields(fields: list[str]) -> list[str]:
    return list(dict.fromkeys(fields))


def pct_label(value: float) -> str:
    return f"{value:g}"


def read_rows(path: Path, input_format: str) -> list[dict[str, str]]:
    if input_format == "auto":
        return read_csv(path)
    frame = read_table(path, format=input_format)
    return [
        {str(key): normalize_row_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def factor_score_panel_fields(config: dict[str, Any], raw_factors: list[str]) -> list[str]:
    return unique_fields(
        [
            "rebalance_date",
            "code",
            *PANEL_EXTRA_FIELDS,
            *UNIVERSE_CACHE_FIELDS,
            *factor_output_fields(config),
            *score_cache_fields(raw_factors),
        ]
    )


def panel_rows_for_date(rows: list[dict[str, str]], rebalance_date: date) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if parse_date(row.get("rebalance_date"), field_name="price_universe_panel.rebalance_date") == rebalance_date
    ]


def included_universe_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    included: list[dict[str, Any]] = []
    for row in rows:
        flag = parse_bool(row.get("included_flag"), default=None)
        if flag is None:
            raise ValueError(f"Invalid included_flag in price/universe panel: {row.get('included_flag')!r}")
        if flag:
            included.append(dict(row))
    return included


def supported_duckdb_raw_factors(config: dict[str, Any], strategy_version: str) -> list[str]:
    if (config.get("factors", {}) or {}).get("definitions"):
        raise ValueError("DuckDB factor/score panel does not support factors.definitions yet. Use --engine legacy.")
    if strategy_version not in {"qvm", "qv", "value_only", "weighted_groups"}:
        raise ValueError(f"DuckDB factor/score panel does not support strategy-version {strategy_version!r}.")
    quality_factors, value_factors, momentum_factors = strategy_factor_groups(config, strategy_version)
    raw_factors = list(dict.fromkeys([*quality_factors, *value_factors, *momentum_factors]))
    unsupported = [factor for factor in raw_factors if factor not in BASE_FACTOR_FIELDS]
    if unsupported:
        raise ValueError(f"DuckDB factor/score panel only supports base QVM factors, got: {', '.join(unsupported)}")
    if strategy_version == "weighted_groups":
        for filter_config in configured_filters(config):
            if filter_config.get("field"):
                raise ValueError("DuckDB factor/score panel supports group filters only. Use --engine legacy for field filters.")
    return raw_factors


def merge_panel_rows(
    *,
    panel_rows: list[dict[str, str]],
    factor_rows: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    factor_by_code = {str(row.get("code", "")): row for row in factor_rows}
    score_by_code = {str(row.get("code", "")): row for row in score_rows}
    rows: list[dict[str, Any]] = []
    for panel_row in sorted(panel_rows, key=lambda item: str(item.get("code", ""))):
        code = str(panel_row.get("code", ""))
        included = parse_bool(panel_row.get("included_flag"), default=None)
        if included is None:
            raise ValueError(f"Invalid included_flag in price/universe panel: {panel_row.get('included_flag')!r}")
        factor = factor_by_code.get(code, {})
        score = score_by_code.get(code, {})
        rows.append(
            {
                **panel_row,
                **factor,
                **score,
                "rebalance_date": text_date(
                    score.get("rebalance_date") or factor.get("rebalance_date") or panel_row.get("rebalance_date"),
                    field_name="factor_score_panel.rebalance_date",
                ),
                "code": code,
                "included_flag": "true" if included else "false",
                "exclusion_reason": "" if included else panel_row.get("exclusion_reason", ""),
                "adjusted_close": panel_row.get("adjusted_close", ""),
                "fundamental_available_date": factor.get("fundamentals_available_date", ""),
                "rank_score": score.get("composite_score", ""),
                "candidate_rank": score.get("rank", ""),
            }
        )
    return rows


def missing_components_sql(group_fields: list[str]) -> str:
    parts = [
        f"case when {field} is null then '{field}' end"
        for field in group_fields
    ]
    return f"concat_ws(';', {', '.join(parts)})" if parts else "''"


def group_score_sql(factors: list[str]) -> str:
    if not factors:
        return "null"
    values = [f"{factor}_z" for factor in factors]
    numerator = " + ".join(f"coalesce({value}, 0)" for value in values)
    denominator = " + ".join(f"case when {value} is not null then 1 else 0 end" for value in values)
    return f"({numerator}) / nullif(({denominator}), 0)"


def zscore_sql(factor: str) -> str:
    clipped = f"{factor}_clipped"
    return (
        f"case "
        f"when {factor} is null then null "
        f"when stddev_pop({clipped}) over (partition by rebalance_date) is null "
        f"  or stddev_pop({clipped}) over (partition by rebalance_date) = 0 then 0 "
        f"else ({clipped} - avg({clipped}) over (partition by rebalance_date)) "
        f"  / stddev_pop({clipped}) over (partition by rebalance_date) "
        f"end as {factor}_z"
    )


def create_raw_panel_table(connection: Any, columns: set[str]) -> None:
    def col(name: str, default: str = "''") -> str:
        return optional_column(columns, name, default)

    def text_col(name: str, default: str = "''") -> str:
        return f"{col(name, default)}::varchar"

    selected_fields = unique_fields(
        [
            *UNIVERSE_CACHE_FIELDS,
            "included_flag",
            "exclusion_reason",
            "adjusted_close",
            "return_12_1",
            "return_6_1",
        ]
    )
    field_sql = []
    date_fields = {
        "source_date",
        "listed_date",
        "delisted_date",
        "last_trading_date",
        "lifecycle_exit_date",
        "latest_price_date",
    }
    bool_fields = {"rebalance_price_available", "latest_price_stale", "has_fundamentals", "tradable_flag", "price_limit_flag"}
    for field in selected_fields:
        if field == "rebalance_date":
            field_sql.append(f"try_cast(nullif({text_col(field)}, '') as date) as rebalance_date")
        elif field in date_fields:
            field_sql.append(f"try_cast(nullif({text_col(field)}, '') as date) as {field}")
        elif field == "included_flag":
            field_sql.append(
                f"""
                case
                  when lower(trim(coalesce({text_col(field)}, '')::varchar)) in ('1', 'true', 't', 'yes', 'y') then 'true'
                  when lower(trim(coalesce({text_col(field)}, '')::varchar)) in ('0', 'false', 'f', 'no', 'n') then 'false'
                  else ''
                end as included_flag
                """
            )
        elif field in bool_fields:
            field_sql.append(
                f"""
                case
                  when lower(trim(coalesce({text_col(field)}, '')::varchar)) in ('1', 'true', 't', 'yes', 'y') then 'True'
                  when lower(trim(coalesce({text_col(field)}, '')::varchar)) in ('0', 'false', 'f', 'no', 'n') then 'False'
                  else ''
                end as {field}
                """
            )
        else:
            field_sql.append(f"coalesce({text_col(field)}, '')::varchar as {field}")

    connection.execute(
        f"""
        create or replace temp table panel_norm as
        select
          {", ".join(field_sql)},
          case
            when lower(trim(coalesce({text_col('included_flag')}, '')::varchar)) in ('1', 'true', 't', 'yes', 'y') then true
            when lower(trim(coalesce({text_col('included_flag')}, '')::varchar)) in ('0', 'false', 'f', 'no', 'n') then false
            else null
          end as included_bool
        from raw_price_universe_panel
        where try_cast(nullif({text_col('rebalance_date')}, '') as date) is not null
          and coalesce({text_col('code')}, '') <> ''
        """
    )
    invalid = connection.execute("select count(*) from panel_norm where included_bool is null").fetchone()[0]
    if invalid:
        raise ValueError("Invalid included_flag in price/universe panel.")


def create_fundamental_table(connection: Any, columns: set[str]) -> None:
    def col(name: str, default: str = "''") -> str:
        return optional_column(columns, name, default)

    def text_col(name: str, default: str = "''") -> str:
        return f"{col(name, default)}::varchar"

    connection.execute(
        f"""
        create or replace temp table fundamentals_norm as
        select
          coalesce({text_col('code')}, '')::varchar as code,
          try_cast(nullif({text_col('available_date')}, '') as date) as available_date,
          coalesce({text_col('available_time')}, '')::varchar as available_time,
          coalesce({text_col('document_type')}, '')::varchar as document_type,
          coalesce({text_col('period_end')}, '')::varchar as period_end,
          coalesce({text_col('disclosure_number')}, '')::varchar as disclosure_number,
          try_cast(nullif({text_col('operating_profit')}, '') as double) as operating_profit,
          try_cast(nullif({text_col('net_profit')}, '') as double) as net_profit,
          try_cast(nullif({text_col('equity')}, '') as double) as equity,
          try_cast(nullif({text_col('total_assets')}, '') as double) as total_assets,
          try_cast(nullif(coalesce(nullif({text_col('shares_outstanding')}, ''), nullif({text_col('avg_shares')}, '')), '') as double) as shares,
          case
            when try_cast(nullif({text_col('operating_profit')}, '') as double) is not null
              or try_cast(nullif({text_col('net_profit')}, '') as double) is not null
              or try_cast(nullif({text_col('equity')}, '') as double) is not null
              or try_cast(nullif({text_col('total_assets')}, '') as double) is not null
              or try_cast(nullif({text_col('shares_outstanding')}, '') as double) is not null
              or try_cast(nullif({text_col('avg_shares')}, '') as double) is not null
            then true
            else false
          end as useful
        from raw_fundamentals
        where coalesce({text_col('code')}, '') <> ''
        """
    )


def apply_duckdb_filters(connection: Any, filters: list[dict[str, Any]]) -> None:
    for filter_config in filters:
        group = str(filter_config.get("group", "") or "")
        if not group:
            raise ValueError("DuckDB factor/score panel supports group filters only. Use --engine legacy for field filters.")
        field = GROUP_SCORE_FIELDS[group]
        label = group
        rule = str(filter_config["rule"])
        connection.execute(
            f"""
            update scored
            set
              filter_status = 'missing_required_score',
              filter_reasons = case when filter_reasons = '' then '{field}' else filter_reasons || ';{field}' end,
              missing_score_components = case
                when missing_score_components = '' then '{field}'
                else missing_score_components || ';{field}'
              end
            where filter_status = 'pass' and {field} is null
            """
        )
        if rule == "require_not_missing":
            continue
        if rule in {"exclude_below", "exclude_above"}:
            threshold = float(filter_config["value"])
            comparator = "<" if rule == "exclude_below" else ">"
            reason = f"{label}_{'below' if rule == 'exclude_below' else 'above'}_{threshold:g}"
            connection.execute(
                f"""
                update scored
                set
                  filter_status = 'filtered',
                  filter_reasons = case when filter_reasons = '' then '{reason}' else filter_reasons || ';{reason}' end
                where filter_status = 'pass' and {field} is not null and {field} {comparator} ?
                """,
                [threshold],
            )
            continue
        if rule in {"exclude_bottom_pct", "exclude_top_pct"}:
            pct = float(filter_config["pct"])
            order = f"{field} asc, code asc" if rule == "exclude_bottom_pct" else f"{field} desc, code asc"
            reason = f"{label}_{'bottom' if rule == 'exclude_bottom_pct' else 'top'}_{pct_label(pct)}pct"
            connection.execute(
                f"""
                update scored
                set
                  filter_status = 'filtered',
                  filter_reasons = case when filter_reasons = '' then '{reason}' else filter_reasons || ';{reason}' end
                from (
                  select rebalance_date, code
                  from (
                    select
                      rebalance_date,
                      code,
                      row_number() over (partition by rebalance_date order by {order}) as rn,
                      count(*) over (partition by rebalance_date) as n
                    from scored
                    where filter_status = 'pass' and {field} is not null
                  )
                  where rn <= ceil(n * ? / 100.0)
                ) selected
                where scored.rebalance_date = selected.rebalance_date
                  and scored.code = selected.code
                """,
                [pct],
            )
            continue
        raise ValueError(f"Unsupported filter rule: {rule}")


def build_duckdb_factor_score_frame(
    *,
    config: dict[str, Any],
    price_universe_panel_path: Path,
    fundamentals_path: Path,
    start_date: date,
    end_date: date,
    frequency: str,
    strategy_version: str,
    input_format: str,
) -> tuple[Any, list[str]]:
    raw_factors = supported_duckdb_raw_factors(config, strategy_version)
    quality_factors, value_factors, momentum_factors = strategy_factor_groups(config, strategy_version)
    factor_config = config.get("factors", {}) or {}
    lower_pct = float((factor_config.get("winsorize", {}) or {}).get("lower_pct", 1))
    upper_pct = float((factor_config.get("winsorize", {}) or {}).get("upper_pct", 99))
    quality_weight = float((factor_config.get("quality", {}) or {}).get("weight", 0.4))
    value_weight = float((factor_config.get("value", {}) or {}).get("weight", 0.4))
    momentum_weight = float((factor_config.get("momentum", {}) or {}).get("weight", 0.2))
    group_weights = configured_group_weights(config) if strategy_version == "weighted_groups" else {}
    filters = configured_filters(config) if strategy_version == "weighted_groups" else []

    duckdb = require_duckdb()
    with duckdb.connect(database=":memory:") as connection:
        panel_columns = register_scan(connection, "raw_price_universe_panel", price_universe_panel_path, input_format)
        fundamental_columns = register_scan(connection, "raw_fundamentals", fundamentals_path, input_format)
        create_raw_panel_table(connection, panel_columns)
        create_fundamental_table(connection, fundamental_columns)

        month_filter = "and month(rebalance_date) in (3, 6, 9, 12)" if frequency == "quarterly" else ""
        connection.execute(
            f"""
            create or replace temp table panel_scope as
            select *
            from panel_norm
            where rebalance_date between ?::date and ?::date
              {month_filter}
            """,
            [start_date.isoformat(), end_date.isoformat()],
        )
        if connection.execute("select count(*) from panel_scope").fetchone()[0] == 0:
            raise ValueError("No price/universe panel rows found for the requested window.")

        connection.execute(
            """
            create or replace temp table selected_fundamentals as
            select *
            from (
              select
                p.rebalance_date,
                p.code as panel_code,
                f.*,
                row_number() over (
                  partition by p.rebalance_date, p.code
                  order by f.useful desc, f.available_date desc, f.available_time desc,
                           f.period_end desc, f.disclosure_number desc
                ) as rn
              from panel_scope p
              join fundamentals_norm f
                on f.code = p.code
               and f.available_date <= p.rebalance_date
              where p.included_bool
            )
            where rn = 1
            """
        )
        connection.execute(
            """
            create or replace temp table factor_base as
            select
              p.rebalance_date,
              p.code,
              p.name,
              p.market,
              p.sector,
              p.latest_price_date as price_date,
              try_cast(nullif(p.latest_unadjusted_close, '') as double) as latest_unadjusted_close,
              sf.available_date as fundamentals_available_date,
              sf.available_time as fundamentals_available_time,
              sf.document_type,
              sf.period_end,
              sf.disclosure_number,
              sf.operating_profit,
              sf.net_profit,
              sf.equity,
              sf.total_assets,
              sf.shares,
              case
                when coalesce(nullif(try_cast(nullif(p.latest_unadjusted_close, '') as double), 0),
                              try_cast(nullif(p.adjusted_close, '') as double)) is not null
                  and sf.shares is not null
                then coalesce(nullif(try_cast(nullif(p.latest_unadjusted_close, '') as double), 0),
                              try_cast(nullif(p.adjusted_close, '') as double)) * sf.shares
                else null
              end as market_cap,
              case when sf.operating_profit is null or sf.total_assets is null or sf.total_assets = 0
                then null else sf.operating_profit / sf.total_assets end as operating_profit_to_total_assets,
              case when sf.equity is null or sf.total_assets is null or sf.total_assets = 0
                then null else sf.equity / sf.total_assets end as equity_to_assets,
              case
                when sf.net_profit is null or market_cap is null or market_cap = 0
                then null else sf.net_profit / market_cap
              end as earnings_yield,
              case
                when sf.equity is null or market_cap is null or market_cap = 0
                then null else sf.equity / market_cap
              end as book_to_market,
              try_cast(nullif(p.return_12_1, '') as double) as return_12_1,
              try_cast(nullif(p.return_6_1, '') as double) as return_6_1
            from panel_scope p
            left join selected_fundamentals sf
              on sf.rebalance_date = p.rebalance_date and sf.panel_code = p.code
            where p.included_bool
            """
        )
        connection.execute(
            """
            create or replace temp table factors as
            select
              *,
              concat_ws(';',
                case when operating_profit_to_total_assets is null then 'operating_profit_to_total_assets' end,
                case when equity_to_assets is null then 'equity_to_assets' end,
                case when earnings_yield is null then 'earnings_yield' end,
                case when book_to_market is null then 'book_to_market' end,
                case when return_12_1 is null then 'return_12_1' end,
                case when return_6_1 is null then 'return_6_1' end
              ) as missing_flags
            from factor_base
            """
        )
        bounds_select = [
            f"quantile_cont({factor}, {lower_pct / 100.0}) over (partition by rebalance_date) as {factor}_lower,"
            f"quantile_cont({factor}, {upper_pct / 100.0}) over (partition by rebalance_date) as {factor}_upper"
            for factor in raw_factors
        ]
        clipped_select = [
            f"case when {factor} is null then null else least(greatest({factor}, {factor}_lower), {factor}_upper) end as {factor}_clipped"
            for factor in raw_factors
        ]
        z_select = [zscore_sql(factor) for factor in raw_factors]
        connection.execute(
            f"""
            create or replace temp table factor_z as
            with bounded as (
              select
                *,
                {", ".join(bounds_select) if bounds_select else "null as no_bounds"}
              from factors
            ),
            clipped as (
              select
                *,
                {", ".join(clipped_select) if clipped_select else "null as no_clipped"}
              from bounded
            )
            select
              *,
              {", ".join(z_select) if z_select else "null as no_zscores"}
            from clipped
            """
        )
        quality_score = group_score_sql(quality_factors)
        value_score = group_score_sql(value_factors)
        momentum_score = group_score_sql(momentum_factors)
        connection.execute(
            f"""
            create or replace temp table group_scores as
            select
              *,
              {quality_score} as quality_score,
              {value_score} as value_score,
              {momentum_score} as momentum_score
            from factor_z
            """
        )

        if strategy_version == "value_only":
            composite_expr = "case when value_score is null then null else value_score end"
            missing_expr = missing_components_sql(["value_score"])
        elif strategy_version == "qv":
            composite_expr = "case when quality_score is null or value_score is null then null else 0.5 * quality_score + 0.5 * value_score end"
            missing_expr = missing_components_sql(["quality_score", "value_score"])
        elif strategy_version == "qvm":
            composite_expr = (
                "case when quality_score is null or value_score is null or momentum_score is null then null "
                f"else {quality_weight} * quality_score + {value_weight} * value_score + {momentum_weight} * momentum_score end"
            )
            missing_expr = missing_components_sql(["quality_score", "value_score", "momentum_score"])
        elif strategy_version == "weighted_groups":
            missing_fields = [GROUP_SCORE_FIELDS[group] for group in FACTOR_GROUPS if group_weights.get(group, 0.0) > 0]
            missing_condition = " or ".join(f"{field} is null" for field in missing_fields) or "false"
            weighted_sum = " + ".join(
                f"{group_weights.get(group, 0.0)} * coalesce({GROUP_SCORE_FIELDS[group]}, 0)"
                for group in FACTOR_GROUPS
            )
            composite_expr = f"case when {missing_condition} then null else {weighted_sum} end"
            missing_expr = missing_components_sql(missing_fields)
        else:
            raise ValueError(f"DuckDB factor/score panel does not support strategy-version {strategy_version!r}.")

        connection.execute(
            f"""
            create or replace temp table scored as
            select
              *,
              {composite_expr} as composite_score,
              {composite_expr} as qvm_score,
              case when {composite_expr} is not null then 'pass' else 'missing_required_score' end as filter_status,
              '' as filter_reasons,
              {missing_expr} as missing_score_components
            from group_scores
            """
        )
        apply_duckdb_filters(connection, filters)
        connection.execute(
            """
            create or replace temp table scored_ranked as
            select
              scored.*,
              ranks.rank
            from scored
            left join (
              select
                rebalance_date,
                code,
                row_number() over (partition by rebalance_date order by composite_score desc, code asc) as rank
              from scored
              where composite_score is not null and filter_status = 'pass'
            ) ranks
              on ranks.rebalance_date = scored.rebalance_date and ranks.code = scored.code
            """
        )

        selected_fields = unique_fields(
            [
                "rebalance_date",
                "code",
                *PANEL_EXTRA_FIELDS,
                *UNIVERSE_CACHE_FIELDS,
                *factor_output_fields(config),
                *score_cache_fields(raw_factors),
            ]
        )
        ranked_columns = set(connection.execute("describe scored_ranked").df()["column_name"].to_list())
        for field in selected_fields:
            if field not in ranked_columns and field not in {
                "included_flag",
                "exclusion_reason",
                "adjusted_close",
                "fundamental_available_date",
                "rank_score",
                "candidate_rank",
                *UNIVERSE_CACHE_FIELDS,
            }:
                connection.execute(f"alter table scored_ranked add column {field} varchar")
                ranked_columns.add(field)
        final_select = []
        for field in selected_fields:
            if field == "rebalance_date":
                final_select.append("p.rebalance_date as rebalance_date")
            elif field == "included_flag":
                final_select.append("p.included_flag as included_flag")
            elif field == "exclusion_reason":
                final_select.append("case when p.included_bool then '' else p.exclusion_reason end as exclusion_reason")
            elif field == "adjusted_close":
                final_select.append("p.adjusted_close as adjusted_close")
            elif field == "fundamental_available_date":
                final_select.append("s.fundamentals_available_date as fundamental_available_date")
            elif field == "rank_score":
                final_select.append("s.composite_score as rank_score")
            elif field == "candidate_rank":
                final_select.append("s.rank as candidate_rank")
            elif field in UNIVERSE_CACHE_FIELDS:
                final_select.append(f"p.{field} as {field}")
            else:
                final_select.append(f"s.{field} as {field}")
        frame = connection.execute(
            f"""
            select {", ".join(final_select)}
            from panel_scope p
            left join scored_ranked s
              on s.rebalance_date = p.rebalance_date and s.code = p.code
            order by p.rebalance_date, p.code
            """
        ).df()
        return frame.astype(object).where(frame.notna(), None), raw_factors


def build_factor_score_panel_rows(
    *,
    config: dict[str, Any],
    price_universe_panel_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    fundamental_rows: list[dict[str, str]],
    start_date: date,
    end_date: date,
    frequency: str,
    strategy_version: str,
    engine: str = "legacy",
) -> tuple[list[dict[str, Any]], list[str]]:
    if engine != "legacy":
        raise ValueError("build_factor_score_panel_rows supports only engine='legacy'.")
    dates = rebalance_dates(trading_calendar_from_rows(price_rows), start_date, end_date, frequency)
    if not dates:
        raise ValueError("No rebalance dates found in price file for the requested window.")

    output_rows: list[dict[str, Any]] = []
    all_raw_factors: list[str] = []
    for rebalance_date in dates:
        panel_rows = panel_rows_for_date(price_universe_panel_rows, rebalance_date)
        if not panel_rows:
            raise ValueError(f"No price/universe panel rows found for rebalance date {rebalance_date}.")
        universe_rows = included_universe_rows(panel_rows)
        factor_rows = build_factors(
            rebalance_date=rebalance_date,
            universe_rows=universe_rows,
            price_rows=price_rows,
            fundamental_rows=fundamental_rows,
            config=config,
        )
        score_rows, raw_factors = build_scores(
            config=config,
            factor_rows=factor_rows,
            strategy_version=strategy_version,
        )
        for raw_factor in raw_factors:
            if raw_factor not in all_raw_factors:
                all_raw_factors.append(raw_factor)
        output_rows.extend(
            merge_panel_rows(
                panel_rows=panel_rows,
                factor_rows=factor_rows,
                score_rows=score_rows,
            )
        )
    return output_rows, all_raw_factors


def build_factor_score_panel(
    *,
    config: dict[str, Any],
    price_universe_panel_path: Path,
    prices_path: Path,
    fundamentals_path: Path,
    start_date: str,
    end_date: str,
    frequency: str,
    strategy_version: str,
    out_path: Path,
    output_format: str,
    input_format: str = "auto",
    engine: str = "legacy",
) -> int:
    parsed_start = parse_date(start_date, field_name="start_date")
    parsed_end = parse_date(end_date, field_name="end_date")
    if parsed_start is None or parsed_end is None:
        raise ValueError("start-date and end-date are required")
    if engine == "legacy":
        rows, raw_factors = build_factor_score_panel_rows(
            config=config,
            price_universe_panel_rows=read_rows(price_universe_panel_path, input_format),
            price_rows=read_rows(prices_path, input_format),
            fundamental_rows=read_rows(fundamentals_path, input_format),
            start_date=parsed_start,
            end_date=parsed_end,
            frequency=frequency,
            strategy_version=strategy_version,
        )
        fieldnames = factor_score_panel_fields(config, raw_factors)
        normalized_rows = [
            {field: format_csv_value(row.get(field, "")) for field in fieldnames}
            for row in rows
        ]
        write_table(normalized_rows, out_path, format=output_format, fieldnames=fieldnames)
        return len(normalized_rows)
    if engine == "duckdb":
        frame, raw_factors = build_duckdb_factor_score_frame(
            config=config,
            price_universe_panel_path=price_universe_panel_path,
            fundamentals_path=fundamentals_path,
            start_date=parsed_start,
            end_date=parsed_end,
            frequency=frequency,
            strategy_version=strategy_version,
            input_format=input_format,
        )
        fieldnames = factor_score_panel_fields(config, raw_factors)
        write_table(frame, out_path, format=output_format, fieldnames=fieldnames)
        return len(frame)
    raise ValueError(f"Unsupported factor/score panel engine: {engine}")


def main() -> int:
    args = build_parser().parse_args()
    config = load_yaml(args.config)
    row_count = build_factor_score_panel(
        config=config,
        price_universe_panel_path=args.price_universe_panel,
        prices_path=args.prices,
        fundamentals_path=args.fundamentals,
        start_date=args.start_date,
        end_date=args.end_date,
        frequency=args.frequency,
        strategy_version=args.strategy_version,
        out_path=args.out,
        input_format=args.input_format,
        output_format=args.output_format,
        engine=args.engine,
    )
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_rebalance_factor_score_panel",
            file_path=args.out,
            vendor="local",
            schema_version="rebalance_factor_score_panel_v0_1",
            date_range=f"{args.start_date}..{args.end_date}",
            notes=(
                f"strategy_version={args.strategy_version}; engine={args.engine}; rows={row_count}; "
                f"price_universe_panel={checksum(args.price_universe_panel)}; "
                f"prices={checksum(args.prices)}; fundamentals={checksum(args.fundamentals)}"
            ),
        )
    print(f"Wrote {row_count} factor/score panel rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
