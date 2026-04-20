"""Provision the sharktopus Cloud Run service in the user's GCloud project.

Pulls the prebuilt container image from the public GitHub Container
Registry (``ghcr.io/sharktopus-project/sharktopus:cloudrun-<tag>``) and
deploys it to Cloud Run. Cloud Run accepts public registries natively —
no local Docker build and no Artifact Registry mirror needed; the
``--image`` flag points directly at GHCR and Cloud Run's internal
puller takes care of the rest.

Idempotent: each call updates the service in place when it already
exists. Safe to re-run.

Resources created (names overridable via env vars / CLI flags):

* Enabled APIs: ``run.googleapis.com``, ``storage.googleapis.com``,
  ``iamcredentials.googleapis.com`` (the last is for V4 signed URLs).
* GCS bucket ``sharktopus-crops-<project>`` with a 7-day lifecycle
  rule on ``crops/`` so forgotten objects expire cheaply.
* Cloud Run service ``sharktopus-crop`` — 1 vCPU, 2 GiB, 600 s request
  timeout, reads the bucket from ``SHARKTOPUS_GCS_BUCKET``. Deployed
  with ``--allow-unauthenticated`` by default so a fresh install can
  invoke it without extra IAM wiring (clients still get TLS).

Auth: this script uses the user's ADC (``gcloud auth application-
default login`` or a GOOGLE_APPLICATION_CREDENTIALS service account
key). Target project comes from ``--project`` / ``GOOGLE_CLOUD_PROJECT``.

Usage::

    python deploy/gcloud/provision.py \\
        --project my-proj [--region us-central1] \\
        [--image-tag cloudrun-latest] [--min-instances 0]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sharktopus-gcloud-deploy")

# Public image published by .github/workflows/build-image.yml.
GHCR_IMAGE = os.environ.get(
    "SHARKTOPUS_GHCR_IMAGE",
    "ghcr.io/sharktopus-project/sharktopus",
)
IMAGE_TAG = os.environ.get("SHARKTOPUS_IMAGE_TAG", "cloudrun-latest")
SERVICE_NAME = os.environ.get("SHARKTOPUS_SERVICE_NAME", "sharktopus-crop")
DEFAULT_REGION = os.environ.get("SHARKTOPUS_REGION", "us-central1")
SERVICE_CPU = os.environ.get("SHARKTOPUS_CPU", "1")
SERVICE_MEMORY = os.environ.get("SHARKTOPUS_MEMORY", "2Gi")
SERVICE_TIMEOUT_S = int(os.environ.get("SHARKTOPUS_TIMEOUT", "600"))


def main() -> int:
    """Entry point: parse CLI, enable APIs, ensure bucket, deploy service."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--image-tag", default=IMAGE_TAG,
        help=f"Tag on {GHCR_IMAGE} (default: cloudrun-latest)",
    )
    parser.add_argument(
        "--min-instances", type=int, default=0, metavar="N",
        help=(
            "Keep N warm instances. N=0 (default) means cold start, zero"
            " idle cost. N>0 eliminates cold start but is BILLED CONTINUOUSLY"
            " (~$5/month/instance at 1 vCPU + 2 GiB)."
        ),
    )
    parser.add_argument(
        "--authenticated-only", action="store_true",
        help=(
            "Require an ID token on every invocation. Default is"
            " --allow-unauthenticated so a fresh-install client works."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without touching GCloud.",
    )
    args = parser.parse_args()

    if not args.project:
        raise SystemExit(
            "error: --project (or GOOGLE_CLOUD_PROJECT) must be set"
        )

    bucket = os.environ.get(
        "SHARKTOPUS_GCS_BUCKET", f"sharktopus-crops-{args.project}",
    )
    image = f"{GHCR_IMAGE}:{args.image_tag}"

    if args.dry_run:
        log.info("DRY RUN — nothing will be created.")
        log.info("Would:")
        log.info("  Enable   : run, storage, iamcredentials, artifactregistry APIs")
        log.info("  Bucket   : gs://%s (7d lifecycle on crops/)", bucket)
        log.info("  AR proxy : %s-docker.pkg.dev/%s/%s → https://ghcr.io",
                 args.region, args.project, AR_REPO_NAME)
        log.info("  Deploy   : %s (via proxy, %s, cpu=%s, mem=%s, timeout=%ds, min=%d)",
                 SERVICE_NAME, image, SERVICE_CPU, SERVICE_MEMORY,
                 SERVICE_TIMEOUT_S, args.min_instances)
        return 0

    _ensure_gcloud_cli()
    ensure_apis(args.project)
    ensure_bucket(args.project, bucket)
    deploy_image = ensure_ghcr_proxy(args.project, args.region, args.image_tag)
    url = deploy_service(
        args.project, args.region, deploy_image, bucket,
        min_instances=args.min_instances,
        allow_unauthenticated=not args.authenticated_only,
    )

    log.info("=" * 60)
    log.info("Deploy complete.")
    log.info("Service URL : %s", url)
    log.info("Bucket      : gs://%s", bucket)
    if args.min_instances:
        log.info("Hot-start   : %d warm instances (billed continuously)",
                 args.min_instances)
    log.info(
        "Point clients at it: export SHARKTOPUS_GCLOUD_URL=%s", url,
    )
    return 0


# ---------------------------------------------------------------------------
# gcloud CLI driver
# ---------------------------------------------------------------------------

def _ensure_gcloud_cli() -> None:
    """Abort early with a helpful message if the gcloud CLI is missing."""
    r = subprocess.run(["which", "gcloud"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            "error: the gcloud CLI is required. Install it from "
            "https://cloud.google.com/sdk/docs/install and run "
            "`gcloud auth login`."
        )


def _gcloud(args: list[str], *, project: str | None = None, capture: bool = True):
    """Run ``gcloud`` with sane defaults, raise on non-zero exit."""
    cmd = ["gcloud"] + args + ["--quiet"]
    if project:
        cmd += ["--project", project]
    log.debug("$ %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if r.returncode != 0:
        out = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"gcloud failed ({r.returncode}): {' '.join(cmd)}\n{out}")
    return r


def ensure_apis(project: str) -> None:
    """Enable the four APIs the service depends on (idempotent)."""
    apis = [
        "run.googleapis.com",
        "storage.googleapis.com",
        "iamcredentials.googleapis.com",
        "artifactregistry.googleapis.com",
    ]
    log.info("Enabling APIs: %s", ", ".join(apis))
    _gcloud(["services", "enable", *apis], project=project, capture=False)


# Remote-repository name inside Artifact Registry. One-shot proxy: AR
# pulls `sharktopus-project/sharktopus:<tag>` from GHCR on first demand
# and caches the layers. Cloud Run can then deploy from this AR URL.
AR_REPO_NAME = os.environ.get("SHARKTOPUS_AR_REPO", "ghcr-proxy")


def ensure_ghcr_proxy(project: str, region: str, image_tag: str) -> str:
    """Create (idempotent) an AR remote repository proxying GHCR.

    Returns the Cloud Run-compatible image URL that Cloud Run will pull
    from — shaped like
    ``<region>-docker.pkg.dev/<project>/ghcr-proxy/sharktopus-project/sharktopus:<tag>``.
    AR fetches the layers from ghcr.io on first pull, caches them, and
    serves them to Cloud Run thereafter.

    Cloud Run rejects direct ``ghcr.io/...`` image URLs — it only accepts
    ``gcr.io``, ``<region>-docker.pkg.dev``, and ``docker.io``. This
    proxy is the GCloud analogue of the AWS ECR Pull-Through Cache used
    on the AWS side.
    """
    # Probe existence first — skip the create if already there.
    r = subprocess.run(
        ["gcloud", "artifacts", "repositories", "describe", AR_REPO_NAME,
         "--location", region, "--project", project, "--quiet"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.info("Creating AR remote repository %s/%s → ghcr.io",
                 region, AR_REPO_NAME)
        _gcloud(
            [
                "artifacts", "repositories", "create", AR_REPO_NAME,
                "--repository-format=docker",
                "--location", region,
                "--mode=remote-repository",
                "--remote-repo-config-desc=GHCR proxy for sharktopus",
                "--remote-docker-repo=https://ghcr.io",
            ],
            project=project, capture=False,
        )
    else:
        log.info("AR remote repository %s/%s already exists", region, AR_REPO_NAME)

    # GHCR path `sharktopus-project/sharktopus` maps 1:1 under the proxy.
    # The `GHCR_IMAGE` env points at `ghcr.io/<owner>/<repo>` — strip the host.
    ghcr_path = GHCR_IMAGE.split("ghcr.io/", 1)[-1]
    return f"{region}-docker.pkg.dev/{project}/{AR_REPO_NAME}/{ghcr_path}:{image_tag}"


def ensure_bucket(project: str, bucket: str) -> None:
    """Create the crops bucket + lifecycle if missing.

    Uses ``gcloud storage buckets`` (the gsutil successor). The bucket
    is kept private; the service generates V4 signed URLs per
    invocation.
    """
    r = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{bucket}",
         "--project", project, "--quiet"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.info("Creating bucket gs://%s", bucket)
        _gcloud(
            ["storage", "buckets", "create", f"gs://{bucket}",
             "--uniform-bucket-level-access"],
            project=project, capture=False,
        )
    else:
        log.info("Bucket gs://%s already exists", bucket)

    lifecycle = {
        "lifecycle": {
            "rule": [{
                "action": {"type": "Delete"},
                "condition": {"age": 7, "matchesPrefix": ["crops/"]},
            }],
        },
    }
    # gcloud storage buckets update --lifecycle-file expects a local JSON file.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(lifecycle, f)
        path = f.name
    try:
        _gcloud(
            ["storage", "buckets", "update", f"gs://{bucket}",
             "--lifecycle-file", path],
            project=project, capture=False,
        )
    finally:
        os.unlink(path)


def deploy_service(
    project: str, region: str, image: str, bucket: str,
    *, min_instances: int, allow_unauthenticated: bool,
) -> str:
    """Deploy or update the Cloud Run service and return its public URL.

    ``gcloud run deploy`` is idempotent — creates on first run, rolls
    out a new revision on each subsequent call. Returns the stable
    URL on the service (not the revision-specific one).
    """
    log.info("Deploying Cloud Run service %s with image %s", SERVICE_NAME, image)
    deploy_args = [
        "run", "deploy", SERVICE_NAME,
        "--image", image,
        "--region", region,
        "--cpu", SERVICE_CPU,
        "--memory", SERVICE_MEMORY,
        "--timeout", str(SERVICE_TIMEOUT_S),
        "--concurrency", "8",  # match Flask threads
        "--min-instances", str(min_instances),
        "--max-instances", "20",
        "--set-env-vars",
        f"SHARKTOPUS_GCS_BUCKET={bucket},SHARKTOPUS_MEMORY_MB=2048",
    ]
    if allow_unauthenticated:
        deploy_args.append("--allow-unauthenticated")
    else:
        deploy_args.append("--no-allow-unauthenticated")

    _gcloud(deploy_args, project=project, capture=False)

    r = _gcloud(
        ["run", "services", "describe", SERVICE_NAME,
         "--region", region, "--format=value(status.url)"],
        project=project,
    )
    return (r.stdout or "").strip()


if __name__ == "__main__":
    sys.exit(main())
