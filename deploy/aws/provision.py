"""Provision the sharktopus Lambda in the user's AWS account.

Pulls the prebuilt container image from the public GitHub Container
Registry (``ghcr.io/sharktopus-project/sharktopus``) via ECR's
Pull-Through Cache — no local Docker build required. The user only
needs AWS credentials.

Idempotent: each step checks for existing resources and updates in
place when possible. Safe to re-run.

Resources created (names can be overridden via env vars):

* ECR pull-through cache rule ``ghcr/*`` → ``ghcr.io/*`` — so the
  public image is transparently mirrored into the user's ECR on first
  pull. No credentials are needed for public GHCR repos.
* IAM role ``sharktopus-lambda-role`` — Lambda execution role with
  CloudWatchLogs + S3 (to the output bucket).
* S3 bucket ``sharktopus-crops-<account>-<region>`` — output for s3-mode
  responses. Configured with a 7-day lifecycle rule so forgotten
  objects don't accumulate cost.
* Lambda function ``sharktopus`` — container-image, 2048 MB, 300 s
  timeout, reads the bucket name from ``SHARKTOPUS_S3_BUCKET`` env var.

This script uses the default boto3 credential chain. Set
``AWS_PROFILE=<name>`` to target a specific profile.

Usage::

    python deploy/aws/provision.py [--profile <name>] [--region <r>] \\
        [--image-tag <tag>] [--hot-start N]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sharktopus-deploy")

HERE = Path(__file__).resolve().parent

# Pull-through cache: <account>.dkr.ecr.<region>.amazonaws.com/<PTC_PREFIX>/<GHCR_REPO>:<IMAGE_TAG>
# The prefix is an arbitrary namespace inside the user's ECR; the GHCR
# repo is the public source-of-truth published by our CI workflow.
PTC_PREFIX = os.environ.get("SHARKTOPUS_PTC_PREFIX", "ghcr")
GHCR_REPO = os.environ.get("SHARKTOPUS_GHCR_REPO", "sharktopus-project/sharktopus")

IAM_ROLE_NAME = os.environ.get("SHARKTOPUS_IAM_ROLE", "sharktopus-lambda-role")
LAMBDA_NAME = os.environ.get("SHARKTOPUS_LAMBDA_NAME", "sharktopus")
IMAGE_TAG = os.environ.get("SHARKTOPUS_IMAGE_TAG", "lambda-latest")
LAMBDA_MEMORY_MB = int(os.environ.get("SHARKTOPUS_LAMBDA_MEMORY", "2048"))
LAMBDA_TIMEOUT_S = int(os.environ.get("SHARKTOPUS_LAMBDA_TIMEOUT", "300"))
# Full 0p25 GFS is ~500 MB; wgrib2 reads + writes roughly doubles that peak.
# 4096 MB ephemeral keeps the unfiltered-bbox path working without tipping
# into pathological cost (Lambda bills ephemeral only above 512 MB).
LAMBDA_EPHEMERAL_MB = int(os.environ.get("SHARKTOPUS_LAMBDA_EPHEMERAL", "4096"))
HOT_ALIAS = "live"


def _hint_credentials(exc: Exception, profile: str | None) -> None:
    """Translate boto3's auth errors into something the user can act on.

    The three common cases:

    * **SSO token expired** (``SSOTokenLoadError`` /
      ``UnauthorizedSSOTokenError``) — user just needs to re-login;
      nothing persistent broke.
    * **Profile missing** (``ProfileNotFound``) — wrong ``--profile`` or
      the user hasn't run ``aws configure sso`` yet.
    * **No creds at all** (``NoCredentialsError``) — neither static keys
      nor an SSO session; first-time setup.

    We'd rather point the user at ``aws sso login`` / ``aws configure sso``
    than dump a raw stack trace that mentions obscure botocore internals.
    """
    msg = str(exc)
    cls = type(exc).__name__
    prof = profile or os.environ.get("AWS_PROFILE") or "<default>"
    log.error("AWS authentication failed: %s — %s", cls, msg.splitlines()[0][:200])

    if "SSO" in cls or "sso" in msg.lower() or "token" in msg.lower():
        log.error("Hint: your AWS SSO session has expired (or was never started).")
        log.error("      Run:  aws sso login --profile %s", prof)
    elif "ProfileNotFound" in cls:
        log.error("Hint: profile %r is not configured in ~/.aws/config.", prof)
        log.error("      Run:  aws configure sso   (recommended, browser-based)")
        log.error("       or:  aws configure --profile %s   (static keys fallback)", prof)
    elif "NoCredentials" in cls:
        log.error("Hint: no AWS credentials found in env or ~/.aws/. Pick one:")
        log.error("      Browser SSO (no long-lived keys):  aws configure sso")
        log.error("      Static keys:                       aws configure")
    else:
        log.error("Hint: confirm you can call `aws sts get-caller-identity`"
                  " (or the equivalent via your chosen auth method) before retrying.")


def main() -> int:
    """Entry point: parse CLI, resolve account/region, drive each ensure_* step.

    The order matters: the image must be warmed in ECR before the
    Lambda can pull it, the IAM role before the function can assume it,
    the S3 bucket before the role policy scopes to it.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="AWS profile name")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--image-tag",
        default=IMAGE_TAG,
        help="Image tag to pull from ghcr.io/%s (default: latest)" % GHCR_REPO,
    )
    parser.add_argument(
        "--skip-image",
        action="store_true",
        help="Skip pull-through cache warm-up (use image already cached in ECR)",
    )
    parser.add_argument(
        "--hot-start",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Reserve N provisioned-concurrency instances so cold starts are"
            " eliminated. BILLED EVEN WHEN IDLE (~$5/instance/month at 2048 MB)."
            " Default 0 = cold start, free tier."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without touching AWS",
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    sts = session.client("sts", region_name=args.region)
    try:
        identity = sts.get_caller_identity()
    except Exception as e:  # noqa: BLE001 — narrow via message
        _hint_credentials(e, args.profile)
        return 2
    account = identity["Account"]
    log.info("Target account %s region %s (ARN=%s)", account, args.region, identity["Arn"])

    bucket = os.environ.get(
        "SHARKTOPUS_S3_BUCKET", f"sharktopus-crops-{account}-{args.region}"
    )

    if args.dry_run:
        log.info("DRY RUN — nothing will be created.")
        log.info("Would create/update:")
        log.info("  ECR PTC rule  : %s → ghcr.io", PTC_PREFIX)
        log.info("  Image         : ghcr.io/%s:%s (mirrored via PTC)", GHCR_REPO, args.image_tag)
        log.info("  IAM role      : %s", IAM_ROLE_NAME)
        log.info("  S3 bucket     : %s", bucket)
        log.info("  Lambda        : %s (mem=%d MB, timeout=%ds, hot=%d)",
                 LAMBDA_NAME, LAMBDA_MEMORY_MB, LAMBDA_TIMEOUT_S, args.hot_start)
        return 0

    image_uri = ensure_ecr_image_via_ptc(
        session, args.region, account,
        image_tag=args.image_tag, skip_warm=args.skip_image,
    )
    role_arn = ensure_iam_role(session, bucket)
    ensure_s3_bucket(session, args.region, bucket)
    fn_arn = ensure_lambda(
        session, args.region, image_uri, role_arn, bucket,
        hot_instances=args.hot_start,
    )

    log.info("=" * 60)
    log.info("Deploy complete.")
    log.info("Function ARN : %s", fn_arn)
    log.info("Image URI    : %s", image_uri)
    log.info("S3 bucket    : %s", bucket)
    if args.hot_start:
        log.info("Hot-start    : %d instances reserved on alias '%s'", args.hot_start, HOT_ALIAS)
    log.info("Invoke with  : aws lambda invoke --function-name %s --payload ...", LAMBDA_NAME)
    return 0


# ---------------------------------------------------------------------------
# ECR Pull-Through Cache: mirror ghcr.io → user's account transparently
# ---------------------------------------------------------------------------

def ensure_ecr_image_via_ptc(
    session, region: str, account: str, *,
    image_tag: str = IMAGE_TAG, skip_warm: bool = False,
) -> str:
    """Ensure the public GHCR image is mirrored into the user's ECR.

    Creates the pull-through cache rule on first run, then warms the
    cache by issuing an HTTP manifest GET (equivalent to ``docker pull``
    but without needing a local Docker daemon). Returns the fully-
    qualified ECR URI that Lambda will pull from.

    When *skip_warm* is set, assumes the image is already cached and
    returns the URI without the HTTP round-trip — useful for fast
    re-runs that only tweak Lambda config.
    """
    ecr = session.client("ecr", region_name=region)

    existing = ecr.describe_pull_through_cache_rules().get("pullThroughCacheRules", [])
    if not any(r["ecrRepositoryPrefix"] == PTC_PREFIX for r in existing):
        log.info("Creating pull-through cache rule %s → ghcr.io", PTC_PREFIX)
        ecr.create_pull_through_cache_rule(
            ecrRepositoryPrefix=PTC_PREFIX,
            upstreamRegistryUrl="ghcr.io",
            upstreamRegistry="github-container-registry",
        )
    else:
        log.info("Pull-through cache rule %s already present", PTC_PREFIX)

    ptc_repo = f"{PTC_PREFIX}/{GHCR_REPO}"
    image_uri = f"{account}.dkr.ecr.{region}.amazonaws.com/{ptc_repo}:{image_tag}"

    if skip_warm:
        log.info("--skip-image set; assuming %s is already cached", image_uri)
        return image_uri

    log.info("Warming pull-through cache: %s (first pull may take ~1 min)", image_uri)
    _warm_ptc_cache(ecr, ptc_repo, image_tag)
    return image_uri


def _warm_ptc_cache(ecr_client, ptc_repo: str, tag: str) -> None:
    """Trigger ECR to pull an image from upstream by fetching its manifest.

    ECR only populates a pull-through-cache repository when something
    actually asks for an image. A ``GET /v2/<repo>/manifests/<tag>``
    against the ECR registry (authenticated with the ECR auth token) is
    the protocol-level equivalent of ``docker pull`` for this purpose,
    and works with only urllib + boto3 — no Docker daemon required.
    """
    auth = ecr_client.get_authorization_token()["authorizationData"][0]
    token = auth["authorizationToken"]  # base64("AWS:<password>")
    registry_host = auth["proxyEndpoint"].replace("https://", "")
    url = f"https://{registry_host}/v2/{ptc_repo}/manifests/{tag}"

    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Basic {token}",
            "Accept": ", ".join([
                "application/vnd.docker.distribution.manifest.v2+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.oci.image.index.v1+json",
            ]),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            digest = resp.headers.get("Docker-Content-Digest", "<unknown>")
            log.info("Pull-through cache warmed (digest %s)", digest)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"Failed to warm pull-through cache for {ptc_repo}:{tag}: "
            f"HTTP {e.code} — {body}. Is the image published at "
            f"ghcr.io/{GHCR_REPO}:{tag}?"
        ) from e


