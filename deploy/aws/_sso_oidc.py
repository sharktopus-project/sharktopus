"""Pure-Python AWS SSO device-code flow.

Equivalent to ``aws sso login`` but without requiring the AWS CLI on the
host. Talks to the ``sso-oidc`` and ``sso`` services using boto3 (which
``provision.py`` already depends on), opens the user's browser at the
verification URL, polls for the access token, then exchanges it for
short-lived role credentials via ``sso:GetRoleCredentials``.

The resulting credentials are returned as a dict suitable for
``boto3.Session(aws_access_key_id=..., aws_secret_access_key=...,
aws_session_token=...)``. They're also cached to
``~/.cache/sharktopus/aws_sso_token.json`` so the user doesn't have to
re-authorize the browser on every run within the token's lifetime
(usually 8-12 h).

This module is deliberately stdlib-+-boto3 only; no `aws` binary, no
``~/.aws/`` files read, no writes outside ``~/.cache/sharktopus/``.
"""

from __future__ import annotations

import json
import logging
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("sharktopus-deploy")

CACHE_DIR = Path.home() / ".cache" / "sharktopus"
TOKEN_CACHE = CACHE_DIR / "aws_sso_token.json"
CLIENT_NAME = "sharktopus-deployer"
CLIENT_TYPE = "public"
SCOPES = ["sso:account:access"]


@dataclass
class SsoCreds:
    """STS-style temporary credentials returned by ``sso:GetRoleCredentials``."""
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration_ms: int  # epoch ms

    def as_session_kwargs(self) -> dict[str, str]:
        return {
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            "aws_session_token": self.session_token,
        }


def login(
    start_url: str,
    sso_region: str,
    *,
    account_id: str | None = None,
    role_name: str | None = None,
    on_device_code=None,
) -> tuple[SsoCreds, str, str]:
    """Run the device-code flow and return (creds, account_id, role_name).

    Reuses the cached SSO access token when it's still valid. Otherwise
    prints the verification URL + code, opens the browser, and polls.

    If *account_id* / *role_name* aren't given, the user is prompted
    from the lists returned by ``sso:ListAccounts`` / ``sso:ListAccountRoles``.
    """
    access_token = _load_cached_token(start_url)
    if access_token is None:
        access_token = _device_code_login(start_url, sso_region, on_device_code)

    sso = boto3.client("sso", region_name=sso_region)

    if account_id is None:
        account_id = _pick_account(sso, access_token)
    if role_name is None:
        role_name = _pick_role(sso, access_token, account_id)

    log.info("Exchanging SSO access token for role credentials (%s / %s)",
             account_id, role_name)
    resp = sso.get_role_credentials(
        accessToken=access_token,
        accountId=account_id,
        roleName=role_name,
    )
    rc = resp["roleCredentials"]
    return (
        SsoCreds(
            access_key_id=rc["accessKeyId"],
            secret_access_key=rc["secretAccessKey"],
            session_token=rc["sessionToken"],
            expiration_ms=int(rc["expiration"]),
        ),
        account_id,
        role_name,
    )


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------

def _load_cached_token(start_url: str) -> str | None:
    """Return a cached access token for *start_url* if still valid, else None."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("startUrl") != start_url:
        return None
    if data.get("expiresAt", 0) <= int(time.time()) + 60:
        return None
    log.info("Reusing cached SSO token (expires at %s)",
             time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data["expiresAt"])))
    return data.get("accessToken")


def _save_cached_token(start_url: str, access_token: str, expires_in_s: int) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({
        "startUrl": start_url,
        "accessToken": access_token,
        "expiresAt": int(time.time()) + int(expires_in_s),
    }))
    TOKEN_CACHE.chmod(0o600)


# ---------------------------------------------------------------------------
# Device-code flow
# ---------------------------------------------------------------------------

def _device_code_login(start_url: str, sso_region: str, on_device_code) -> str:
    """Register a public OIDC client, request a device code, poll for token."""
    oidc = boto3.client("sso-oidc", region_name=sso_region)

    log.info("Registering SSO OIDC client %r ...", CLIENT_NAME)
    reg = oidc.register_client(
        clientName=CLIENT_NAME,
        clientType=CLIENT_TYPE,
        scopes=SCOPES,
    )
    client_id = reg["clientId"]
    client_secret = reg["clientSecret"]

    log.info("Requesting device authorization ...")
    dev = oidc.start_device_authorization(
        clientId=client_id,
        clientSecret=client_secret,
        startUrl=start_url,
    )
    verification_uri = dev["verificationUriComplete"]
    user_code = dev["userCode"]
    device_code = dev["deviceCode"]
    interval = int(dev.get("interval", 5))
    expires_in = int(dev.get("expiresIn", 600))

    print()
    print("=" * 60)
    print("AWS SSO authorization required.")
    print(f"  URL:  {verification_uri}")
    print(f"  Code: {user_code}")
    print("=" * 60)
    print("Opening your browser at the URL above; approve the")
    print("'sharktopus-deployer' request. If the browser doesn't")
    print("open automatically, copy the URL manually.")
    print()

    if on_device_code is not None:
        on_device_code(verification_uri, user_code)
    try:
        webbrowser.open(verification_uri, new=2)
    except webbrowser.Error:
        pass

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        try:
            tok = oidc.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=device_code,
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "AuthorizationPendingException":
                continue
            if code == "SlowDownException":
                interval += 5
                continue
            if code == "ExpiredTokenException":
                raise RuntimeError(
                    "SSO authorization window expired. Rerun to get a new code."
                ) from e
            raise
        access_token = tok["accessToken"]
        _save_cached_token(start_url, access_token, tok.get("expiresIn", 28800))
        log.info("SSO authorization successful.")
        return access_token

    raise RuntimeError("SSO authorization timed out waiting for browser approval.")


# ---------------------------------------------------------------------------
# Interactive account / role selection
# ---------------------------------------------------------------------------

def _pick_account(sso, access_token: str) -> str:
    accounts = sso.list_accounts(accessToken=access_token).get("accountList", [])
    if not accounts:
        raise RuntimeError(
            "No AWS accounts visible to this SSO identity. Check with your "
            "organization admin that a permission set is assigned."
        )
    if len(accounts) == 1:
        a = accounts[0]
        log.info("Only one account visible: %s (%s)", a["accountId"], a.get("accountName", ""))
        return a["accountId"]
    print()
    print("Pick an AWS account to deploy into:")
    for i, a in enumerate(accounts):
        print(f"  [{i}] {a['accountId']}   {a.get('accountName', '')}   {a.get('emailAddress', '')}")
    while True:
        try:
            raw = input("Choice [0]: ").strip() or "0"
            idx = int(raw)
            return accounts[idx]["accountId"]
        except (ValueError, IndexError):
            print("  invalid choice, try again")


def _pick_role(sso, access_token: str, account_id: str) -> str:
    roles = sso.list_account_roles(
        accessToken=access_token, accountId=account_id,
    ).get("roleList", [])
    if not roles:
        raise RuntimeError(
            f"No SSO roles available in account {account_id}. Ask your admin "
            "for a permission set with Lambda + IAM + S3 + ECR write access."
        )
    if len(roles) == 1:
        r = roles[0]["roleName"]
        log.info("Only one role available: %s", r)
        return r
    print()
    print(f"Pick a role in account {account_id}:")
    for i, r in enumerate(roles):
        print(f"  [{i}] {r['roleName']}")
    while True:
        try:
            raw = input("Choice [0]: ").strip() or "0"
            idx = int(raw)
            return roles[idx]["roleName"]
        except (ValueError, IndexError):
            print("  invalid choice, try again")
