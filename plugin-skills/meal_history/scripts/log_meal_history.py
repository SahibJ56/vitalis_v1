#!/usr/bin/env python3
"""Log a meal to the local JSON meal history."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from meal_store import STORE_PATH, iso, load_entries, now_local, parse_datetime, save_entries


MACRO_FIELDS = ("calories", "protein", "carbs", "fat", "fiber", "sugar", "sodium")


def number(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a number") from exc


def parse_macros(args: argparse.Namespace) -> dict[str, float]:
    macros: dict[str, float] = {}
    if args.macros:
        try:
            payload = json.loads(args.macros)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid --macros JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SystemExit("--macros must be a JSON object")
        for key, value in payload.items():
            if not isinstance(value, (int, float)):
                raise SystemExit(f"Macro {key!r} must be numeric")
            macros[str(key)] = float(value)

    for field in MACRO_FIELDS:
        value = getattr(args, field)
        if value is not None:
            macros[field] = value
    return macros


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Log a meal to local JSON history.")
    parser.add_argument("--name", required=True, help="Meal or food name.")
    parser.add_argument("--logged-at", help="ISO 8601 timestamp. Defaults to now.")
    parser.add_argument("--notes", help="Optional note.")
    parser.add_argument("--macros", help="JSON object of macro values.")
    for field in MACRO_FIELDS:
        parser.add_argument(f"--{field}", type=number, help=f"{field} value.")
    parser.add_argument("--store", help=f"Override store path. Defaults to {STORE_PATH}.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = Path(args.store).expanduser() if args.store else STORE_PATH
    logged_at = parse_datetime(args.logged_at) if args.logged_at else now_local()
    created_at = now_local()
    entry = {
        "id": str(uuid.uuid4()),
        "logged_at": iso(logged_at),
        "name": args.name,
        "macros": parse_macros(args),
        "notes": args.notes,
        "created_at": iso(created_at),
    }
    entries = load_entries(store)
    entries.append(entry)
    entries.sort(key=lambda item: item.get("logged_at", ""))
    save_entries(entries, store)
    print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
