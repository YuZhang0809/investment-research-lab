from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def trading_days(end: date, count: int) -> list[date]:
    values: list[date] = []
    current = end
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current -= timedelta(days=1)
    return sorted(values)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        listings = temp / "listings.csv"
        prices = temp / "prices.csv"
        fundamentals = temp / "fundamentals.csv"
        out_dir = temp / "universe"
        manifest = temp / "manifest.csv"
        rebalance_date = date(2026, 3, 31)

        write_csv(
            listings,
            [
                {
                    "code": "1001",
                    "name": "Eligible Co",
                    "market": "Prime",
                    "sector": "Industrials",
                    "listed_date": "2020-01-01",
                    "delisted_date": "",
                    "security_type": "common_stock",
                    "is_common_stock": "true",
                    "is_etf_reit_infra": "false",
                    "tradable_flag": "true",
                    "lot_size": "100",
                },
                {
                    "code": "1002",
                    "name": "Recent IPO",
                    "market": "Growth",
                    "sector": "Tech",
                    "listed_date": "2026-01-10",
                    "delisted_date": "",
                    "security_type": "common_stock",
                    "is_common_stock": "true",
                    "is_etf_reit_infra": "false",
                    "tradable_flag": "true",
                    "lot_size": "100",
                },
                {
                    "code": "1003",
                    "name": "ETF Sample",
                    "market": "ETF",
                    "sector": "",
                    "listed_date": "2020-01-01",
                    "delisted_date": "",
                    "security_type": "etf",
                    "is_common_stock": "false",
                    "is_etf_reit_infra": "true",
                    "tradable_flag": "true",
                    "lot_size": "1",
                },
            ],
            [
                "code",
                "name",
                "market",
                "sector",
                "listed_date",
                "delisted_date",
                "security_type",
                "is_common_stock",
                "is_etf_reit_infra",
                "tradable_flag",
                "lot_size",
            ],
        )

        price_rows: list[dict[str, object]] = []
        for code, count, trading_value in [("1001", 270, 10_000_000), ("1002", 40, 8_000_000), ("1003", 270, 30_000_000)]:
            for idx, day in enumerate(trading_days(rebalance_date, count)):
                price_rows.append(
                    {
                        "date": day.isoformat(),
                        "code": code,
                        "unadjusted_close": 1000 + idx,
                        "trading_value": trading_value,
                        "tradable_flag": "true",
                        "price_limit_flag": "false",
                    }
                )
        write_csv(
            prices,
            price_rows,
            ["date", "code", "unadjusted_close", "trading_value", "tradable_flag", "price_limit_flag"],
        )

        write_csv(
            fundamentals,
            [
                {"code": "1001", "available_date": "2026-02-15"},
                {"code": "1002", "available_date": "2026-02-15"},
                {"code": "1003", "available_date": "2026-02-15"},
            ],
            ["code", "available_date"],
        )

        command = [
            sys.executable,
            str(ROOT / "scripts" / "build_universe.py"),
            "--config",
            str(ROOT / "configs" / "qvm_v0_1.example.yml"),
            "--rebalance-date",
            rebalance_date.isoformat(),
            "--listings",
            str(listings),
            "--prices",
            str(prices),
            "--fundamentals",
            str(fundamentals),
            "--out-dir",
            str(out_dir),
            "--manifest",
            str(manifest),
        ]
        result = subprocess.run(command, cwd=ROOT, check=True, text=True, capture_output=True)
        print(result.stdout.strip())

        universe_path = out_dir / "universe_202603.csv"
        excluded_path = out_dir / "excluded_202603.csv"
        with universe_path.open("r", encoding="utf-8", newline="") as file:
            universe = list(csv.DictReader(file))
        with excluded_path.open("r", encoding="utf-8", newline="") as file:
            excluded = list(csv.DictReader(file))

        assert [row["code"] for row in universe] == ["1001"], universe
        reasons = {row["code"]: row["reason"] for row in excluded}
        assert "insufficient_ipo_age_trading_days" in reasons["1002"], reasons
        assert "excluded_instrument_flag" in reasons["1003"], reasons
        assert manifest.exists(), "manifest was not written"
        print("Smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
