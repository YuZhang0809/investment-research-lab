from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
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


def request_json(api_key: str, path: str, params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value})
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "User-Agent": "investment-experiment/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc


def request_paginated(api_key: str, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_params = dict(params)
    while True:
        payload = request_json(api_key, path, next_params)
        data = payload.get("data", [])
        if isinstance(data, list):
            rows.extend(data)
        pagination_key = payload.get("pagination_key")
        if not pagination_key:
            break
        next_params["pagination_key"] = str(pagination_key)
    return rows
