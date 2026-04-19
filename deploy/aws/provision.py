"""Provision the sharktopus Lambda in the user's AWS account.

Idempotent: each step checks for existing resources and updates in
place when possible. Safe to re-run.

Resources created (names can be overridden via env vars):

* ECR repository ``sharktopus`` — holds the container image.
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

    python deploy/aws/provision.py [--profile <name>] [--region <r>]
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import subprocess
import sys
import time
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

ECR_REPO = os.environ.get("SHARKTOPUS_ECR_REPO", "sharktopus")
IAM_ROLE_NAME = os.environ.get("SHARKTOPUS_IAM_ROLE", "sharktopus-lambda-role")
LAMBDA_NAME = os.environ.get("SHARKTOPUS_LAMBDA_NAME", "sharktopus")
IMAGE_TAG = os.environ.get("SHARKTOPUS_IMAGE_TAG", "latest")
LAMBDA_MEMORY_MB = int(os.environ.get("SHARKTOPUS_LAMBDA_MEMORY", "2048"))
LAMBDA_TIMEOUT_S = int(os.environ.get("SHARKTOPUS_LAMBDA_TIMEOUT", "300"))
# Full 0p25 GFS is ~500 MB; wgrib2 reads + writes roughly doubles that peak.
# 4096 MB ephemeral keeps the unfiltered-bbox path working without tipping
# into pathological cost (Lambda bills ephemeral only above 512 MB).
LAMBDA_EPHEMERAL_MB = int(os.environ.get("SHARKTOPUS_LAMBDA_EPHEMERAL", "4096"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="AWS profile name")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip docker build+push (use existing ECR image)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without touching AWS",
    )
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    sts = session.client("sts", region_name=args.region)
    identity = sts.get_caller_identity()
    account = identity["Account"]
    log.info("Target account %s region %s (ARN=%s)", account, args.region, identity["Arn"])

    bucket = os.environ.get(
        "SHARKTOPUS_S3_BUCKET", f"sharktopus-crops-{account}-{args.region}"
    )

    if args.dry_run:
        log.info("DRY RUN — nothing will be created.")
        log.info("Would create/update:")
        log.info("  ECR repo      : %s", ECR_REPO)
        log.info("  IAM role      : %s", IAM_ROLE_NAME)
        log.info("  S3 bucket     : %s", bucket)
        log.info("  Lambda        : %s (mem=%d MB, timeout=%ds)",
                 LAMBDA_NAME, LAMBDA_MEMORY_MB, LAMBDA_TIMEOUT_S)
        return 0

    image_uri = ensure_ecr_image(session, args.region, account, skip_build=args.skip_build)
    role_arn = ensure_iam_role(session, bucket)
    ensure_s3_bucket(session, args.region, bucket)
    fn_arn = ensure_lambda(session, args.region, image_uri, role_arn, bucket)

    log.info("=" * 60)
    log.info("Deploy complete.")
    log.info("Function ARN : %s", fn_arn)
    log.info("Image URI    : %s", image_uri)
    log.info("S3 bucket    : %s", bucket)
    log.info("Invoke with  : aws lambda invoke --function-name %s --payload ...", LAMBDA_NAME)
    return 0


# ---------------------------------------------------------------------------
# ECR: create repo, docker login, build, tag, push
# ---------------------------------------------------------------------------

def ensure_ecr_image(session, region: str, account: str, *, skip_build: bool) -> str:
    ecr = session.client("ecr", region_name=region)
    try:
        ecr.describe_repositories(repositoryNames=[ECR_REPO])
        log.info("ECR repo %s already exists", ECR_REPO)
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        log.info("Creating ECR repo %s", ECR_REPO)
        ecr.create_repository(
            repositoryName=ECR_REPO,
            imageScanningConfiguration={"scanOnPush": True},
            encryptionConfiguration={"encryptionType": "AES256"},
        )

    image_uri = f"{account}.dkr.ecr.{region}.amazonaws.com/{ECR_REPO}:{IMAGE_TAG}"
    if skip_build:
        log.info("--skip-build set; using existing image %s", image_uri)
        return image_uri

    log.info("Authenticating docker to ECR %s.dkr.ecr.%s.amazonaws.com", account, region)
    auth = ecr.get_authorization_token()["authorizationData"][0]
    token = base64.b64decode(auth["authorizationToken"]).decode()
    _, password = token.split(":", 1)
    registry = auth["proxyEndpoint"].replace("https://", "")
    _run(
        ["docker", "login", "--username", "AWS", "--password-stdin", registry],
        input_text=password, capture=False,
    )

    log.info("Building image %s (this takes a few minutes — wgrib2 compile)", image_uri)
    # --provenance=false / --sbom=false: Lambda Container Images require
    # the classic Docker v2 manifest. Buildkit ≥ 0.11 ships attestations
    # by default, which wrap the image in an OCI index that Lambda rejects
    # with "The image manifest, config or layer media type is not supported."
    _run(
        [
            "docker", "build",
            "--platform", "linux/amd64",
            "--provenance=false", "--sbom=false",
            "-t", f"{ECR_REPO}:{IMAGE_TAG}",
            str(HERE),
        ],
        capture=False,
    )
    _run(["docker", "tag", f"{ECR_REPO}:{IMAGE_TAG}", image_uri], capture=False)
    log.info("Pushing %s", image_uri)
    _run(["docker", "push", image_uri], capture=False)
    return image_uri


# ---------------------------------------------------------------------------
# IAM: execution role for Lambda
# ---------------------------------------------------------------------------

def ensure_iam_role(session, bucket: str) -> str:
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
# Lambda: create or update function
# ---------------------------------------------------------------------------

def ensure_lambda(session, region: str, image_uri: str, role_arn: str, bucket: str) -> str:
    lam = session.client("lambda", region_name=region)
    env = {"Variables": {"SHARKTOPUS_S3_BUCKET": bucket, "LOG_LEVEL": "INFO"}}

    try:
        fn = lam.get_function(FunctionName=LAMBDA_NAME)
        log.info("Lambda %s already exists — updating code + config", LAMBDA_NAME)
        _wait_for_not_pending(lam, LAMBDA_NAME)
        lam.update_function_code(FunctionName=LAMBDA_NAME, ImageUri=image_uri, Publish=True)
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
        return fn["Configuration"]["FunctionArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    log.info("Creating Lambda %s", LAMBDA_NAME)
    # IAM propagation delay: role can't assume immediately after create.
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
            return resp["FunctionArn"]
        except ClientError as e:
            if "role defined for the function cannot be assumed" in str(e).lower():
                wait = min(5 * (attempt + 1), 30)
                log.info("IAM role not yet propagated; retrying in %ds", wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("IAM role failed to propagate after 10 attempts")


def _wait_for_not_pending(lam, name: str) -> None:
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


def _run(cmd: list[str], *, input_text: str | None = None, capture: bool = True):
    log.debug("$ %s", " ".join(cmd))
    r = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=capture,
        check=False,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip() if capture else "(output streamed)"
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(cmd)}\n{msg}")
    return r


if __name__ == "__main__":
    sys.exit(main())