# ---------------------------------------------------------------------------
# IAM: execution role for Lambda
# ---------------------------------------------------------------------------

def ensure_iam_role(session, bucket: str) -> str:
    """Create or update the Lambda execution role.

    Attaches :aws-managed:`AWSLambdaBasicExecutionRole` (CloudWatch
    Logs) and an inline policy scoped to *bucket* for Put/Get/Delete.
    Returns the role ARN.

    ``put_role_policy`` / ``attach_role_policy`` are both idempotent —
    re-running is cheap and safe.
    """
    iam = session.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    try:
        role = iam.get_role(RoleName=IAM_ROLE_NAME)["Role"]
        log.info("IAM role %s already exists", IAM_ROLE_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        log.info("Creating IAM role %s", IAM_ROLE_NAME)
        role = iam.create_role(
            RoleName=IAM_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Execution role for the sharktopus Lambda (GFS cloud-side crop)",
        )["Role"]

    iam.attach_role_policy(
        RoleName=IAM_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
            "Resource": f"arn:aws:s3:::{bucket}/*",
        }],
    }
    iam.put_role_policy(
        RoleName=IAM_ROLE_NAME,
        PolicyName="sharktopus-s3-output",
        PolicyDocument=json.dumps(s3_policy),
    )
    return role["Arn"]


