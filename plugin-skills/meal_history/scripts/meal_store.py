#!/usr/bin/env python3
"""Shared JSON storage for meal history scripts."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


STORE_PATH = Path.home() / ".openclaw" / "data" / "meal_history.json"


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def parse_datetime(value: str) -> dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_local().tzinfo)
    return parsed.astimezone()


def iso(value: dt.datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")


def load_entries(path: Path = STORE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload["entries"]
    raise ValueError(f"Unexpected meal history JSON shape in {path}")


def save_entries(entries: list[dict[str, Any]], path: Path = STORE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "entries": entries}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
