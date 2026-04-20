"""Browser-OAuth client-side authentication for Cloud Run invocation.

Pairs with ``deploy/gcloud/_oauth_browser.py`` on the deploy side.
The deploy step drops a refresh-token-bearing JSON at
``~/.cache/sharktopus/gcloud_token.json`` and creates a
``sharktopus-invoker@<project>.iam.gserviceaccount.com`` service
account bound as ``roles/run.invoker`` on the Cloud Run service.
The user is granted ``roles/iam.serviceAccountTokenCreator`` on that
SA, so this module can impersonate it (via ``generateIdToken``) to
mint audience-scoped ID tokens for Cloud Run invocation — all
without a downloaded service-account key.

The flow, step-by-step, when ``mint_id_token_via_browser_cache`` is
called:

1. Load the cached user OAuth credentials (or return ``None`` — the
   caller falls through to other auth paths).
2. Resolve the invoker SA email from ``SHARKTOPUS_GCLOUD_INVOKER_SA``
   or construct it from ``GOOGLE_CLOUD_PROJECT``.
3. POST to
   ``iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/<sa>:generateIdToken``
   with ``{"audience": <service URL>, "includeEmail": true}``.
4. Return the token string.

All failures return ``None`` rather than raising — the Cloud Run
client has a three-way fallback and we don't want to take it down.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("sharktopus")

CACHE_PATH = Path.home() / ".cache" / "sharktopus" / "gcloud_token.json"
INVOKER_SA_ID = "sharktopus-invoker"


def mint_id_token_via_browser_cache(audience: str) -> str | None:
    """Return an ID token minted by impersonating the invoker SA.

    Returns ``None`` on any failure — the caller should fall through
    to the next auth path (gcloud CLI, metadata server, ...).
    """
    if not CACHE_PATH.exists():
        return None

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import AuthorizedSession, Request
    except ImportError:
        return None

    scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
    ]
    try:
        creds = Credentials.from_authorized_user_file(str(CACHE_PATH), scopes)
    except (ValueError, OSError) as e:
        log.debug("browser-cache credentials unreadable: %s", e)
        return None

    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            log.debug("browser-cache credentials expired and unrefreshable")
            return None
        try:
            creds.refresh(Request())
        except Exception as e:  # noqa: BLE001
            log.debug("browser-cache refresh failed: %s", e)
            return None

    sa_email = _resolve_invoker_sa()
    if not sa_email:
        log.debug(
            "no invoker SA configured; set SHARKTOPUS_GCLOUD_INVOKER_SA "
            "or GOOGLE_CLOUD_PROJECT",
        )
        return None

    try:
        session = AuthorizedSession(creds)
        url = (
            "https://iamcredentials.googleapis.com/v1/projects/-"
            f"/serviceAccounts/{sa_email}:generateIdToken"
        )
        r = session.post(
            url,
            json={"audience": audience, "includeEmail": True},
            timeout=30,
        )
        if r.status_code != 200:
            log.debug(
                "generateIdToken failed (%d): %s",
                r.status_code, r.text[:300],
            )
            return None
        return r.json().get("token") or None
    except Exception as e:  # noqa: BLE001
        log.debug("generateIdToken raised: %s", e)
        return None


def _resolve_invoker_sa() -> str | None:
    """Resolve the invoker SA email from env, or construct from project."""
    explicit = os.environ.get("SHARKTOPUS_GCLOUD_INVOKER_SA", "").strip()
    if explicit:
        return explicit
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        return None
    return f"{INVOKER_SA_ID}@{project}.iam.gserviceaccount.com"
