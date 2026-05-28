#!/usr/bin/env python3
"""Fetch Oura API v2 data using OAuth2."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


BASE_URL = "https://api.ouraring.com"
AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"
DEFAULT_SCOPES = "daily heartrate workout tag session spo2 personal"
DEFAULT_TOKEN_FILE = Path.home() / ".openclaw" / "secrets" / "oura_oauth_tokens.json"
DEFAULT_CLIENT_FILE = Path.home() / ".openclaw" / "secrets" / "oura_oauth_client.json"

ENDPOINTS = {
    "personal_info": "/v2/usercollection/personal_info",
    "daily_activity": "/v2/usercollection/daily_activity",
    "daily_readiness": "/v2/usercollection/daily_readiness",
    "daily_sleep": "/v2/usercollection/daily_sleep",
    "sleep": "/v2/usercollection/sleep",
    "daily_spo2": "/v2/usercollection/daily_spo2",
    "heartrate": "/v2/usercollection/heartrate",
    "workout": "/v2/usercollection/workout",
    "session": "/v2/usercollection/session",
    "tag": "/v2/usercollection/tag",
    "enhanced_tag": "/v2/usercollection/enhanced_tag",
}

NO_DATE_KINDS = {"personal_info"}
DATETIME_KINDS = {"heartrate"}


def env_or_arg(value: str | None, env_name: str) -> str | None:
    return value or os.environ.get(env_name)


def load_client_config(path: Path = DEFAULT_CLIENT_FILE) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def client_credentials(args: argparse.Namespace) -> tuple[str | None, str | None]:
    config = load_client_config()
    client_id = args.client_id or os.environ.get("OURA_CLIENT_ID") or config.get("client_id")
    client_secret = (
        args.client_secret
        or os.environ.get("OURA_CLIENT_SECRET")
        or config.get("client_secret")
    )
    return client_id, client_secret


def token_file_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else DEFAULT_TOKEN_FILE


def load_tokens(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_tokens(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token_payload = dict(payload)
    if "expires_in" in token_payload:
        token_payload["expires_at"] = int(time.time()) + int(token_payload["expires_in"])
    with path.open("w", encoding="utf-8") as handle:
        json.dump(token_payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def post_token_request(data: dict[str, str], client_id: str, client_secret: str) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "openclaw-fetch-oura/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(body_text)
        except json.JSONDecodeError:
            error = {"detail": body_text}
        raise SystemExit(json.dumps({"status": exc.code, "error": error}, indent=2))


def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    token_path: Path,
) -> dict:
    payload = post_token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        client_id,
        client_secret,
    )
    save_tokens(token_path, payload)
    return payload


def refresh_tokens(client_id: str, client_secret: str, token_path: Path) -> dict:
    current = load_tokens(token_path)
    refresh_token = current.get("refresh_token") or os.environ.get("OURA_REFRESH_TOKEN")
    if not refresh_token:
        raise SystemExit("No refresh token found. Run --login first.")
    payload = post_token_request(
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        client_id,
        client_secret,
    )
    save_tokens(token_path, payload)
    return payload


def access_token(args: argparse.Namespace) -> str:
    direct = os.environ.get("OURA_ACCESS_TOKEN")
    if direct:
        return direct

    token_path = token_file_path(args.token_file)
    tokens = load_tokens(token_path)
    token = tokens.get("access_token")
    expires_at = int(tokens.get("expires_at", 0) or 0)
    if token and expires_at > int(time.time()) + 60:
        return token

    client_id, client_secret = client_credentials(args)
    if token and not client_id:
        return token
    if client_id and client_secret:
        refreshed = refresh_tokens(client_id, client_secret, token_path)
        return refreshed["access_token"]

    raise SystemExit(
        "Missing Oura OAuth tokens. Run --login with OURA_CLIENT_ID and "
        "OURA_CLIENT_SECRET set, or provide a short-lived OURA_ACCESS_TOKEN."
    )


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "OuraOAuthCallback/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = params.get("code", [None])[0]  # type: ignore[attr-defined]
        self.server.auth_state = params.get("state", [None])[0]  # type: ignore[attr-defined]
        self.server.auth_error = params.get("error", [None])[0]  # type: ignore[attr-defined]
        ok = self.server.auth_code and not self.server.auth_error  # type: ignore[attr-defined]
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if ok:
            self.wfile.write(b"Oura authorization complete. You can close this tab.")
        else:
            self.wfile.write(b"Oura authorization failed. Return to the terminal.")


def login(args: argparse.Namespace) -> int:
    client_id, client_secret = client_credentials(args)
    if not client_id or not client_secret:
        raise SystemExit("Set OURA_CLIENT_ID and OURA_CLIENT_SECRET before running --login.")

    redirect_uri = args.redirect_uri
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("This helper only listens on localhost redirect URIs.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": args.scopes,
        "state": state,
    }
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    server = http.server.HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
    server.auth_code = None  # type: ignore[attr-defined]
    server.auth_state = None  # type: ignore[attr-defined]
    server.auth_error = None  # type: ignore[attr-defined]

    print(f"Opening Oura authorization URL for scopes: {args.scopes}", file=sys.stderr)
    print(auth_url, file=sys.stderr)
    webbrowser.open(auth_url)
    server.handle_request()

    if server.auth_error:  # type: ignore[attr-defined]
        raise SystemExit(f"Oura authorization failed: {server.auth_error}")  # type: ignore[attr-defined]
    if server.auth_state != state:  # type: ignore[attr-defined]
        raise SystemExit("OAuth state mismatch; refusing token exchange.")
    if not server.auth_code:  # type: ignore[attr-defined]
        raise SystemExit("No authorization code received.")

    exchange_code(
        server.auth_code,  # type: ignore[arg-type,attr-defined]
        client_id,
        client_secret,
        redirect_uri,
        token_file_path(args.token_file),
    )
    print(f"Saved Oura OAuth tokens to {token_file_path(args.token_file)}")
    return 0


def print_authorize_url(args: argparse.Namespace) -> int:
    client_id, _ = client_credentials(args)
    if not client_id:
        raise SystemExit("Set OURA_CLIENT_ID before generating an authorization URL.")
    state = args.state or secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": args.redirect_uri,
        "scope": args.scopes,
        "state": state,
    }
    print(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))
    print(f"state={state}", file=sys.stderr)
    return 0


def exchange_manual_code(args: argparse.Namespace) -> int:
    client_id, client_secret = client_credentials(args)
    if not client_id or not client_secret:
        raise SystemExit("Set OURA_CLIENT_ID and OURA_CLIENT_SECRET before exchanging a code.")
    exchange_code(
        args.code,
        client_id,
        client_secret,
        args.redirect_uri,
        token_file_path(args.token_file),
    )
    print(f"Saved Oura OAuth tokens to {token_file_path(args.token_file)}")
    return 0


def iso_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def default_dates(days: int) -> tuple[str, str]:
    end = dt.date.today()
    start = end - dt.timedelta(days=max(days - 1, 0))
    return start.isoformat(), end.isoformat()


def normalize_endpoint(endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return BASE_URL + endpoint


def build_params(args: argparse.Namespace, kind: str | None) -> dict[str, str]:
    if kind in NO_DATE_KINDS:
        return {}

    params: dict[str, str] = {}
    if kind in DATETIME_KINDS or args.start_datetime or args.end_datetime:
        if args.start_datetime:
            params["start_datetime"] = args.start_datetime
        if args.end_datetime:
            params["end_datetime"] = args.end_datetime
        return params

    start, end = args.start_date, args.end_date
    if not start and not end:
        start, end = default_dates(args.days)
    if start:
        params["start_date"] = start
    if end:
        params["end_date"] = end
    return params


def fetch_json(url: str, token: str, params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode(params)
    full_url = url + (("?" + query) if query else "")
    req = urllib.request.Request(
        full_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "openclaw-fetch-oura/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(body)
        except json.JSONDecodeError:
            error = {"detail": body}
        raise SystemExit(json.dumps({"status": exc.code, "error": error}, indent=2))
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error: {exc.reason}")


def latest_record(payload: dict) -> dict | None:
    data = payload.get("data")
    if isinstance(data, list) and data:
        return data[-1]
    if isinstance(data, dict):
        return data
    return None


def print_summary(payload: dict) -> None:
    record = latest_record(payload)
    if not record:
        print("No records returned.")
        return
    interesting = [
        "day",
        "timestamp",
        "score",
        "readiness_score",
        "sleep_score",
        "activity_score",
        "total_sleep_duration",
        "efficiency",
        "steps",
        "active_calories",
        "average_bpm",
        "bpm",
        "spo2_percentage",
    ]
    summary = {key: record[key] for key in interesting if key in record}
    if not summary:
        keys = ", ".join(sorted(record.keys())[:20])
        print(f"Latest record keys: {keys}")
        return
    print(json.dumps(summary, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Oura API v2 data.")
    parser.add_argument("--login", action="store_true", help="Run local OAuth2 authorization flow.")
    parser.add_argument("--authorize-url", action="store_true", help="Print an OAuth2 authorization URL.")
    parser.add_argument("--code", help="Exchange a manually copied authorization code.")
    parser.add_argument("--state", help="State value for --authorize-url.")
    parser.add_argument("--refresh", action="store_true", help="Refresh and save OAuth2 tokens.")
    parser.add_argument("--client-id", help="OAuth client ID. Defaults to OURA_CLIENT_ID.")
    parser.add_argument("--client-secret", help="OAuth client secret. Defaults to OURA_CLIENT_SECRET.")
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI, help="Registered OAuth redirect URI.")
    parser.add_argument("--scopes", default=DEFAULT_SCOPES, help="Space-separated OAuth scopes.")
    parser.add_argument("--token-file", help=f"OAuth token cache. Defaults to {DEFAULT_TOKEN_FILE}.")
    parser.add_argument("--kind", choices=sorted(ENDPOINTS), help="Known endpoint shortcut.")
    parser.add_argument("--endpoint", help="Raw endpoint path or full URL.")
    parser.add_argument("--start-date", type=iso_date, help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", type=iso_date, help="End date, YYYY-MM-DD.")
    parser.add_argument("--start-datetime", help="Start datetime for time-series endpoints.")
    parser.add_argument("--end-datetime", help="End datetime for time-series endpoints.")
    parser.add_argument("--days", type=int, default=7, help="Default date range ending today.")
    parser.add_argument("--summary", action="store_true", help="Print a compact latest-record summary.")
    parser.add_argument("--output", help="Write JSON to this file.")
    parser.add_argument("--list", action="store_true", help="List known endpoint shortcuts.")
    args = parser.parse_args()

    if args.list:
        for name, endpoint in sorted(ENDPOINTS.items()):
            print(f"{name}\t{endpoint}")
        raise SystemExit(0)
    if args.login or args.refresh or args.authorize_url or args.code:
        return args
    if not args.kind and not args.endpoint:
        parser.error("choose --kind or --endpoint")
    if args.kind and args.endpoint:
        parser.error("choose only one of --kind or --endpoint")
    return args


def main() -> int:
    args = parse_args()
    if args.login:
        return login(args)
    if args.authorize_url:
        return print_authorize_url(args)
    if args.code:
        return exchange_manual_code(args)
    if args.refresh:
        client_id, client_secret = client_credentials(args)
        if not client_id or not client_secret:
            raise SystemExit("Set OURA_CLIENT_ID and OURA_CLIENT_SECRET before refreshing.")
        refresh_tokens(client_id, client_secret, token_file_path(args.token_file))
        print(f"Refreshed Oura OAuth tokens in {token_file_path(args.token_file)}")
        return 0

    kind = args.kind
    endpoint = ENDPOINTS[kind] if kind else args.endpoint
    assert endpoint is not None
    url = normalize_endpoint(endpoint)
    payload = fetch_json(url, access_token(args), build_params(args, kind))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    elif args.summary:
        print_summary(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
