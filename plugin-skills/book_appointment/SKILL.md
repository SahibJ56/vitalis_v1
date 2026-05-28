---
name: book_appointment
description: "Make real outbound phone calls through ElevenLabs/Twilio to book appointments or restaurant reservations, with mandatory user confirmation before dialing."
homepage: https://elevenlabs.io/docs/api-reference/twilio/outbound-call/
metadata:
  {
    "openclaw":
      {
        "emoji": "☎️",
        "requires": { "bins": ["python3"] },
        "primaryEnv": "ELEVENLABS_API_KEY",
      },
  }
---

# Book Appointment

Use when the user wants a real phone call made to book a doctor appointment, other appointment, or restaurant reservation.

## Safety rule

Always confirm with the user before making a call. Tell them:

- who or what number will be called
- why the call is being made
- any important details that will be said on their behalf

Do not call until the user clearly confirms. The script enforces this with `--confirmed`.

If Oura Ring or meal-history patterns suggest a doctor appointment may be useful, ask the user first. Do not diagnose and do not book automatically.

## Credentials

The helper uses ElevenLabs' official outbound Twilio call endpoint:

```text
POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call
```

It needs:

```bash
export ELEVENLABS_API_KEY="..."
export ELEVENLABS_AGENT_ID="..."
export ELEVENLABS_AGENT_PHONE_NUMBER_ID="..."
```

It also reads:

```text
~/.openclaw/secrets/elevenlabs_appointment_call.json
```

with keys:

```json
{
  "api_key": "...",
  "agent_id": "...",
  "agent_phone_number_id": "..."
}
```

## Command

Dry-run the call payload:

```bash
python3 scripts/book_appointment.py --reason "Book a table for two tonight at 7 PM" --number "+14165550123" --dry-run --confirmed
```

Make the real call only after user confirmation:

```bash
python3 scripts/book_appointment.py --reason "Book a table for two tonight at 7 PM" --number "+14165550123" --confirmed
```

The default booking name is `Bryson`. Use `--booking-name` only if the user explicitly gives a different name:

```bash
python3 scripts/book_appointment.py --reason "Book a table for two tonight at 7 PM" --number "+14165550123" --booking-name "Different Name" --confirmed
```

The agent must not invent names. If the default ever needs to be removed, update the script instead of substituting a placeholder.

The helper checks that `agent_phone_number_id` is assigned to `agent_id` in ElevenLabs before dialing. If it reports an assignment mismatch, fix the phone number assignment in the ElevenLabs dashboard or update `~/.openclaw/secrets/elevenlabs_appointment_call.json`.

If the user gives a North American 10-digit number, the helper normalizes it to `+1...`. Prefer E.164 format when possible.

## Behavior

- Identifies itself as Vitalis, a personal AI assistant calling on behalf of the user.
- Keeps the call objective narrow: book the appointment or reservation described in `reason`.
- Does not invent symptoms, insurance, payment details, legal consent, or other personal data.
- Uses `Bryson` as the default booking name and does not invent other names.
- If the business needs information not provided, it asks for what is needed or says the user will follow up.
- Stores a small call audit log at `~/.openclaw/data/appointment_calls.json`.

## Appointment webhook

Use `scripts/appointment_webhook.py` to receive an ElevenLabs post-call/client-tool payload and relay the appointment result through the OpenClaw Gateway Telegram channel.

Local config:

```text
~/.openclaw/secrets/appointment_webhook.json
```

Fields:

- `telegram_bot_token`: Telegram bot token
- `telegram_chat_id`: Telegram chat to notify
- `webhook_secret`: required `X-Appointment-Webhook-Secret` header value
- `openclaw_delivery`: Gateway delivery target, usually `{"enabled": true, "channel": "telegram", "target": "<chat_id>"}`

Endpoint:

```text
POST /elevenlabs/appointment
```

Expected JSON can include:

- `status`
- `when`
- `what`
- `where`
- `confirmation`
- `summary`

The webhook stores received events at `~/.openclaw/data/appointment_webhook_events.json`.
