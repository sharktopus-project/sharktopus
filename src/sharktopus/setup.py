"""Interactive bootstrap for a cloud-crop deploy.

``sharktopus --setup {gcloud,aws,azure}`` walks the user through the
three things that stand between a fresh ``pip install`` and a working
cloud-crop endpoint:

1. **Install the provider CLI** (user-space, opt-in, with an explicit
   download prompt — never silent).
2. **Authenticate** via browser OAuth (we print the command and wait
   for ENTER; no stdin-forwarding tricks).
3. **Run ``provision.py``** for that cloud.

The goal is *fewer user-visible steps*, not *magic*. We never install
binaries during ``pip install sharktopus``; we never authenticate
silently. The user can quit at any prompt.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = ["run_setup"]

HOME = Path.home()
GCLOUD_HOME = HOME / "google-cloud-sdk"
AWS_CLI_HOME = HOME / ".local" / "aws-cli"
AZURE_CLI_HOME = HOME / ".local" / "azure-cli"
LOCAL_BIN = HOME / ".local" / "bin"


def run_setup(cloud: str) -> int:
    """Dispatch to the per-cloud walkthrough."""
    if platform.system() not in ("Linux", "Darwin"):
        print(
            "setup: only Linux and macOS are supported right now. "
            "See docs/DEPLOY_GCLOUD.md or docs/DEPLOY_AWS.md.",
            file=sys.stderr,
        )
        return 2
    if cloud == "gcloud":
        return _setup_gcloud()
    if cloud == "aws":
        return _setup_aws()
    if cloud == "azure":
        return _setup_azure()
    print(f"setup: unknown cloud {cloud!r}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _confirm(prompt: str, *, default: bool = False) -> bool:
    """Yes/No prompt honoring ``default`` on empty input."""
    d = "Y/n" if default else "y/N"
    try:
        ans = input(f"{prompt} [{d}]: ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "s", "sim")


def _ask(prompt: str, default: str | None = None) -> str:
    """Text prompt with optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or (default or "")


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run *cmd* with stdio inherited. Returns the exit code."""
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, env=env).returncode


def _find_provision_script(cloud: str) -> Path:
    """Locate ``deploy/<cloud>/provision.py`` relative to this file.

    Works in a source checkout; raises a clear error if the script is
    missing (e.g., when running from a wheel that didn't bundle the
    deploy scripts — future problem).
    """
    here = Path(__file__).resolve().parent
    for ancestor in (here.parent.parent, here.parent, here):
        candidate = ancestor / "deploy" / cloud / "provision.py"
        if candidate.exists():
            return candidate
    raise SystemExit(
        f"setup: cannot find deploy/{cloud}/provision.py — run from a "
        "source checkout, or install with deploy scripts bundled."
    )


# ---------------------------------------------------------------------------
# GCloud walkthrough
# ---------------------------------------------------------------------------

def _find_gcloud() -> str | None:
    p = shutil.which("gcloud")
    if p:
        return p
    candidate = GCLOUD_HOME / "bin" / "gcloud"
    return str(candidate) if candidate.exists() else None


def _install_gcloud() -> str:
    """User-space install of the gcloud CLI into ``~/google-cloud-sdk``."""
    print()
    print("gcloud CLI not found.")
    print(f"  Will download ~200 MB from dl.google.com and install into {GCLOUD_HOME}")
    print("  (user-space — no sudo, reversible by `rm -rf ~/google-cloud-sdk`)")
    if not _confirm("Proceed?", default=True):
        raise SystemExit("setup: aborted before gcloud install")

    sysname = platform.system()
    machine = platform.machine()
    if sysname == "Darwin":
        arch = "darwin-arm" if machine in ("arm64", "aarch64") else "darwin-x86_64"
    else:
        arch = "linux-arm" if machine in ("arm64", "aarch64") else "linux-x86_64"

    url = f"https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-{arch}.tar.gz"
    tgz = HOME / "google-cloud-cli.tar.gz"
    if _run(["curl", "-sSLo", str(tgz), url]) != 0:
        raise SystemExit("setup: curl failed")
    if _run(["tar", "-xf", str(tgz), "-C", str(HOME)]) != 0:
        raise SystemExit("setup: tar extraction failed")
    tgz.unlink(missing_ok=True)
    if _run([
        str(GCLOUD_HOME / "install.sh"),
        "--quiet", "--path-update=true",
        f"--rc-path={HOME}/.bashrc",
    ]) != 0:
        raise SystemExit("setup: gcloud install.sh failed")
    return str(GCLOUD_HOME / "bin" / "gcloud")


def _setup_gcloud() -> int:
    print("== sharktopus setup gcloud ==")
    gcloud = _find_gcloud() or _install_gcloud()
    print(f"[1/4] gcloud CLI: {gcloud}")

    # --- [2/4] User account auth
    active = subprocess.run(
        [gcloud, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not active:
        print()
        print("[2/4] gcloud has no active account.")
        print("      Run in a terminal (browser will open, paste code back):")
        print(f"          {gcloud} auth login --no-launch-browser")
        try:
            input("      Press ENTER when login completes, or Ctrl-C to abort... ")
        except (KeyboardInterrupt, EOFError):
            print()
            return 130
    else:
        print(f"[2/4] gcloud active account: {active}")

    # --- [3/4] ADC (for the Python client later)
    adc = HOME / ".config" / "gcloud" / "application_default_credentials.json"
    if not adc.exists():
        print()
        print("[3/4] Application Default Credentials missing.")
        print("      Run in a terminal:")
        print(f"          {gcloud} auth application-default login --no-launch-browser")
        try:
            input("      Press ENTER when it completes... ")
        except (KeyboardInterrupt, EOFError):
            print()
            return 130
    else:
        print(f"[3/4] ADC present at {adc}")

    # --- [4/4] Pick project + deploy
    cfg_proj = subprocess.run(
        [gcloud, "config", "get-value", "project"],
        capture_output=True, text=True,
    ).stdout.strip()
    project = _ask("GCloud project ID to deploy into", default=cfg_proj or None)
    if not project:
        print("setup: project is required", file=sys.stderr)
        return 2

    print()
    print(f"[4/4] Deploying sharktopus-crop to project {project} ...")
    script = _find_provision_script("gcloud")
    env = os.environ.copy()
    env["PATH"] = f"{Path(gcloud).parent}{os.pathsep}{env.get('PATH', '')}"
    rc = _run(
        [sys.executable, str(script), "--project", project, "--authenticated-only"],
        env=env,
    )
    if rc != 0:
        print("setup: provision.py failed", file=sys.stderr)
        return rc

    print()
    print("Done. Quick test:")
    print("  python -c \"from sharktopus.sources import gcloud_crop;"
          " p = gcloud_crop.fetch_step('20260417','00',6,"
          " bbox=(-50,-40,-25,-20), variables=['TMP'], levels=['500 mb']);"
          " print(p, p.stat().st_size, 'bytes')\"")
    return 0


# ---------------------------------------------------------------------------
# AWS walkthrough
# ---------------------------------------------------------------------------

def _find_aws() -> str | None:
    p = shutil.which("aws")
    if p:
        return p
    candidate = LOCAL_BIN / "aws"
    return str(candidate) if candidate.exists() else None


def _install_aws() -> str:
    """User-space install of AWS CLI v2 under ``~/.local``."""
    print()
    print("AWS CLI v2 not found.")
    print(f"  Will download ~60 MB from awscli.amazonaws.com and install into {AWS_CLI_HOME}")
    print(f"  (user-space — binary will be at {LOCAL_BIN}/aws)")
    if not _confirm("Proceed?", default=True):
        raise SystemExit("setup: aborted before aws install")

    sysname = platform.system()
    machine = platform.machine()
    if sysname == "Darwin":
        raise SystemExit(
            "setup: AWS CLI v2 on macOS ships as a .pkg installer that needs "
            "admin rights. Please install it manually from "
            "https://awscli.amazonaws.com/AWSCLIV2.pkg and re-run."
        )
    if sysname != "Linux":
        raise SystemExit(f"setup: platform {sysname!r} not supported")

    arch_url = {
        "x86_64": "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip",
        "aarch64": "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip",
    }.get(machine)
    if not arch_url:
        raise SystemExit(f"setup: unsupported machine arch {machine!r}")

    zippath = HOME / "awscliv2.zip"
    tmp = HOME / "_awscli_install_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    if _run(["curl", "-sSLo", str(zippath), arch_url]) != 0:
        raise SystemExit("setup: curl failed")
    if _run(["unzip", "-q", str(zippath), "-d", str(tmp)]) != 0:
        raise SystemExit("setup: unzip failed (install the `unzip` package)")
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    AWS_CLI_HOME.mkdir(parents=True, exist_ok=True)
    if _run([
        str(tmp / "aws" / "install"),
        "--install-dir", str(AWS_CLI_HOME),
        "--bin-dir", str(LOCAL_BIN),
        "--update",
    ]) != 0:
        raise SystemExit("setup: aws/install failed")
    shutil.rmtree(tmp, ignore_errors=True)
    zippath.unlink(missing_ok=True)
    return str(LOCAL_BIN / "aws")


def _setup_aws() -> int:
    print("== sharktopus setup aws ==")
    aws = _find_aws() or _install_aws()
    print(f"[1/3] AWS CLI: {aws}")

    profile = _ask("AWS profile name", default="sharktopus-deploy")
    region = _ask("AWS region", default="us-east-1")

    env = os.environ.copy()
    env["PATH"] = f"{Path(aws).parent}{os.pathsep}{env.get('PATH', '')}"
    rc = subprocess.run(
        [aws, "sts", "get-caller-identity", "--profile", profile],
        capture_output=True, env=env,
    ).returncode
    if rc != 0:
        print()
        print(f"[2/3] Profile {profile!r} has no valid credentials.")
        print("      Pick an auth method:")
        print("        (a) SSO — browser-based, no long-lived keys (recommended)")
        print("        (b) Static access keys (IAM User)")
        choice = _ask("Choice", default="a").lower()
        print()
        if choice.startswith("b"):
            print("      Run this and enter your access keys when prompted:")
            print(f"          {aws} configure --profile {profile}")
        else:
            print("      Run this (one-shot SSO setup), then the login:")
            print(f"          {aws} configure sso --profile {profile}")
            print(f"          {aws} sso login    --profile {profile}")
        try:
            input("      Press ENTER when auth is working... ")
        except (KeyboardInterrupt, EOFError):
            print()
            return 130
    else:
        print(f"[2/3] Profile {profile!r} already authenticated.")

    print()
    print(f"[3/3] Deploying Lambda to profile={profile} region={region} ...")
    script = _find_provision_script("aws")
    rc = _run(
        [sys.executable, str(script), "--profile", profile, "--region", region],
        env=env,
    )
    if rc != 0:
        print("setup: provision.py failed", file=sys.stderr)
        return rc

    print()
    print("Done. Quick test:")
    print("  python -c \"import sharktopus;"
          " sharktopus.fetch_batch(timestamps=['2026041700'], lat_s=-25, lat_n=-20,"
          " lon_w=-50, lon_e=-40, ext=6, interval=3, priority=['aws_crop'],"
          " variables=['TMP'], levels=['500 mb'])\"")
    return 0


# ---------------------------------------------------------------------------
# Azure walkthrough
# ---------------------------------------------------------------------------

def _find_az() -> str | None:
    p = shutil.which("az")
    if p:
        return p
    candidate = AZURE_CLI_HOME / "bin" / "az"
    return str(candidate) if candidate.exists() else None


def _install_az() -> str:
    """Point the user at Microsoft's install script and wait.

    Unlike gcloud and AWS CLI, Azure CLI doesn't publish a clean
    user-space tarball — the official installer runs via a bash
    script that touches ``/usr/local``. We surface it but require
    the user to run it themselves (sudo prompt).
    """
    print()
    print("Azure CLI not found.")
    print("  Microsoft does not publish a user-space tarball. Run the")
    print("  official installer (needs sudo once):")
    print("      curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash")
    print("  Or: https://learn.microsoft.com/cli/azure/install-azure-cli")
    if not _confirm("I've installed it; continue?", default=False):
        raise SystemExit("setup: aborted waiting for az install")
    az = shutil.which("az")
    if not az:
        raise SystemExit("setup: az still not on PATH — please install and re-run")
    return az


def _setup_azure() -> int:
    print("== sharktopus setup azure ==")

    # --- [1/3] Pick auth path.
    # Two dimensions: (a) install/use az CLI, or (b) go pure-Python.
    # If az is already on PATH we ask which to use; otherwise we offer
    # install OR pure-Python OR service principal env vars.
    az = _find_az()
    if az:
        print(f"[1/3] Azure CLI found at {az}.")
        print("      Pick an auth path:")
        print("        (a) Use installed az CLI (reads ~/.azure/ session)")
        print("        (b) Pure Python (no CLI calls; azure-identity opens browser)")
        choice = _ask("Choice", default="a").lower()
        mode = "cli" if choice.startswith("a") else "py"
    else:
        print("[1/3] Azure CLI not installed. Pick an auth path:")
        print("        (a) Install az CLI (sudo once + browser login)")
        print("        (b) Pure Python (no install, no sudo, browser login)")
        print("        (c) Service principal env vars (CI / headless)")
        choice = _ask("Choice", default="b").lower()
        if choice.startswith("a"):
            az = _install_az()
            mode = "cli"
        elif choice.startswith("c"):
            mode = "sp"
        else:
            mode = "py"

    subscription: str | None = None
    if mode == "cli":
        # Use the az CLI session.
        show = subprocess.run(
            [az, "account", "show"],
            capture_output=True, text=True,
        )
        if show.returncode != 0:
            print()
            print("      az has no active session. In a terminal run:")
            print(f"          {az} login                    # opens default browser")
            print(f"          {az} login --use-device-code  # headless / SSH")
            print("      Your password is typed at microsoft.com, never here.")
            try:
                input("      Press ENTER when login completes... ")
            except (KeyboardInterrupt, EOFError):
                print()
                return 130
            show = subprocess.run(
                [az, "account", "show"],
                capture_output=True, text=True,
            )
            if show.returncode != 0:
                print("setup: still no active subscription after login",
                      file=sys.stderr)
                return 2
        sub_cmd = subprocess.run(
            [az, "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True, text=True,
        )
        subscription = _ask(
            "Azure subscription ID",
            default=sub_cmd.stdout.strip() or None,
        )
    elif mode == "sp":
        # Env-var service principal; provision.py's DefaultAzureCredential
        # will pick it up.
        missing = [
            v for v in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
            if not os.environ.get(v)
        ]
        if missing:
            print(
                "setup: missing env vars for service principal: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        subscription = _ask(
            "Azure subscription ID",
            default=os.environ.get("AZURE_SUBSCRIPTION_ID"),
        )
    # mode == "py": subscription stays None; provision.py lists and prompts
    #               after the browser login against the InteractiveBrowserCredential.

    location = _ask("Azure region (location)", default="eastus2")
    rg = _ask("Resource group", default="sharktopus-rg")

    summary = f"[2/3] Auth: {mode}"
    if subscription:
        summary += f"   Subscription: {subscription}"
    summary += f"   Region: {location}"
    print(summary)

    # --- [3/3] Deploy
    print()
    print(f"[3/3] Provisioning Container App in {rg}/{location} ...")
    script = _find_provision_script("azure")
    env = os.environ.copy()
    auth_flag = "browser" if mode == "py" else "default"
    cmd = [
        sys.executable, str(script),
        "--auth", auth_flag,
        "--location", location,
        "--resource-group", rg,
    ]
    if subscription:
        cmd += ["--subscription", subscription]
        env["AZURE_SUBSCRIPTION_ID"] = subscription
    rc = _run(cmd, env=env)
    if rc != 0:
        print("setup: provision.py failed", file=sys.stderr)
        return rc

    print()
    print("Done. Quick test:")
    print("  python -c \"from sharktopus.sources import azure_crop;"
          " p = azure_crop.fetch_step('20260417','00',6,"
          " bbox=(-50,-40,-25,-20), variables=['TMP'], levels=['500 mb']);"
          " print(p, p.stat().st_size, 'bytes')\"")
    return 0
