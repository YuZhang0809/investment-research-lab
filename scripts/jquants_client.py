from __future__ import annotations

import os
import re
import sys
from typing import Any


API_BASE = "https://api.jquants.com/v2"
DEFAULT_API_KEY_ENV = "JQUANTS_API_KEY"


def api_key_from_env(env_name: str = DEFAULT_API_KEY_ENV) -> str:
    return os.environ.get(env_name, "")


def require_api_key(env_name: str = DEFAULT_API_KEY_ENV) -> str:
    api_key = api_key_from_env(env_name)
    if not api_key:
        print(
            f"J-Quants API key is not set. Set {env_name} before live API calls.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return api_key


def client_v2(api_key: str | None = None) -> Any:
    try:
        import jquantsapi
    except ImportError as exc:
        raise RuntimeError(
            "jquants-api-client is required for J-Quants downloads. "
            "Install requirements.txt before live API calls."
        ) from exc
    return jquantsapi.ClientV2(api_key=api_key)


def frame_to_rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    rows = frame.to_dict(orient="records")
    clean_rows: list[dict[str, Any]] = []
    for row in rows:
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if value is None:
                clean[key] = ""
                continue
            try:
                if value != value:
                    clean[key] = ""
                    continue
            except TypeError:
                pass
            if hasattr(value, "isoformat"):
                text = value.isoformat()
                clean[key] = text[:10] if "T" in text else text
            else:
                clean[key] = value
        clean_rows.append(clean)
    return clean_rows


def request_json(api_key: str, path: str, params: dict[str, str]) -> dict[str, Any]:
    client = client_v2(api_key)
    if path == "/bulk/list":
        rows = frame_to_rows(client.get_bulk_list(endpoint=params.get("endpoint", "")))
        rows = filter_bulk_rows(rows, params.get("from", ""), params.get("to", ""))
        return {"data": rows}
    if path == "/bulk/get":
        return {"url": client.get_bulk(key=params.get("key", ""))}
    raise ValueError(f"Unsupported JSON endpoint for official J-Quants client: {path}")


def request_paginated(api_key: str, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    client = client_v2(api_key)
    if path == "/equities/master":
        frame = client.get_eq_master(code=params.get("code", ""), date=params.get("date", ""))
    elif path == "/equities/bars/daily":
        frame = client.get_eq_bars_daily(
            code=params.get("code", ""),
            from_yyyymmdd=params.get("from", ""),
            to_yyyymmdd=params.get("to", ""),
            date_yyyymmdd=params.get("date", ""),
        )
    elif path == "/fins/summary":
        frame = client.get_fin_summary(
            code=params.get("code", ""),
            date_yyyymmdd=params.get("date", ""),
        )
    elif path == "/indices/topix":
        frame = client.get_idx_bars_daily_topix(
            from_yyyymmdd=params.get("from", ""),
            to_yyyymmdd=params.get("to", ""),
        )
    else:
        raise ValueError(f"Unsupported endpoint for official J-Quants client: {path}")
    return frame_to_rows(frame)


def filter_bulk_rows(rows: list[dict[str, Any]], from_value: str, to_value: str) -> list[dict[str, Any]]:
    if not from_value and not to_value:
        return rows
    start = normalize_month_token(from_value)
    end = normalize_month_token(to_value)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("Key") or "")
        token = bulk_month_token(key)
        if token is None:
            filtered.append(row)
            continue
        if start and token < start:
            continue
        if end and token > end:
            continue
        filtered.append(row)
    return filtered


def normalize_month_token(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[:6]


def bulk_month_token(value: str) -> str | None:
    matches = re.findall(r"(20\d{2})[-_/]?(0[1-9]|1[0-2])", value)
    if not matches:
        return None
    year, month = matches[-1]
    return f"{year}{month}"