# ---------------------------------------------------------------------------
# S3: output bucket with lifecycle
# ---------------------------------------------------------------------------

def ensure_s3_bucket(session, region: str, bucket: str) -> None:
    """Create (or verify) the crops bucket and set a 7-day lifecycle rule.

    When the function returns inline base64 (<4 MB), the bucket is
    unused — but the s3 fallback path needs it, and the lifecycle rule
    guarantees that even forgotten objects expire within a week.

    Public access is blocked at the bucket level: outputs are reached
    via short-lived presigned URLs only.
    """
    s3 = session.client("s3", region_name=region)
    try:
        s3.head_bucket(Bucket=bucket)
        log.info("S3 bucket %s already exists", bucket)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise
        log.info("Creating S3 bucket %s", bucket)
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [{
                "ID": "sharktopus-crops-7d-expiry",
                "Status": "Enabled",
                "Filter": {"Prefix": "crops/"},
                "Expiration": {"Days": 7},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
            }],
        },
    )


# ---------------------------------------------------------------------------
# Lambda: create or update function + optional provisioned concurrency
# ---------------------------------------------------------------------------

def ensure_lambda(
    session, region: str, image_uri: str, role_arn: str, bucket: str,
    *, hot_instances: int = 0,
) -> str:
    """Create the Lambda if missing, otherwise update code + configuration.

    Waits between ``update_function_code`` and ``update_function_configuration``
    because Lambda serializes those (a second mutating call on a
    ``Pending`` function is rejected).

    On first create, retries up to 10 times to ride out IAM propagation
    — a fresh role frequently fails the first ``create_function`` with
    "role defined for the function cannot be assumed".

    When *hot_instances* > 0, publishes a ``live`` alias pointing at the
    just-published version and attaches that many units of provisioned
    concurrency. Setting it back to 0 tears the alias config down.
    """
    lam = session.client("lambda", region_name=region)
    env = {"Variables": {"SHARKTOPUS_S3_BUCKET": bucket, "LOG_LEVEL": "INFO"}}

    try:
        fn = lam.get_function(FunctionName=LAMBDA_NAME)
        log.info("Lambda %s already exists — updating code + config", LAMBDA_NAME)
        _wait_for_not_pending(lam, LAMBDA_NAME)
        updated = lam.update_function_code(
            FunctionName=LAMBDA_NAME, ImageUri=image_uri, Publish=True,
        )
        _wait_for_not_pending(lam, LAMBDA_NAME)
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Role=role_arn,
            Timeout=LAMBDA_TIMEOUT_S,
            MemorySize=LAMBDA_MEMORY_MB,
            EphemeralStorage={"Size": LAMBDA_EPHEMERAL_MB},
            Environment=env,
        )
        _wait_for_not_pending(lam, LAMBDA_NAME)
        _configure_hot_start(lam, updated["Version"], hot_instances)
        return fn["Configuration"]["FunctionArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    log.info("Creating Lambda %s", LAMBDA_NAME)
    for attempt in range(10):
        try:
            resp = lam.create_function(
                FunctionName=LAMBDA_NAME,
                PackageType="Image",
                Code={"ImageUri": image_uri},
                Role=role_arn,
                Timeout=LAMBDA_TIMEOUT_S,
                MemorySize=LAMBDA_MEMORY_MB,
                EphemeralStorage={"Size": LAMBDA_EPHEMERAL_MB},
                Environment=env,
                Description="GFS byte-range + wgrib2 crop (sharktopus)",
                Publish=True,
            )
            _wait_for_not_pending(lam, LAMBDA_NAME)
            _configure_hot_start(lam, resp["Version"], hot_instances)
            return resp["FunctionArn"]
        except ClientError as e:
            if "role defined for the function cannot be assumed" in str(e).lower():
                wait = min(5 * (attempt + 1), 30)
                log.info("IAM role not yet propagated; retrying in %ds", wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("IAM role failed to propagate after 10 attempts")


def _configure_hot_start(lam, version: str, hot_instances: int) -> None:
    """Attach or detach provisioned concurrency on the ``live`` alias.

    Provisioned concurrency keeps *hot_instances* execution environments
    warm at all times, eliminating cold starts at the cost of a steady
    monthly bill (~$5/instance/month for a 2048 MB function). Setting
    *hot_instances* to 0 is the path back to the pure on-demand, free-tier
    model — any prior alias config is torn down.
    """
    if hot_instances > 0:
        _upsert_alias(lam, HOT_ALIAS, version)
        log.info(
            "Enabling provisioned concurrency: %d instances on alias %s "
            "(pointing at version %s)", hot_instances, HOT_ALIAS, version,
        )
        lam.put_provisioned_concurrency_config(
            FunctionName=LAMBDA_NAME,
            Qualifier=HOT_ALIAS,
            ProvisionedConcurrentExecutions=hot_instances,
        )
        log.warning(
            "Hot-start is BILLED CONTINUOUSLY. Set --hot-start 0 to revert.",
        )
        return

    try:
        lam.delete_provisioned_concurrency_config(
            FunctionName=LAMBDA_NAME, Qualifier=HOT_ALIAS,
        )
        log.info("Cleared provisioned concurrency on alias %s", HOT_ALIAS)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("ProvisionedConcurrencyConfigNotFoundException", "ResourceNotFoundException"):
            raise


def _upsert_alias(lam, alias: str, version: str) -> None:
    """Point *alias* at *version*, creating it on first run."""
    try:
        lam.create_alias(FunctionName=LAMBDA_NAME, Name=alias, FunctionVersion=version)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_alias(FunctionName=LAMBDA_NAME, Name=alias, FunctionVersion=version)


def _wait_for_not_pending(lam, name: str) -> None:
    """Poll ``get_function_configuration`` until the function is Active + Successful.

    Every mutating Lambda call (code, config, publish) returns while
    the function is still ``Pending``; subsequent mutations error. This
    helper blocks for up to ~120 s and raises on failed updates.
    """
    for _ in range(60):
        cfg = lam.get_function_configuration(FunctionName=name)
        state = cfg.get("State")
        last_update = cfg.get("LastUpdateStatus")
        if state == "Active" and last_update in (None, "Successful"):
            return
        if last_update == "Failed":
            raise RuntimeError(f"Lambda {name} last update failed: {cfg.get('LastUpdateStatusReason')}")
        time.sleep(2)
    raise TimeoutError(f"Lambda {name} did not become Active within 120s")


if __name__ == "__main__":
    sys.exit(main())
