"""Browser OAuth flow for sharktopus's Cloud Run deploy.

Uses Google's "installed application" OAuth flow — pops a local browser,
runs a one-shot ``localhost:0`` HTTP listener for the callback,
exchanges the auth code for a refresh+access token, and caches the
result at ``~/.cache/sharktopus/gcloud_token.json`` (mode 0o600).
Subsequent runs reuse the cached refresh token without opening a
browser.

This is the pure-Python alternative to ``gcloud auth login`` that does
not require the gcloud CLI.

The OAuth client JSON (``oauth_client.json``) is a per-tool Desktop
OAuth client registered in the ``sharktopus-oauth`` GCP project. Google
documents the "client secret" field as non-secret for Desktop apps
(``https://developers.google.com/identity/protocols/oauth2#installed``),
but we keep the JSON gitignored during development. End users will
eventually get a bundled copy inside the sharktopus wheel once the app
clears Google's verification review.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("sharktopus-gcloud-deploy")

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

DEFAULT_OAUTH_CLIENT = Path(__file__).resolve().parent / "oauth_client.json"
TOKEN_CACHE = Path.home() / ".cache" / "sharktopus" / "gcloud_token.json"


def login(
    oauth_client_json: str | Path | None = None,
    token_cache: str | Path | None = None,
):
    """Return a ``google.oauth2.credentials.Credentials`` via browser OAuth.

    First call opens a browser and listens on a random localhost port
    for the OAuth callback, then persists a refresh token to the cache.
    Subsequent calls read the cache and silently refresh the access
    token when it expires — no browser prompt.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise SystemExit(
            "Browser OAuth needs `google-auth-oauthlib` and `google-auth`.\n"
            "Install with: pip install google-auth-oauthlib google-auth"
        ) from e

    client_path = Path(oauth_client_json) if oauth_client_json else DEFAULT_OAUTH_CLIENT
    cache_path = Path(token_cache) if token_cache else TOKEN_CACHE

    if not client_path.exists():
        raise SystemExit(
            f"OAuth client JSON not found at {client_path}.\n"
            "Download it from GCP Console → APIs & Services → Credentials\n"
            "→ (Desktop OAuth client) → Download JSON, and pass it via\n"
            "--oauth-client-json or drop it at the default path above."
        )

    creds = None
    if cache_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(cache_path), SCOPES)
        except ValueError:
            log.warning("Cached token at %s is invalid; re-authenticating", cache_path)
            creds = None

    if creds and creds.valid:
        log.info("Using cached OAuth token from %s", cache_path)
        return creds

    if creds and creds.expired and creds.refresh_token:
        log.info("Refreshing expired OAuth token")
        try:
            creds.refresh(Request())
            _save_token(creds, cache_path)
            return creds
        except Exception as e:  # noqa: BLE001 — any refresh failure → re-auth
            log.warning("Token refresh failed (%s); running browser flow", e)

    log.info("Starting browser OAuth flow (opens localhost:0 listener)")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        authorization_prompt_message="",
        success_message=(
            "sharktopus login succeeded. You can close this browser tab."
        ),
        open_browser=True,
    )
    _save_token(creds, cache_path)
    return creds


def _save_token(creds, cache_path: Path) -> None:
    """Write token JSON to *cache_path* and tighten perms to 0o600."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(creds.to_json())
    try:
        cache_path.chmod(0o600)
    except OSError:
        pass
    log.info("Cached OAuth token at %s (0600)", cache_path)
