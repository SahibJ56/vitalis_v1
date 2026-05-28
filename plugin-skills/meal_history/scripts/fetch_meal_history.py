#!/usr/bin/env python3
"""Fetch recent meals from the local JSON meal history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from meal_store import STORE_PATH, load_entries, now_local, parse_datetime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch recent meal history.")
    parser.add_argument("--days", required=True, type=int, help="Lookback window in days.")
    parser.add_argument("--summary", action="store_true", help="Include totals by macro.")
    parser.add_argument("--store", help=f"Override store path. Defaults to {STORE_PATH}.")
    return parser.parse_args()


def in_window(entry: dict[str, Any], cutoff) -> bool:
    logged_at = entry.get("logged_at")
    if not isinstance(logged_at, str):
        return False
    try:
        return parse_datetime(logged_at) >= cutoff
    except ValueError:
        return False


def summarize(entries: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for entry in entries:
        macros = entry.get("macros")
        if not isinstance(macros, dict):
            continue
        for key, value in macros.items():
            if isinstance(value, (int, float)):
                totals[key] = round(totals.get(key, 0.0) + float(value), 3)
    return totals


def main() -> int:
    args = parse_args()
    if args.days < 0:
        raise SystemExit("--days must be non-negative")
    store = Path(args.store).expanduser() if args.store else STORE_PATH
    cutoff = now_local() - __import__("datetime").timedelta(days=args.days)
    entries = [entry for entry in load_entries(store) if in_window(entry, cutoff)]
    entries.sort(key=lambda item: item.get("logged_at", ""))
    payload: dict[str, Any] = {
        "days": args.days,
        "count": len(entries),
        "entries": entries,
    }
    if args.summary:
        payload["totals"] = summarize(entries)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
