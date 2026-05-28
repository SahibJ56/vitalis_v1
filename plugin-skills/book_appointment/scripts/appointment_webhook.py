#!/usr/bin/env python3
"""Receive appointment summaries and relay them to Telegram."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


CONFIG_PATH = Path("~/.openclaw/secrets/appointment_webhook.json").expanduser()
EVENT_LOG_PATH = Path("~/.openclaw/data/appointment_webhook_events.json").expanduser()
MEAL_STORE_PATH = Path("~/.openclaw/data/meal_history.json").expanduser()
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
OPENCLAW_BIN = os.getenv("OPENCLAW_BIN", "/home/bryson2/.npm-global/bin/openclaw")
MEAL_MACRO_FIELDS = ("calories", "protein", "carbs", "fat")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing config: {path}")
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return config


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp.chmod(0o600)
    temp.replace(path)


def append_event(event: dict[str, Any], path: Path = EVENT_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            events = json.load(handle)
        if not isinstance(events, list):
            raise RuntimeError(f"{path} must contain a JSON array")
    else:
        events = []
    events.append(event)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(events, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp.replace(path)


def parse_datetime(value: str) -> dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_iso_datetime().tzinfo)
    return parsed.astimezone()


def now_iso_datetime() -> dt.datetime:
    return dt.datetime.now().astimezone()


def iso(value: dt.datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")


def load_meal_entries(path: Path = MEAL_STORE_PATH) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload["entries"]
    raise RuntimeError(f"Unexpected meal history JSON shape in {path}")


def save_meal_entries(entries: list[dict[str, Any]], path: Path = MEAL_STORE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump({"version": 1, "entries": entries}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp.replace(path)
    path.chmod(0o600)


def numeric_field(payload: dict[str, Any], key: str, required: bool = True) -> float | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError(f"Missing required field: {key}")
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def log_meal_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name") or payload.get("food") or payload.get("meal")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Missing required field: name")
    macros = {field: numeric_field(payload, field) for field in MEAL_MACRO_FIELDS}
    logged_at_raw = payload.get("logged_at") or payload.get("timestamp") or payload.get("time")
    if logged_at_raw is None:
        logged_at = now_iso_datetime()
    elif isinstance(logged_at_raw, str):
        logged_at = parse_datetime(logged_at_raw)
    else:
        raise ValueError("logged_at must be an ISO 8601 string")
    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValueError("notes must be a string")
    entry = {
        "id": str(uuid.uuid4()),
        "logged_at": iso(logged_at),
        "name": name.strip(),
        "macros": macros,
        "notes": notes,
        "created_at": now_iso(),
        "source": "tailnet_food_api",
    }
    entries = load_meal_entries()
    entries.append(entry)
    entries.sort(key=lambda item: item.get("logged_at", ""))
    save_meal_entries(entries)
    return entry


def telegram_api(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram {method} failed: HTTP {exc.code}: {detail}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result


def discover_chat_id(config: dict[str, Any]) -> str | None:
    token = config["telegram_bot_token"]
    updates = telegram_api(token, "getUpdates").get("result", [])
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if isinstance(chat, dict) and chat.get("id") is not None:
            chat_id = str(chat["id"])
            config["telegram_chat_id"] = chat_id
            save_config(config)
            return chat_id
    return None


def format_appointment_message(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or payload.get("appointment_summary") or payload.get("result")
    when = payload.get("when") or payload.get("appointment_time") or payload.get("time")
    what = payload.get("what") or payload.get("appointment_type") or payload.get("reason")
    where = payload.get("where") or payload.get("location") or payload.get("business_name")
    status = payload.get("status") or payload.get("booking_status")
    confirmation = payload.get("confirmation") or payload.get("confirmation_details")

    lines = ["Appointment update"]
    if status:
        lines.append(f"Status: {status}")
    if when:
        lines.append(f"When: {when}")
    if what:
        lines.append(f"What: {what}")
    if where:
        lines.append(f"Where: {where}")
    if confirmation:
        lines.append(f"Confirmation: {confirmation}")
    if summary:
        lines.append(f"Summary: {summary}")

    extra = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "summary",
            "appointment_summary",
            "result",
            "when",
            "appointment_time",
            "time",
            "what",
            "appointment_type",
            "reason",
            "where",
            "location",
            "business_name",
            "status",
            "booking_status",
            "confirmation",
            "confirmation_details",
        }
        and value not in (None, "", [])
    }
    if extra:
        lines.append("Details: " + json.dumps(extra, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def send_telegram(config: dict[str, Any], text: str) -> dict[str, Any]:
    token = config["telegram_bot_token"]
    chat_id = config.get("telegram_chat_id") or discover_chat_id(config)
    if not chat_id:
        raise RuntimeError("Telegram chat ID is not configured. Send /start to the bot, then run --discover-chat.")
    return telegram_api(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_openclaw(config: dict[str, Any], text: str) -> dict[str, Any]:
    delivery = config.get("openclaw_delivery") or {}
    channel = delivery.get("channel", "telegram")
    target = str(delivery.get("target") or config.get("telegram_chat_id") or "")
    if not target:
        raise RuntimeError("OpenClaw delivery target is not configured.")
    command = [
        OPENCLAW_BIN,
        "message",
        "send",
        "--channel",
        channel,
        "--target",
        target,
        "--message",
        text,
        "--json",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=45)
    if completed.returncode != 0:
        raise RuntimeError(f"OpenClaw message send failed: {completed.stderr.strip() or completed.stdout.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"ok": True, "stdout": completed.stdout.strip()}


def send_notification(config: dict[str, Any], text: str) -> dict[str, Any]:
    delivery = config.get("openclaw_delivery") or {}
    if delivery.get("enabled", True):
        return send_openclaw(config, text)
    return send_telegram(config, text)


class Handler(BaseHTTPRequestHandler):
    server_version = "AppointmentWebhook/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True})
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        config = load_config()
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/elevenlabs/appointment", "/meal/log"}:
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        expected = config.get("webhook_secret")
        provided = self.headers.get("X-Appointment-Webhook-Secret")
        if expected and provided != expected:
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
            if parsed.path == "/meal/log":
                entry = log_meal_from_payload(payload)
                self.send_json(200, {"ok": True, "entry": entry})
                return
            text = format_appointment_message(payload)
            response = send_notification(config, text)
            event = {
                "created_at": now_iso(),
                "payload": payload,
                "delivery_response": response,
            }
            append_event(event)
            self.send_json(200, {"ok": True, "sent": True})
        except Exception as exc:
            append_event({"created_at": now_iso(), "error": str(exc)})
            self.send_json(500, {"ok": False, "error": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Appointment webhook relay for ElevenLabs and Telegram.")
    parser.add_argument("--host", default=os.getenv("APPOINTMENT_WEBHOOK_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("APPOINTMENT_WEBHOOK_PORT", DEFAULT_PORT)))
    parser.add_argument("--discover-chat", action="store_true", help="Discover and save Telegram chat_id from bot updates.")
    parser.add_argument("--send-test", action="store_true", help="Send a Telegram test message.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config()
    if args.discover_chat:
        chat_id = discover_chat_id(config)
        if chat_id:
            print(f"saved telegram_chat_id={chat_id}")
            return 0
        print("No Telegram chat found. Send /start to the bot, then run --discover-chat again.")
        return 1
    if args.send_test:
        send_notification(config, "Vitalis appointment webhook test is working through OpenClaw.")
        print("sent appointment webhook test message")
        return 0
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"appointment webhook listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
