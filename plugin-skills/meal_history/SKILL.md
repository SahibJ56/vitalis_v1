---
name: meal_history
description: "Log meals with names, macros, and timestamps, and fetch recent meal history from a local JSON food log."
metadata:
  {
    "openclaw":
      {
        "emoji": "🍽️",
        "requires": { "bins": ["python3"] },
      },
  }
---

# Meal History

Use when the user wants to log food, meals, calories, protein, carbs, fat, fiber, sugar, sodium, or fetch recent meal history.

Data lives in:

```text
~/.openclaw/data/meal_history.json
```

## Commands

Log a meal:

```bash
python3 scripts/log_meal_history.py --name "Greek yogurt" --calories 180 --protein 18 --carbs 12 --fat 4
python3 scripts/log_meal_history.py --name "Dinner" --macros '{"calories":720,"protein":42,"carbs":78,"fat":24}' --notes "post-workout"
python3 scripts/log_meal_history.py --name "Coffee" --logged-at "2026-05-28T08:15:00-04:00"
```

Fetch meals:

```bash
python3 scripts/fetch_meal_history.py --days 1
python3 scripts/fetch_meal_history.py --days 7 --summary
```

## Tailnet Food API

An always-on local API accepts meal logs from Bryson's Android app through Tailscale Funnel:

```text
POST https://bryson-yoga-pro-9-16imh9.tail9f2dac.ts.net/meal/log
```

Headers:

```text
Content-Type: application/json
X-Appointment-Webhook-Secret: <local webhook secret>
```

Body:

```json
{
  "name": "Chicken rice bowl",
  "calories": 650,
  "protein": 45,
  "carbs": 70,
  "fat": 18,
  "logged_at": "2026-05-28T12:30:00-04:00",
  "notes": "optional"
}
```

`logged_at` and `notes` are optional. If `logged_at` is omitted, the server uses the current local time.

## Schema

Each entry:

- `id`: UUID
- `logged_at`: ISO 8601 timestamp
- `name`: meal/food name
- `macros`: object with numeric values such as `calories`, `protein`, `carbs`, `fat`, `fiber`, `sugar`, `sodium`
- `notes`: optional free text
- `created_at`: ISO 8601 timestamp

## Notes

- If the user gives approximate macros, store them as given and mark uncertainty in `notes` if helpful.
- If the user omits time, use current local time.
- Fetch uses `days` as lookback from now.
- If diet history suggests a possible medical issue or the user reports symptoms, ask whether they want help booking a doctor appointment. Never diagnose and never call automatically.
