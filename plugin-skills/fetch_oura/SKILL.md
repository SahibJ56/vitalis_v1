---
name: fetch_oura
description: "Fetch Oura Ring data from the official Oura API v2 for sleep, readiness, activity, heart rate, workouts, sessions, tags, SpO2, and profile data."
homepage: https://cloud.ouraring.com/docs/
metadata:
  {
    "openclaw":
      {
        "emoji": "💍",
        "requires": { "bins": ["python3"] },
        "primaryEnv": "OURA_CLIENT_ID",
      },
  }
---

# Fetch Oura

Use this skill when the user asks for Oura Ring data, trends, recovery/readiness, sleep, activity, heart rate, workouts, tags, sessions, SpO2, or personal profile data.

## Privacy and safety

- Oura data is health-adjacent and private. Do not share it outside the current user context.
- Never paste OAuth client secrets, access tokens, refresh tokens, or raw Oura data into public/shared chats.
- Use OAuth2. Store client credentials and tokens under `~/.openclaw/secrets/`, not in the workspace.
- Give practical pattern observations, not diagnosis. Encourage professional care for concerning symptoms or high-stakes decisions.
- If Oura patterns look concerning enough that a doctor visit may be useful, ask the user whether they want help booking. Never book or call automatically.

## Auth

Official docs: https://cloud.ouraring.com/docs/authentication

Oura uses OAuth2. Prefer a public HTTPS redirect URI if Oura rejects localhost during app creation. For local-only use, try this first:

```text
http://localhost:8765/callback
```

If Oura returns `invalid_redirect_uri` while creating the app, use a public HTTPS callback URL you control, for example:

```text
https://your-domain.example/oura/callback
```

Then set:

```bash
export OURA_CLIENT_ID="..."
export OURA_CLIENT_SECRET="..."
```

The helper also reads private client credentials from `~/.openclaw/secrets/oura_oauth_client.json`:

```json
{"client_id":"...","client_secret":"..."}
```

Run:

```bash
python3 scripts/fetch_oura.py --login
```

The helper opens Oura authorization, listens once on localhost, exchanges the code for tokens, and saves tokens with `0600` permissions.

For a public HTTPS redirect URI that does not point back to this machine, use manual code exchange:

```bash
python3 scripts/fetch_oura.py --authorize-url --redirect-uri "https://your-domain.example/oura/callback"
python3 scripts/fetch_oura.py --code "CODE_FROM_REDIRECT_URL" --redirect-uri "https://your-domain.example/oura/callback"
```

The `--code` redirect URI must exactly match the URI used in `--authorize-url` and registered in Oura.

Fetches still use:

```bash
Authorization: Bearer $OURA_ACCESS_TOKEN
```

but that access token comes from OAuth login/refresh. `OURA_ACCESS_TOKEN` is accepted only as a short-lived override.

Scopes may be required:

- `daily`: daily sleep, readiness, activity
- `heartrate`: heart-rate time series
- `workout`: workouts
- `tag`: tags
- `session`: sessions
- `spo2`: daily SpO2
- `personal`: profile data

If auth fails, ask the user to create/check the Oura API app, registered redirect URI, selected scopes, and active Oura Membership if relevant.

## Helper script

From this skill directory:

```bash
python3 scripts/fetch_oura.py --list
python3 scripts/fetch_oura.py --login
python3 scripts/fetch_oura.py --authorize-url --redirect-uri "https://your-domain.example/oura/callback"
python3 scripts/fetch_oura.py --code "CODE_FROM_REDIRECT_URL" --redirect-uri "https://your-domain.example/oura/callback"
python3 scripts/fetch_oura.py --refresh
python3 scripts/fetch_oura.py --kind daily_sleep --days 14
python3 scripts/fetch_oura.py --kind daily_readiness --start-date 2026-05-01 --end-date 2026-05-28
python3 scripts/fetch_oura.py --kind heartrate --start-datetime 2026-05-27T00:00:00 --end-datetime 2026-05-28T00:00:00
python3 scripts/fetch_oura.py --endpoint /v2/usercollection/daily_activity --days 7
```

Output is raw JSON by default. Use `--summary` for a small human-readable latest-record summary.

## Common endpoints

- `personal_info`: `/v2/usercollection/personal_info`
- `daily_activity`: `/v2/usercollection/daily_activity`
- `daily_readiness`: `/v2/usercollection/daily_readiness`
- `daily_sleep`: `/v2/usercollection/daily_sleep`
- `sleep`: `/v2/usercollection/sleep`
- `daily_spo2`: `/v2/usercollection/daily_spo2`
- `heartrate`: `/v2/usercollection/heartrate`
- `workout`: `/v2/usercollection/workout`
- `session`: `/v2/usercollection/session`
- `tag`: `/v2/usercollection/tag`
- `enhanced_tag`: `/v2/usercollection/enhanced_tag`

For newer endpoints not listed here, use `--endpoint /v2/usercollection/...`.

## Workflow

1. Fetch the narrowest useful date range.
2. Inspect the raw JSON keys before summarizing.
3. For trends, compare at least 7-14 days unless the user asks for a single day.
4. Mention missing data plainly: no ring wear, sync delay, missing scope, no membership/API access, or endpoint unsupported.
5. For sustained concerning patterns, such as unusually low readiness paired with elevated resting heart rate, major HRV drop, abnormal temperature trend, or repeatedly poor sleep with symptoms the user reports, suggest professional care and ask before using `book_appointment`.
