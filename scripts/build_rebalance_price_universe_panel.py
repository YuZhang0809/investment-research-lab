from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from duckdb_query import require_duckdb
from research_common import append_manifest, checksum, load_yaml, parse_date, resolve_table_format, write_table


PANEL_FIELDS = [
    "rebalance_date",
    "code",
    "name",
    "market",
    "sector",
    "source_date",
    "source",
    "listing_lifecycle_status",
    "listed_date",
    "delisted_date",
    "last_trading_date",
    "lifecycle_exit_date",
    "security_type",
    "lot_size",
    "included_flag",
    "exclusion_reason",
    "latest_price_date",
    "latest_unadjusted_close",
    "adjusted_close",
    "rebalance_price_available",
    "latest_price_stale",
    "price_staleness_trading_days",
    "ipo_age_trading_days",
    "median_60d_trading_value",
    "has_fundamentals",
    "tradable_flag",
    "price_limit_flag",
    "return_12_1",
    "return_6_1",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a DuckDB price/universe rebalance panel fast path.")
    parser.add_argument("--config", type=Path, default=Path("configs/qvm_v0_1.example.yml"))
    parser.add_argument("--listings", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--fundamentals", type=Path, help="Optional fundamentals CSV/Parquet for has_fundamentals filtering only.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="monthly")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--engine", choices=["duckdb"], default="duckdb")
    parser.add_argument("--input-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--output-format", choices=["auto", "csv", "parquet"], default="auto")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest/data_manifest.csv"))
    parser.add_argument("--no-manifest", action="store_true")
    return parser


def sql_string(value: Any) -> str:
    return str(value).replace("'", "''")


def scan_sql(path: Path, table_format: str) -> str:
    resolved = resolve_table_format(path, table_format)
    escaped = sql_string(path.as_posix())
    if resolved == "csv":
        return f"read_csv_auto('{escaped}', all_varchar=true, header=true)"
    return f"read_parquet('{escaped}')"


def normalize_kind(value: str) -> str:
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def register_scan(connection: Any, view_name: str, path: Path, table_format: str) -> set[str]:
    connection.execute(f"create or replace temp view {view_name} as select * from {scan_sql(path, table_format)}")
    description = connection.execute(f"describe select * from {view_name}").fetchall()
    return {str(row[0]) for row in description}


def optional_column(columns: set[str], name: str, default: str = "''") -> str:
    return f'"{name}"' if name in columns else default


def create_listings_table(connection: Any, columns: set[str]) -> None:
    connection.execute(
        f"""
        create or replace temp table listings_norm as
        select
          coalesce({optional_column(columns, 'code')}, '')::varchar as code,
          coalesce({optional_column(columns, 'name')}, '')::varchar as name,
          coalesce({optional_column(columns, 'market')}, '')::varchar as market,
          coalesce({optional_column(columns, 'sector')}, '')::varchar as sector,
          try_cast(nullif({optional_column(columns, 'listed_date')}, '') as date) as listed_date,
          try_cast(nullif({optional_column(columns, 'delisted_date')}, '') as date) as delisted_date,
          try_cast(nullif({optional_column(columns, 'last_trading_date')}, '') as date) as last_trading_date,
          coalesce({optional_column(columns, 'security_type')}, '')::varchar as security_type,
          coalesce({optional_column(columns, 'is_common_stock')}, '')::varchar as is_common_stock,
          coalesce({optional_column(columns, 'is_etf_reit_infra')}, '')::varchar as is_etf_reit_infra,
          coalesce({optional_column(columns, 'tradable_flag')}, '')::varchar as tradable_flag,
          coalesce({optional_column(columns, 'lot_size', "'100'")}, '100')::varchar as lot_size,
          try_cast(nullif(coalesce({optional_column(columns, 'source_date')}, {optional_column(columns, 'snapshot_date')}), '') as date) as source_date,
          coalesce({optional_column(columns, 'source')}, '')::varchar as source,
          coalesce({optional_column(columns, 'listing_lifecycle_status')}, '')::varchar as listing_lifecycle_status,
          coalesce({optional_column(columns, 'delisting_reason')}, '')::varchar as delisting_reason,
          coalesce({optional_column(columns, 'successor_code')}, '')::varchar as successor_code
        from raw_listings
        where coalesce({optional_column(columns, 'code')}, '') <> ''
        """
    )


def create_prices_table(connection: Any, columns: set[str]) -> None:
    close_expr = (
        optional_column(columns, "unadjusted_close")
        if "unadjusted_close" in columns
        else optional_column(columns, "close") if "close" in columns else optional_column(columns, "price")
    )
    connection.execute(
        f"""
        create or replace temp table prices_base as
        select
          try_cast(nullif({optional_column(columns, 'date')}, '') as date) as price_date,
          coalesce({optional_column(columns, 'code')}, '')::varchar as code,
          try_cast(nullif({close_expr}, '') as double) as unadjusted_close,
          try_cast(nullif({optional_column(columns, 'adjusted_close')}, '') as double) as adjusted_close,
          try_cast(nullif({optional_column(columns, 'trading_value')}, '') as double) as trading_value,
          try_cast(nullif({optional_column(columns, 'adjustment_factor')}, '') as double) as adjustment_factor,
          bool_value({optional_column(columns, 'tradable_flag')}, true) as price_tradable_flag,
          bool_value({optional_column(columns, 'price_limit_flag')}, false) as price_limit_flag
        from raw_prices
        where coalesce({optional_column(columns, 'code')}, '') <> ''
          and try_cast(nullif({optional_column(columns, 'date')}, '') as date) is not null
        """
    )
    connection.execute(
        """
        create or replace temp table prices_norm as
        select
          *,
          case
            when adjusted_close is not null then adjusted_close
            when unadjusted_close is not null and cumulative_adjustment > 0 then unadjusted_close / cumulative_adjustment
            else null
          end as effective_adjusted_close
        from (
          select
            *,
            exp(
              sum(ln(case when adjustment_factor is not null and adjustment_factor > 0 then adjustment_factor else 1 end))
              over (partition by code order by price_date rows between unbounded preceding and current row)
            ) as cumulative_adjustment
          from prices_base
        )
        """
    )


def create_fundamentals_table(connection: Any, columns: set[str] | None) -> None:
    if not columns:
        connection.execute("create or replace temp table fundamentals_norm(code varchar, available_date date)")
        return
    connection.execute(
        f"""
        create or replace temp table fundamentals_norm as
        select
          coalesce({optional_column(columns, 'code')}, '')::varchar as code,
          try_cast(nullif(coalesce({optional_column(columns, 'available_date')}, {optional_column(columns, 'disclosure_date')}), '') as date) as available_date
        from raw_fundamentals
        where coalesce({optional_column(columns, 'code')}, '') <> ''
        """
    )


def list_table(connection: Any, name: str, values: list[str]) -> None:
    connection.execute(f"create or replace temp table {name}(value varchar)")
    if values:
        connection.executemany(f"insert into {name} values (?)", [(normalize_kind(value),) for value in values])


def build_panel_frame(
    *,
    config: dict[str, Any],
    listings_path: Path,
    prices_path: Path,
    fundamentals_path: Path | None,
    start_date: str,
    end_date: str,
    frequency: str,
    input_format: str,
) -> Any:
    duckdb = require_duckdb()
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            create macro bool_value(value, default_value) as (
              case
                when value is null or trim(value::varchar) = '' then default_value
                when lower(trim(value::varchar)) in ('1', 'true', 't', 'yes', 'y') then true
                when lower(trim(value::varchar)) in ('0', 'false', 'f', 'no', 'n') then false
                else default_value
              end
            )
            """
        )
        listing_columns = register_scan(connection, "raw_listings", listings_path, input_format)
        price_columns = register_scan(connection, "raw_prices", prices_path, input_format)
        fundamental_columns = register_scan(connection, "raw_fundamentals", fundamentals_path, input_format) if fundamentals_path else None
        create_listings_table(connection, listing_columns)
        create_prices_table(connection, price_columns)
        create_fundamentals_table(connection, fundamental_columns)

        instruments = config.get("scope", {}).get("instruments", {})
        list_table(connection, "included_security_types", [str(value) for value in instruments.get("include", [])])
        list_table(connection, "excluded_security_types", [str(value) for value in instruments.get("exclude", [])])

        universe_config = config.get("universe", {})
        min_ipo_days = int(universe_config.get("min_ipo_age_trading_days") or 0)
        lookback_days = int(universe_config.get("liquidity_lookback_days") or 60)
        min_trading_value = universe_config.get("min_median_trading_value_jpy")
        require_tradable = bool(universe_config.get("require_tradable_on_rebalance_date", True))
        require_fundamentals = bool(universe_config.get("require_fundamentals", True))
        strict_price = bool(universe_config.get("strict_rebalance_price_filter", False))

        connection.execute(
            """
            create or replace temp table calendar as
            select price_date, row_number() over (order by price_date) - 1 as calendar_index
            from (select distinct price_date from prices_norm where price_date is not null)
            order by price_date
            """
        )
        quarter_filter = "and month(price_date) in (3, 6, 9, 12)" if frequency == "quarterly" else ""
        connection.execute(
            f"""
            create or replace temp table rebalances as
            select max(price_date) as rebalance_date
            from calendar
            where price_date between ?::date and ?::date
            group by strftime(price_date, '%Y-%m')
            having max(price_date) is not null {quarter_filter}
            order by rebalance_date
            """,
            [start_date, end_date],
        )
        connection.execute(
            """
            create or replace temp table rebalance_index as
            select r.rebalance_date, c.calendar_index
            from rebalances r
            join calendar c on c.price_date = r.rebalance_date
            """
        )
        connection.execute(
            """
            create or replace temp table rebalance_snapshots as
            select r.rebalance_date, max(l.source_date) as selected_source_date
            from rebalances r
            left join listings_norm l on l.source_date <= r.rebalance_date
            group by r.rebalance_date
            """
        )
        connection.execute(
            """
            create or replace temp table listing_panel as
            select
              r.rebalance_date,
              l.code,
              l.name,
              l.market,
              l.sector,
              l.source_date,
              l.source,
              case
                when s.selected_source_date is not null and l.listed_date is null then 'pit_snapshot_panel_missing_lifecycle_dates'
                else l.listing_lifecycle_status
              end as listing_lifecycle_status,
              l.listed_date,
              l.delisted_date,
              l.last_trading_date,
              coalesce(l.last_trading_date, l.delisted_date) as lifecycle_exit_date,
              l.security_type,
              l.is_common_stock,
              l.is_etf_reit_infra,
              l.tradable_flag,
              l.lot_size,
              l.delisting_reason,
              l.successor_code
            from rebalances r
            join rebalance_snapshots s on s.rebalance_date = r.rebalance_date
            join listings_norm l
              on (s.selected_source_date is null or l.source_date = s.selected_source_date)
            """
        )
        connection.execute(
            """
            create or replace temp table price_features as
            select
              lp.rebalance_date,
              lp.code,
              latest.price_date as latest_price_date,
              latest.unadjusted_close as latest_unadjusted_close,
              latest.effective_adjusted_close as adjusted_close,
              rebalance_price.price_date is not null as rebalance_price_available,
              latest.price_date is not null and rebalance_price.price_date is null as latest_price_stale,
              case
                when latest.price_date is null then null
                when rebalance_price.price_date is not null then 0
                else (
                  select count(*)
                  from calendar c
                  where c.price_date > latest.price_date
                    and c.price_date <= lp.rebalance_date
                )
              end as price_staleness_trading_days,
              coalesce(price_counts.ipo_age_trading_days, 0) as ipo_age_trading_days,
              liquidity.median_60d_trading_value,
              coalesce(rebalance_price.price_tradable_flag, false) as rebalance_price_tradable_flag,
              coalesce(latest.price_limit_flag, false) as price_limit_flag,
              ret12.return_12_1,
              ret6.return_6_1
            from listing_panel lp
            left join lateral (
              select *
              from prices_norm p
              where p.code = lp.code and p.price_date <= lp.rebalance_date
              order by p.price_date desc
              limit 1
            ) latest on true
            left join prices_norm rebalance_price
              on rebalance_price.code = lp.code
             and rebalance_price.price_date = lp.rebalance_date
            left join lateral (
              select count(*) as ipo_age_trading_days
              from prices_norm p
              where p.code = lp.code and p.price_date <= lp.rebalance_date
            ) price_counts on true
            left join lateral (
              select median(trading_value) as median_60d_trading_value
              from (
                select trading_value
                from prices_norm p
                where p.code = lp.code and p.price_date <= lp.rebalance_date
                order by p.price_date desc
                limit case when ? <= 0 then 2147483647 else ? end
              )
              where trading_value is not null
            ) liquidity on true
            left join rebalance_index ri on ri.rebalance_date = lp.rebalance_date
            left join calendar start12 on start12.calendar_index = ri.calendar_index - 252
            left join calendar end12 on end12.calendar_index = ri.calendar_index - 21
            left join calendar start6 on start6.calendar_index = ri.calendar_index - 126
            left join calendar end6 on end6.calendar_index = ri.calendar_index - 21
            left join lateral (
              select
                case
                  when start_price.effective_adjusted_close is null
                    or end_price.effective_adjusted_close is null
                    or start_price.effective_adjusted_close <= 0
                    or start12.price_date >= end12.price_date
                  then null
                  else end_price.effective_adjusted_close / start_price.effective_adjusted_close - 1.0
                end as return_12_1
              from (
                select effective_adjusted_close
                from prices_norm p
                where p.code = lp.code and p.price_date <= start12.price_date
                order by p.price_date desc
                limit 1
              ) start_price
              cross join (
                select effective_adjusted_close
                from prices_norm p
                where p.code = lp.code and p.price_date <= end12.price_date
                order by p.price_date desc
                limit 1
              ) end_price
            ) ret12 on true
            left join lateral (
              select
                case
                  when start_price.effective_adjusted_close is null
                    or end_price.effective_adjusted_close is null
                    or start_price.effective_adjusted_close <= 0
                    or start6.price_date >= end6.price_date
                  then null
                  else end_price.effective_adjusted_close / start_price.effective_adjusted_close - 1.0
                end as return_6_1
              from (
                select effective_adjusted_close
                from prices_norm p
                where p.code = lp.code and p.price_date <= start6.price_date
                order by p.price_date desc
                limit 1
              ) start_price
              cross join (
                select effective_adjusted_close
                from prices_norm p
                where p.code = lp.code and p.price_date <= end6.price_date
                order by p.price_date desc
                limit 1
              ) end_price
            ) ret6 on true
            """,
            [lookback_days, lookback_days],
        )
        min_trading_expr = "null" if min_trading_value is None else str(float(min_trading_value))
        connection.execute(
            f"""
            create or replace temp table final_panel as
            select
              lp.rebalance_date,
              lp.code,
              lp.name,
              lp.market,
              lp.sector,
              lp.source_date,
              lp.source,
              lp.listing_lifecycle_status,
              lp.listed_date,
              lp.delisted_date,
              lp.last_trading_date,
              lp.lifecycle_exit_date,
              lp.security_type,
              lp.lot_size,
              reasons.exclusion_reason = '' as included_flag,
              reasons.exclusion_reason,
              pf.latest_price_date,
              pf.latest_unadjusted_close,
              pf.adjusted_close,
              pf.rebalance_price_available,
              pf.latest_price_stale,
              pf.price_staleness_trading_days,
              pf.ipo_age_trading_days,
              pf.median_60d_trading_value,
              coalesce(f.has_fundamentals, false) as has_fundamentals,
              bool_value(lp.tradable_flag, null) is not false
                and coalesce(pf.rebalance_price_tradable_flag, false) as tradable_flag,
              pf.price_limit_flag,
              case when reasons.exclusion_reason = '' then pf.return_12_1 else null end as return_12_1,
              case when reasons.exclusion_reason = '' then pf.return_6_1 else null end as return_6_1
            from listing_panel lp
            left join price_features pf
              on pf.rebalance_date = lp.rebalance_date and pf.code = lp.code
            left join lateral (
              select count(*) > 0 as has_fundamentals
              from fundamentals_norm f
              where f.code = lp.code and f.available_date <= lp.rebalance_date
            ) f on true
            left join lateral (
              select concat_ws(';',
                case when lp.listed_date is not null and lp.listed_date > lp.rebalance_date
                  then 'listed_after_rebalance:' || lp.listed_date::varchar end,
                case when lp.last_trading_date is not null and lp.last_trading_date < lp.rebalance_date
                  then 'last_trading_before_rebalance:' || lp.last_trading_date::varchar end,
                case when lp.delisted_date is not null and lp.delisted_date < lp.rebalance_date
                  then 'delisted_before_rebalance:' || lp.delisted_date::varchar end,
                case when bool_value(lp.is_etf_reit_infra, false)
                  then 'excluded_instrument_flag' end,
                case
                  when replace(replace(lower(trim(lp.security_type)), ' ', '_'), '-', '_') in (select value from excluded_security_types)
                  then 'excluded_security_type:' || replace(replace(lower(trim(lp.security_type)), ' ', '_'), '-', '_')
                end,
                case when bool_value(lp.is_common_stock, null) = false then 'not_common_stock' end,
                case
                  when (select count(*) from included_security_types) > 0
                    and replace(replace(lower(trim(lp.security_type)), ' ', '_'), '-', '_') <> ''
                    and replace(replace(lower(trim(lp.security_type)), ' ', '_'), '-', '_') not in (select value from included_security_types)
                    and bool_value(lp.is_common_stock, null) is not true
                  then 'not_in_included_security_types:' || replace(replace(lower(trim(lp.security_type)), ' ', '_'), '-', '_')
                end,
                case when {str(require_tradable).lower()} and bool_value(lp.tradable_flag, null) = false
                  then 'listing_not_tradable' end,
                case when pf.ipo_age_trading_days < {min_ipo_days}
                  then 'insufficient_ipo_age_trading_days:' || pf.ipo_age_trading_days::varchar || '<{min_ipo_days}' end,
                case when pf.ipo_age_trading_days < {lookback_days}
                  then 'insufficient_liquidity_lookback:' || pf.ipo_age_trading_days::varchar || '<{lookback_days}' end,
                case when {min_trading_expr} is not null
                  and (pf.median_60d_trading_value is null or pf.median_60d_trading_value < {min_trading_expr})
                  then 'below_min_median_trading_value:' || coalesce(pf.median_60d_trading_value::varchar, 'None') end,
                case when pf.latest_price_date is null then 'no_price_on_or_before_rebalance' end,
                case when {str(strict_price).lower()} and pf.latest_price_date is not null and not pf.rebalance_price_available
                  then 'no_price_on_rebalance_date' end,
                case when {str(strict_price).lower()} and pf.rebalance_price_available and not pf.rebalance_price_tradable_flag
                  then 'price_not_tradable_on_rebalance_date' end,
                case when {str(require_fundamentals).lower()} and not coalesce(f.has_fundamentals, false)
                  then 'missing_point_in_time_fundamentals' end
              ) as exclusion_reason
            ) reasons on true
            order by lp.rebalance_date, lp.code
            """
        )
        frame = connection.execute(f"select {', '.join(PANEL_FIELDS)} from final_panel").df()
        return frame.astype(object).where(frame.notna(), None)


def build_panel(
    *,
    config: dict[str, Any],
    listings_path: Path,
    prices_path: Path,
    fundamentals_path: Path | None,
    start_date: str,
    end_date: str,
    frequency: str,
    input_format: str,
    out_path: Path,
    output_format: str,
) -> int:
    frame = build_panel_frame(
        config=config,
        listings_path=listings_path,
        prices_path=prices_path,
        fundamentals_path=fundamentals_path,
        start_date=start_date,
        end_date=end_date,
        frequency=frequency,
        input_format=input_format,
    )
    write_table(frame, out_path, format=output_format, fieldnames=PANEL_FIELDS)
    return len(frame)


def main() -> int:
    args = build_parser().parse_args()
    if parse_date(args.start_date, field_name="start_date") is None:
        raise ValueError("start-date is required")
    if parse_date(args.end_date, field_name="end_date") is None:
        raise ValueError("end-date is required")
    config = load_yaml(args.config)
    row_count = build_panel(
        config=config,
        listings_path=args.listings,
        prices_path=args.prices,
        fundamentals_path=args.fundamentals,
        start_date=args.start_date,
        end_date=args.end_date,
        frequency=args.frequency,
        input_format=args.input_format,
        out_path=args.out,
        output_format=args.output_format,
    )
    if not args.no_manifest:
        append_manifest(
            args.manifest,
            source="derived_rebalance_price_universe_panel",
            file_path=args.out,
            vendor="local",
            schema_version="rebalance_price_universe_panel_v0_1",
            date_range=f"{args.start_date}..{args.end_date}",
            notes=f"engine={args.engine}; rows={row_count}; listings={checksum(args.listings)}; prices={checksum(args.prices)}",
        )
    print(f"Wrote {row_count} price/universe panel rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
