#!/usr/bin/env python3
"""Generate a morning brief from meals and Oura data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any


MEAL_STORE = Path.home() / ".openclaw" / "data" / "meal_history.json"
FETCH_OURA = Path.home() / ".openclaw" / "plugin-skills" / "fetch_oura" / "scripts" / "fetch_oura.py"
OPENCLAW_BIN = Path("/home/bryson2/.npm-global/bin/openclaw")
TELEGRAM_TARGET = "6535551401"


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


def load_meals(path: Path = MEAL_STORE) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload["entries"]
    raise SystemExit(f"Unexpected meal history shape in {path}")


def meals_for_day(day: dt.date) -> list[dict[str, Any]]:
    entries = []
    for entry in load_meals():
        logged_at = entry.get("logged_at")
        if not isinstance(logged_at, str):
            continue
        try:
            if parse_datetime(logged_at).date() == day:
                entries.append(entry)
        except ValueError:
            continue
    entries.sort(key=lambda item: item.get("logged_at", ""))
    return entries


def sum_macros(entries: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for entry in entries:
        macros = entry.get("macros")
        if not isinstance(macros, dict):
            continue
        for key, value in macros.items():
            if isinstance(value, (int, float)):
                totals[key] = round(totals.get(key, 0.0) + float(value), 1)
    return totals


def fetch_oura(kind: str, days: int = 180) -> tuple[list[dict[str, Any]], str | None]:
    command = [sys.executable, str(FETCH_OURA), "--kind", kind, "--days", str(days)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if completed.returncode != 0:
        return [], completed.stderr.strip() or completed.stdout.strip()
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return [], f"Could not parse Oura {kind}: {exc}"
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], None
    return [], None


def day_value(record: dict[str, Any]) -> dt.date | None:
    value = record.get("day")
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def latest_record(records: list[dict[str, Any]], on_or_before: dt.date) -> dict[str, Any] | None:
    dated = [(day_value(record), record) for record in records]
    candidates = [(day, record) for day, record in dated if day and day <= on_or_before]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def previous_records(records: list[dict[str, Any]], latest: dict[str, Any], limit: int = 14) -> list[dict[str, Any]]:
    latest_day = day_value(latest)
    if not latest_day:
        return []
    prior = [record for record in records if (day := day_value(record)) and day < latest_day]
    prior.sort(key=lambda record: day_value(record) or dt.date.min)
    return prior[-limit:]


def sleep_duration_by_day(records: list[dict[str, Any]]) -> dict[dt.date, float]:
    by_day: dict[dt.date, float] = {}
    for record in records:
        day = day_value(record)
        duration = num(record, "total_sleep_duration")
        if day and duration is not None:
            by_day[day] = by_day.get(day, 0.0) + duration
    return by_day


def avg(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def num(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def hours(seconds: float | None) -> float | None:
    return round(seconds / 3600, 2) if seconds is not None else None


def fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if abs(value - round(value)) < 0.05:
        return f"{round(value):.0f}{suffix}"
    return f"{value:.1f}{suffix}"


def nutrition_insight(entries: list[dict[str, Any]], totals: dict[str, float]) -> tuple[str, str]:
    if not entries:
        return (
            "No meals were logged yesterday, so nutrition trends are blind.",
            "Log each meal today with at least protein and calories so tomorrow's brief has a real signal.",
        )
    protein = totals.get("protein")
    fiber = totals.get("fiber")
    calories = totals.get("calories")
    if protein is not None and protein < 90:
        return (
            f"Protein looks light at about {fmt(protein, 'g')} yesterday.",
            "Aim for 30g protein at your first meal today.",
        )
    if fiber is not None and fiber < 20:
        return (
            f"Fiber looks low at about {fmt(fiber, 'g')} yesterday.",
            "Add one high-fiber anchor today: beans, berries, oats, or a large vegetable serving.",
        )
    if calories is not None and calories < 1600:
        return (
            f"Calories look low at about {fmt(calories)} yesterday, which can drag recovery.",
            "Add one balanced meal today with protein, carbs, and produce instead of grazing.",
        )
    macro_bits = []
    for key, suffix in [("calories", ""), ("protein", "g"), ("carbs", "g"), ("fat", "g"), ("fiber", "g")]:
        if key in totals:
            macro_bits.append(f"{key}: {fmt(totals[key], suffix)}")
    detail = ", ".join(macro_bits) if macro_bits else f"{len(entries)} meals logged"
    return (
        f"Yesterday's log is usable: {detail}.",
        "Repeat the most complete meal structure today and keep logging portions consistently.",
    )


def readiness_label(readiness_score: float | None, sleep_score: float | None, sleep_delta_hours: float | None) -> str:
    if readiness_score is None and sleep_score is None:
        return "Use subjective readiness today; recent Oura data is missing."
    if (readiness_score is not None and readiness_score < 60) or (
        sleep_delta_hours is not None and sleep_delta_hours <= -1.0
    ):
        return "Take it easy: bias toward lighter training, steady meals, and an earlier wind-down."
    if (readiness_score is not None and readiness_score >= 80) and (
        sleep_score is None or sleep_score >= 75
    ):
        return "Green light: normal workload is reasonable if you feel good."
    return "Normal day: keep intensity moderate and watch energy in the afternoon."


def build_brief(target_date: dt.date) -> str:
    yesterday = target_date - dt.timedelta(days=1)
    meals = meals_for_day(yesterday)
    totals = sum_macros(meals)
    sleep_records, sleep_error = fetch_oura("daily_sleep", days=180)
    sleep_detail_records, sleep_detail_error = fetch_oura("sleep", days=180)
    readiness_records, readiness_error = fetch_oura("daily_readiness", days=180)

    latest_sleep = latest_record(sleep_records, target_date)
    latest_readiness = latest_record(readiness_records, target_date)
    sleep_prior = previous_records(sleep_records, latest_sleep) if latest_sleep else []
    readiness_prior = previous_records(readiness_records, latest_readiness) if latest_readiness else []

    sleep_day = day_value(latest_sleep) if latest_sleep else None
    readiness_day = day_value(latest_readiness) if latest_readiness else None
    sleep_score = num(latest_sleep or {}, "score")
    readiness_score = num(latest_readiness or {}, "score")
    sleep_durations = sleep_duration_by_day(sleep_detail_records)
    sleep_hours = hours(sleep_durations.get(sleep_day)) if sleep_day else None
    prior_sleep_days = [day_value(record) for record in sleep_prior]
    prior_sleep_seconds = [sleep_durations[day] for day in prior_sleep_days if day in sleep_durations]
    usual_sleep_hours = hours(avg(prior_sleep_seconds))
    usual_sleep_score = avg([num(record, "score") for record in sleep_prior if num(record, "score") is not None])
    usual_readiness = avg([num(record, "score") for record in readiness_prior if num(record, "score") is not None])
    sleep_delta = None
    if sleep_hours is not None and usual_sleep_hours is not None:
        sleep_delta = round(sleep_hours - usual_sleep_hours, 2)

    nutrition, action = nutrition_insight(meals, totals)
    readiness = readiness_label(readiness_score, sleep_score, sleep_delta)

    lines = [
        f"Morning brief for {target_date.isoformat()}",
        "",
        f"Readiness: {readiness}",
    ]
    if latest_readiness:
        stale = f" ({readiness_day.isoformat()})" if readiness_day and readiness_day != target_date else ""
        trend = f"; usual ~{fmt(usual_readiness)}" if usual_readiness is not None else ""
        lines.append(f"Oura readiness: {fmt(readiness_score)}{stale}{trend}.")
    elif readiness_error:
        lines.append(f"Oura readiness: unavailable ({readiness_error}).")
    else:
        lines.append("Oura readiness: no recent record.")

    if latest_sleep:
        stale = f" ({sleep_day.isoformat()})" if sleep_day and sleep_day != target_date else ""
        score_trend = f"; usual score ~{fmt(usual_sleep_score)}" if usual_sleep_score is not None else ""
        duration_trend = ""
        if sleep_hours is not None and usual_sleep_hours is not None:
            direction = "above" if sleep_delta and sleep_delta > 0 else "below"
            duration_trend = f"; usual {fmt(usual_sleep_hours, 'h')} ({fmt(abs(sleep_delta or 0), 'h')} {direction})"
        lines.append(f"Sleep: {fmt(sleep_hours, 'h')}, score {fmt(sleep_score)}{stale}{score_trend}{duration_trend}.")
    elif sleep_error or sleep_detail_error:
        lines.append(f"Sleep: unavailable ({sleep_error or sleep_detail_error}).")
    else:
        lines.append("Sleep: no recent Oura sleep record.")

    if sleep_day and (target_date - sleep_day).days > 2:
        lines.append(f"Note: latest Oura sleep is stale by {(target_date - sleep_day).days} days, so treat readiness as low-confidence.")

    lines.extend(
        [
            "",
            f"Nutrition insight: {nutrition}",
            f"Action today: {action}",
        ]
    )
    return "\n".join(lines)


def send_telegram(message: str) -> None:
    if not OPENCLAW_BIN.exists():
        raise SystemExit(f"OpenClaw binary not found: {OPENCLAW_BIN}")
    command = [
        str(OPENCLAW_BIN),
        "message",
        "send",
        "--channel",
        "telegram",
        "--target",
        TELEGRAM_TARGET,
        "--message",
        message,
        "--json",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=45)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or completed.stdout.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Bryson's morning brief.")
    parser.add_argument("--date", help="Brief date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--send-telegram", action="store_true", help="Also send through OpenClaw Telegram.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_date = dt.date.fromisoformat(args.date) if args.date else now_local().date()
    brief = build_brief(target_date)
    print(brief)
    if args.send_telegram:
        send_telegram(brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
