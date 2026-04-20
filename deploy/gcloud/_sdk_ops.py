"""Pure-Python SDK implementations of the gcloud provision steps.

Mirrors the CLI-based helpers in ``provision.py`` but uses the
``google-cloud-*`` SDKs directly — no ``gcloud`` binary on ``PATH``.
Each function takes an explicit ``credentials`` object so the caller
can inject whatever auth source it resolved (ADC, service account
JSON, or a short-lived browser token).

Credentials come from one of four places (checked in order by
``resolve_credentials``):

1. ``GOOGLE_APPLICATION_CREDENTIALS`` env var → service account JSON.
2. Explicit path passed as an argument → service account JSON.
3. User ADC at ``~/.config/gcloud/application_default_credentials.json``
   (written by a prior ``gcloud auth application-default login``).
4. Compute Engine / Cloud Shell metadata server.

A future commit will add a browser OAuth device-code flow once a
public Desktop OAuth client has been registered in a sharktopus-owned
GCP project. Until then, pure-Python users need either a service
account key or an ADC file from anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("sharktopus-gcloud-deploy")

ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


# ---------------------------------------------------------------------------
# Credentials resolution
# ---------------------------------------------------------------------------

@dataclass
class ResolvedCreds:
    credentials: object  # google.auth.credentials.Credentials
    project_hint: str | None  # project discoverable from the credential, or None
    source: str  # human label: "service-account", "adc", "metadata"


def resolve_credentials(service_account_json: str | None = None) -> ResolvedCreds:
    """Return credentials + (optional) project hint + a human source label.

    Order: explicit SA path → env var → ADC file → metadata server.
    Raises a clear error if none of those work.
    """
    import google.auth
    from google.auth import exceptions as auth_exc
    from google.oauth2 import service_account

    if service_account_json:
        log.info("Loading service account from %s", service_account_json)
        creds = service_account.Credentials.from_service_account_file(
            service_account_json,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        proj = json.loads(Path(service_account_json).read_text()).get("project_id")
        return ResolvedCreds(creds, proj, "service-account")

    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path and Path(env_path).exists():
        log.info("Loading service account from GOOGLE_APPLICATION_CREDENTIALS=%s", env_path)
        creds = service_account.Credentials.from_service_account_file(
            env_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        proj = json.loads(Path(env_path).read_text()).get("project_id")
        return ResolvedCreds(creds, proj, "service-account")

    try:
        creds, proj = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    except auth_exc.DefaultCredentialsError as e:
        raise RuntimeError(
            "No Google credentials found. Set GOOGLE_APPLICATION_CREDENTIALS "
            "to a service account JSON path, or run `gcloud auth "
            "application-default login` on any machine and copy the resulting "
            f"file to {ADC_PATH}."
        ) from e

    src = "adc" if ADC_PATH.exists() else "metadata"
    return ResolvedCreds(creds, proj, src)


# ---------------------------------------------------------------------------
# API enablement
# ---------------------------------------------------------------------------

def ensure_apis_sdk(credentials, project: str, apis: list[str]) -> None:
    """Enable *apis* on *project* via Service Usage API (idempotent)."""
    from google.cloud import service_usage_v1

    client = service_usage_v1.ServiceUsageClient(credentials=credentials)
    log.info("Enabling APIs: %s", ", ".join(apis))
    # batch_enable_services handles the already-enabled case gracefully.
    op = client.batch_enable_services(
        request=service_usage_v1.BatchEnableServicesRequest(
            parent=f"projects/{project}",
            service_ids=apis,
        ),
    )
    op.result(timeout=300)


# ---------------------------------------------------------------------------
# GCS bucket + lifecycle
# ---------------------------------------------------------------------------

def ensure_bucket_sdk(credentials, project: str, bucket: str, region: str) -> None:
    """Create *bucket* if missing and apply the 7-day crops/ lifecycle rule."""
    from google.cloud import storage
    from google.cloud.exceptions import Conflict, NotFound

    client = storage.Client(project=project, credentials=credentials)
    try:
        b = client.get_bucket(bucket)
        log.info("Bucket gs://%s already exists", bucket)
    except NotFound:
        log.info("Creating bucket gs://%s", bucket)
        try:
            b = client.create_bucket(
                bucket, location=region, project=project,
            )
            b.iam_configuration.uniform_bucket_level_access_enabled = True
            b.patch()
        except Conflict:
            b = client.get_bucket(bucket)

    b.lifecycle_rules = [{
        "action": {"type": "Delete"},
        "condition": {"age": 7, "matchesPrefix": ["crops/"]},
    }]
    b.patch()
    log.info("Lifecycle rule (crops/ 7d) applied on gs://%s", bucket)


# ---------------------------------------------------------------------------
# Artifact Registry remote repo → GHCR proxy
# ---------------------------------------------------------------------------

def ensure_ghcr_proxy_sdk(
    credentials, project: str, region: str, repo_name: str,
) -> None:
    """Create a remote-repository in AR that proxies ghcr.io (idempotent)."""
    from google.cloud import artifactregistry_v1
    from google.api_core.exceptions import AlreadyExists, NotFound

    client = artifactregistry_v1.ArtifactRegistryClient(credentials=credentials)
    parent = f"projects/{project}/locations/{region}"
    name = f"{parent}/repositories/{repo_name}"

    try:
        client.get_repository(name=name)
        log.info("AR remote repository %s/%s already exists", region, repo_name)
        return
    except NotFound:
        pass

    log.info("Creating AR remote repository %s/%s → ghcr.io", region, repo_name)
    repo = artifactregistry_v1.Repository(
        format_=artifactregistry_v1.Repository.Format.DOCKER,
        mode=artifactregistry_v1.Repository.Mode.REMOTE_REPOSITORY,
        description="GHCR proxy for sharktopus (created by provision.py)",
        remote_repository_config=artifactregistry_v1.RemoteRepositoryConfig(
            description="GHCR proxy for sharktopus",
            docker_repository=artifactregistry_v1.RemoteRepositoryConfig.DockerRepository(
                public_repository=artifactregistry_v1.RemoteRepositoryConfig.DockerRepository.PublicRepository.DOCKER_HUB,
            ),
        ),
    )
    # DOCKER_HUB is the only "public_repository" enum value in the SDK today;
    # the REST API accepts a "customRepository" with URI=https://ghcr.io,
    # but the Python SDK hasn't exposed that field yet. Fall back to REST.
    try:
        op = client.create_repository(
            parent=parent, repository_id=repo_name, repository=repo,
        )
        op.result(timeout=120)
    except (AlreadyExists, Exception) as e:
        # If the SDK path failed for any reason, try the raw REST API.
        log.info("SDK create_repository hit %s; falling back to REST API", type(e).__name__)
        _create_ghcr_remote_repo_rest(credentials, project, region, repo_name)


def _create_ghcr_remote_repo_rest(
    credentials, project: str, region: str, repo_name: str,
) -> None:
    """Fallback: call the Artifact Registry REST API directly.

    The Python SDK's ``RemoteRepositoryConfig`` exposes ``DockerRepository``
    with only the Docker-Hub preset today; pointing at ``ghcr.io`` requires
    the ``customRepository`` field, which is reachable via plain HTTP.
    """
    import google.auth.transport.requests as gtr
    from google.auth.transport.requests import AuthorizedSession

    session = AuthorizedSession(credentials)
    url = (
        f"https://artifactregistry.googleapis.com/v1/projects/{project}"
        f"/locations/{region}/repositories?repositoryId={repo_name}"
    )
    body = {
        "format": "DOCKER",
        "mode": "REMOTE_REPOSITORY",
        "description": "GHCR proxy for sharktopus (created by provision.py)",
        "remoteRepositoryConfig": {
            "description": "GHCR proxy for sharktopus",
            "dockerRepository": {
                "customRepository": {"uri": "https://ghcr.io"},
            },
        },
    }
    r = session.post(url, json=body, timeout=120)
    if r.status_code == 409:
        log.info("AR remote repository %s/%s already exists (409)", region, repo_name)
        return
    if r.status_code >= 300:
        raise RuntimeError(
            f"Artifact Registry REST create failed: {r.status_code} {r.text[:300]}"
        )
    log.info("AR remote repository %s/%s created via REST", region, repo_name)


# ---------------------------------------------------------------------------
# Cloud Run deploy
# ---------------------------------------------------------------------------

def deploy_service_sdk(
    credentials, project: str, region: str, service_name: str, image: str,
    *, env_vars: dict[str, str], cpu: str, memory: str, timeout_s: int,
    concurrency: int, min_instances: int, max_instances: int,
    allow_unauthenticated: bool,
) -> str:
    """Create or update the Cloud Run service and return its public URL."""
    from google.cloud import run_v2
    from google.api_core.exceptions import NotFound

    client = run_v2.ServicesClient(credentials=credentials)
    parent = f"projects/{project}/locations/{region}"
    full_name = f"{parent}/services/{service_name}"

    container = run_v2.Container(
        image=image,
        env=[run_v2.EnvVar(name=k, value=v) for k, v in env_vars.items()],
        resources=run_v2.ResourceRequirements(
            limits={"cpu": cpu, "memory": memory},
        ),
    )
    template = run_v2.RevisionTemplate(
        containers=[container],
        timeout={"seconds": timeout_s},
        max_instance_request_concurrency=concurrency,
        scaling=run_v2.RevisionScaling(
            min_instance_count=min_instances,
            max_instance_count=max_instances,
        ),
    )
    service = run_v2.Service(template=template)

    try:
        existing = client.get_service(name=full_name)
        log.info("Updating existing Cloud Run service %s", service_name)
        service.name = full_name
        op = client.update_service(service=service)
    except NotFound:
        log.info("Creating Cloud Run service %s", service_name)
        op = client.create_service(
            parent=parent, service=service, service_id=service_name,
        )
    result = op.result(timeout=600)

    if allow_unauthenticated:
        _grant_invoker_allusers(client, full_name)

    url = result.uri or ""
    return url


def _grant_invoker_allusers(services_client, full_name: str) -> None:
    """Set allUsers → roles/run.invoker so the service is reachable by anyone."""
    from google.iam.v1 import iam_policy_pb2, policy_pb2

    req = iam_policy_pb2.GetIamPolicyRequest(resource=full_name)
    policy = services_client.get_iam_policy(request=req)
    wanted = policy_pb2.Binding(role="roles/run.invoker", members=["allUsers"])
    for b in policy.bindings:
        if b.role == "roles/run.invoker" and "allUsers" in b.members:
            log.info("allUsers already has run.invoker")
            return
    policy.bindings.append(wanted)
    services_client.set_iam_policy(
        request=iam_policy_pb2.SetIamPolicyRequest(resource=full_name, policy=policy),
    )
    log.info("Granted roles/run.invoker to allUsers (public ingress)")
