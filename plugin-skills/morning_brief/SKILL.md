---
name: morning_brief
description: "Create a morning health brief from yesterday's meal log and recent Oura sleep/readiness trends."
metadata:
  {
    "openclaw":
      {
        "emoji": "🌅",
        "requires": { "bins": ["python3"] },
      },
  }
---

# Morning Brief

Use when Bryson asks for a morning brief, readiness for the day, last night's Oura sleep/readiness compared with usual trends, or yesterday's nutrition insight.

## Command

Run manually:

```bash
python3 scripts/send_morning_brief.py
```

Optional:

```bash
python3 scripts/send_morning_brief.py --date 2026-05-28
python3 scripts/send_morning_brief.py --send-telegram
```

## Output

The brief should include:

- readiness for today: take it easy / normal day / green light
- last night's sleep compared with recent Oura trend
- yesterday's top nutrition insight from the meal log
- one specific action to take today

If Oura or meal data is missing, say that plainly and give a fallback action.
