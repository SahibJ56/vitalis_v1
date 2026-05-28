#!/usr/bin/env python3
"""Place an ElevenLabs/Twilio outbound call to book an appointment."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


API_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
PHONE_NUMBER_API_URL = "https://api.elevenlabs.io/v1/convai/phone-numbers"
CONFIG_PATH = Path("~/.openclaw/secrets/elevenlabs_appointment_call.json").expanduser()
CALL_LOG_PATH = Path("~/.openclaw/data/appointment_calls.json").expanduser()
DEFAULT_BOOKING_NAME = "Bryson"


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return data


def load_config(path: Path) -> dict[str, str]:
    file_config = read_json(path)
    config = {
        "api_key": os.getenv("ELEVENLABS_API_KEY") or file_config.get("api_key", ""),
        "agent_id": os.getenv("ELEVENLABS_AGENT_ID") or file_config.get("agent_id", ""),
        "agent_phone_number_id": os.getenv("ELEVENLABS_AGENT_PHONE_NUMBER_ID")
        or file_config.get("agent_phone_number_id", ""),
    }
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise SystemExit(
            "Missing ElevenLabs config: "
            + ", ".join(missing)
            + f". Set env vars or create {path}."
        )
    return config


def normalize_phone(number: str) -> str:
    raw = number.strip()
    compact = re.sub(r"[\s().-]", "", raw)
    if re.fullmatch(r"\+[1-9]\d{7,14}", compact):
        return compact
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    raise SystemExit(
        "Phone number must be E.164, like +14165550123, or a North American 10-digit number."
    )


def appointment_prompt(reason: str, called_name: str | None, booking_name: str | None) -> str:
    target = called_name or "the person or business at the dialed number"
    booking_name_instruction = (
        f"The booking name is {booking_name}."
        if booking_name
        else "No booking name was provided. Do not invent one. Ask whether the booking can be held under the caller's phone number or say the user will follow up with a name."
    )
    return (
        "You are Vitalis, a personal AI assistant calling on behalf of Bryson. "
        f"You are calling {target}. Your objective is: {reason}. "
        f"{booking_name_instruction} "
        "Be polite, concise, and transparent that you are an AI assistant. "
        "Book the appointment or reservation if possible, confirm date, time, name, location, and any callback details. "
        "Never invent names, symptoms, insurance, payment details, consent, or other personal information. "
        "If required details are missing, ask what information is needed or say the user will follow up."
    )


def build_payload(args: argparse.Namespace, config: dict[str, str], to_number: str) -> dict[str, Any]:
    booking_name = args.booking_name or DEFAULT_BOOKING_NAME
    prompt = appointment_prompt(args.reason, args.called_name, booking_name)
    dynamic_variables = {
        "appointment_reason": args.reason,
        "called_number": to_number,
        "called_name": args.called_name or "",
        "booking_name": booking_name,
        "caller_identity": "Vitalis, a personal AI assistant calling on behalf of Bryson",
    }
    return {
        "agent_id": config["agent_id"],
        "agent_phone_number_id": config["agent_phone_number_id"],
        "to_number": to_number,
        "call_recording_enabled": bool(args.record),
        "conversation_initiation_client_data": {
            "conversation_config_override": {
                "agent": {
                    "prompt": {"prompt": prompt},
                    "first_message": (
                        "Hi, this is Vitalis, a personal AI assistant calling on behalf of Bryson. "
                        f"I'm calling to {args.reason}. "
                        f"The booking name is {booking_name}."
                    ),
                    "language": "en",
                }
            },
            "dynamic_variables": dynamic_variables,
        },
    }


def get_phone_number(phone_number_id: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{PHONE_NUMBER_API_URL}/{phone_number_id}",
        headers={"xi-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ElevenLabs phone preflight failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"ElevenLabs phone preflight failed: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"ElevenLabs phone preflight returned unexpected response: {data!r}")
    return data


def preflight_phone_assignment(config: dict[str, str]) -> None:
    phone = get_phone_number(config["agent_phone_number_id"], config["api_key"])
    assigned = phone.get("assigned_agent") or {}
    assigned_agent_id = assigned.get("agent_id") if isinstance(assigned, dict) else None
    if assigned_agent_id != config["agent_id"]:
        phone_number = phone.get("phone_number", config["agent_phone_number_id"])
        assigned_name = assigned.get("agent_name") if isinstance(assigned, dict) else None
        raise SystemExit(
            "ElevenLabs phone number is assigned to a different agent. "
            f"{phone_number} is assigned to {assigned_agent_id or 'no agent'}"
            f"{f' ({assigned_name})' if assigned_name else ''}, "
            f"but config uses {config['agent_id']}. "
            "Assign the phone number to the appointment-booking agent in ElevenLabs before dialing."
        )
    if phone.get("supports_outbound") is False:
        raise SystemExit("ElevenLabs phone number does not support outbound calls.")


def post_call(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ElevenLabs call failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"ElevenLabs call failed: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ElevenLabs returned non-JSON response: {text}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"ElevenLabs returned unexpected response: {data!r}")
    return data


def append_call_log(entry: dict[str, Any], path: Path = CALL_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
        if not isinstance(existing, list):
            raise SystemExit(f"{path} must contain a JSON array")
    else:
        existing = []
    existing.append(entry)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Call a number via ElevenLabs to book an appointment.")
    parser.add_argument("--reason", required=True, help="Appointment or reservation objective.")
    parser.add_argument("--number", required=True, help="Phone number to call. E.164 preferred.")
    parser.add_argument("--called-name", help="Business, clinic, restaurant, or person being called.")
    parser.add_argument(
        "--booking-name",
        default=DEFAULT_BOOKING_NAME,
        help=f"Name to use for the booking. Defaults to {DEFAULT_BOOKING_NAME}.",
    )
    parser.add_argument("--confirmed", action="store_true", help="Required after explicit user confirmation.")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without calling.")
    parser.add_argument("--record", action="store_true", help="Ask Twilio/ElevenLabs to record the call.")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to ElevenLabs appointment-call config JSON.")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip ElevenLabs phone/agent assignment check.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    to_number = normalize_phone(args.number)
    if not args.confirmed:
        print(
            "Confirmation required before dialing.\n"
            f"Would call: {args.called_name or to_number} ({to_number})\n"
            f"Reason: {args.reason}\n"
            "Re-run with --confirmed only after the user explicitly approves.",
            file=sys.stderr,
        )
        return 2

    config = load_config(Path(args.config).expanduser())
    if not args.skip_preflight:
        preflight_phone_assignment(config)
    payload = build_payload(args, config, to_number)
    redacted_payload = json.loads(json.dumps(payload))
    if args.dry_run:
        print(json.dumps(redacted_payload, indent=2, sort_keys=True))
        return 0

    response = post_call(payload, config["api_key"])
    log_entry = {
        "id": str(uuid.uuid4()),
        "created_at": now_iso(),
        "called_name": args.called_name,
        "to_number": to_number,
        "reason": args.reason,
        "recording_requested": bool(args.record),
        "elevenlabs_success": response.get("success"),
        "elevenlabs_message": response.get("message"),
        "conversation_id": response.get("conversation_id"),
        "call_sid": response.get("callSid"),
    }
    append_call_log(log_entry)
    print(json.dumps(log_entry, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
