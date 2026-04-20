"""Provision the sharktopus Container App in the user's Azure subscription.

Pulls the prebuilt container image from the public GitHub Container
Registry (``ghcr.io/sharktopus-project/sharktopus:azure-<tag>``) and
deploys it to Azure Container Apps. Container Apps accepts public
registries natively — no local Docker build and no Azure Container
Registry mirror needed; the image reference points straight at GHCR.

Idempotent: each call updates the resources in place when they already
exist. Safe to re-run.

Resources created (names overridable via env vars / CLI flags):

* Resource providers: ``Microsoft.App``, ``Microsoft.Storage``,
  ``Microsoft.OperationalInsights`` (registered on first call).
* Resource group ``sharktopus-rg`` in the chosen region.
* Storage account ``sharktopus<hash>`` (globally unique, StorageV2,
  LRS) with a blob container ``crops`` and a 7-day lifecycle rule.
* Log Analytics workspace ``sharktopus-logs`` (free tier cap) for the
  Container App Environment.
* Container App Environment ``sharktopus-env`` (Consumption plan).
* Container App ``sharktopus-crop`` with system-assigned managed
  identity, 1 vCPU / 2 GiB, public HTTPS ingress on port 8080, scale
  0..10, env vars wired to the storage account/container.
* Role assignment: ``Storage Blob Data Contributor`` on the storage
  account for the Container App's managed identity (so server-side
  SAS generation via user-delegation key works).

Auth: uses ``DefaultAzureCredential`` — honours ``az login``, env
credentials, managed identity, etc. Subscription comes from
``--subscription`` / ``AZURE_SUBSCRIPTION_ID``.

Usage::

    python deploy/azure/provision.py \\
        --subscription <sub-id> [--location eastus2] \\
        [--resource-group sharktopus-rg] \\
        [--image-tag azure-latest]
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sharktopus-azure-deploy")

GHCR_IMAGE = os.environ.get(
    "SHARKTOPUS_GHCR_IMAGE",
    "ghcr.io/sharktopus-project/sharktopus",
)
IMAGE_TAG = os.environ.get("SHARKTOPUS_IMAGE_TAG", "azure-latest")
APP_NAME = os.environ.get("SHARKTOPUS_APP_NAME", "sharktopus-crop")
ENV_NAME = os.environ.get("SHARKTOPUS_ENV_NAME", "sharktopus-env")
LOGS_NAME = os.environ.get("SHARKTOPUS_LOGS_NAME", "sharktopus-logs")
DEFAULT_RG = os.environ.get("SHARKTOPUS_AZURE_RG", "sharktopus-rg")
DEFAULT_LOCATION = os.environ.get("SHARKTOPUS_AZURE_LOCATION", "eastus2")
CONTAINER_NAME = os.environ.get("SHARKTOPUS_AZURE_BLOB_CONTAINER", "crops")
APP_CPU = float(os.environ.get("SHARKTOPUS_CPU", "1.0"))
APP_MEMORY = os.environ.get("SHARKTOPUS_MEMORY", "2Gi")

# Storage Blob Data Contributor (built-in role). Grants the Container
# App identity permission to upload/delete/read blobs and to request
# user-delegation keys for SAS generation.
ROLE_BLOB_DATA_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"


def main() -> int:
    """Entry point: parse CLI, register providers, create resources, deploy app."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--resource-group", default=DEFAULT_RG)
    parser.add_argument(
        "--image-tag", default=IMAGE_TAG,
        help=f"Tag on {GHCR_IMAGE} (default: azure-latest)",
    )
    parser.add_argument(
        "--min-replicas", type=int, default=0, metavar="N",
        help=(
            "Keep N warm replicas. N=0 (default) scales to zero when idle."
            " N>0 eliminates cold start but BILLS CONTINUOUSLY for the"
            " kept vCPU-seconds / GB-seconds."
        ),
    )
    parser.add_argument(
        "--max-replicas", type=int, default=10,
        help="Cap on Container App replicas. Default: 10.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without touching Azure.",
    )
    args = parser.parse_args()

    if not args.subscription:
        raise SystemExit(
            "error: --subscription (or AZURE_SUBSCRIPTION_ID) must be set"
        )

    sub = args.subscription
    rg = args.resource_group
    location = args.location
    image = f"{GHCR_IMAGE}:{args.image_tag}"
    storage_account = _storage_account_name(sub, rg)

    if args.dry_run:
        log.info("DRY RUN — nothing will be created.")
        log.info("  Subscription : %s", sub)
        log.info("  Resource grp : %s (%s)", rg, location)
        log.info("  Storage acct : %s (container %s, 7d lifecycle)",
                 storage_account, CONTAINER_NAME)
        log.info("  Environment  : %s + %s workspace", ENV_NAME, LOGS_NAME)
        log.info("  Container App: %s (%s, cpu=%.1f, mem=%s, min=%d, max=%d)",
                 APP_NAME, image, APP_CPU, APP_MEMORY,
                 args.min_replicas, args.max_replicas)
        return 0

    _cred, clients = _clients(sub)

    _ensure_providers(clients["resource_providers"])
    _ensure_resource_group(clients["resources"], rg, location)
    _ensure_storage(
        clients["storage"], rg, storage_account, location, CONTAINER_NAME,
    )
    workspace_id, workspace_key = _ensure_log_analytics(
        clients["loganalytics"], rg, LOGS_NAME, location,
    )
    _ensure_environment(
        clients["apps"], rg, ENV_NAME, location, workspace_id, workspace_key,
    )
    fqdn, principal_id = _ensure_container_app(
        clients["apps"], rg, APP_NAME, ENV_NAME, location,
        image=image,
        storage_account=storage_account,
        blob_container=CONTAINER_NAME,
        min_replicas=args.min_replicas,
        max_replicas=args.max_replicas,
    )
    _ensure_blob_role(
        clients["authorization"], sub, rg, storage_account, principal_id,
    )

    url = f"https://{fqdn}"
    log.info("=" * 60)
    log.info("Deploy complete.")
    log.info("Container App URL : %s", url)
    log.info("Storage account   : %s (container %s)",
             storage_account, CONTAINER_NAME)
    if args.min_replicas:
        log.info("Hot-start        : %d warm replicas (billed continuously)",
                 args.min_replicas)
    log.info("Point clients at it: export SHARKTOPUS_AZURE_URL=%s", url)
    return 0


# ---------------------------------------------------------------------------
# SDK client plumbing
# ---------------------------------------------------------------------------


def _clients(subscription: str) -> tuple[object, dict]:
    """Instantiate the Azure SDK management clients we need.

    Gated behind a helpful error so a fresh install without the azure
    SDKs installed fails with an actionable message rather than a raw
    ``ImportError`` from deep inside the ARM layer.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.appcontainers import ContainerAppsAPIClient
        from azure.mgmt.authorization import AuthorizationManagementClient
        from azure.mgmt.loganalytics import LogAnalyticsManagementClient
        from azure.mgmt.resource import ResourceManagementClient
        from azure.mgmt.storage import StorageManagementClient
    except ImportError as e:
        raise SystemExit(
            "error: Azure SDK not installed. Run:\n"
            "    pip install "
            "azure-identity azure-mgmt-resource azure-mgmt-storage "
            "azure-mgmt-appcontainers azure-mgmt-loganalytics "
            "azure-mgmt-authorization\n"
            f"(import failed: {e})"
        )

    cred = DefaultAzureCredential()
    resources = ResourceManagementClient(cred, subscription)
    return cred, {
        "resources": resources,
        "resource_providers": resources.providers,
        "storage": StorageManagementClient(cred, subscription),
        "apps": ContainerAppsAPIClient(cred, subscription),
        "loganalytics": LogAnalyticsManagementClient(cred, subscription),
        "authorization": AuthorizationManagementClient(cred, subscription),
    }


# ---------------------------------------------------------------------------
# Resource providers + resource group
# ---------------------------------------------------------------------------


def _ensure_providers(providers) -> None:
    """Register the resource providers this deploy touches (idempotent)."""
    for ns in (
        "Microsoft.App",
        "Microsoft.Storage",
        "Microsoft.OperationalInsights",
        "Microsoft.ContainerRegistry",
    ):
        log.info("Registering provider %s", ns)
        providers.register(ns)


def _ensure_resource_group(client, rg: str, location: str) -> None:
    """Create or update the target resource group."""
    log.info("Ensuring resource group %s in %s", rg, location)
    client.resource_groups.create_or_update(rg, {"location": location})


# ---------------------------------------------------------------------------
# Storage account + blob container
# ---------------------------------------------------------------------------


def _storage_account_name(subscription: str, rg: str) -> str:
    """Derive a globally-unique, DNS-safe storage account name.

    Names must be 3–24 chars, lowercase alphanumeric. We hash
    ``subscription/rg`` to get deterministic uniqueness without
    persisting state; ``sharktopus`` prefix keeps it discoverable.
    """
    override = os.environ.get("SHARKTOPUS_AZURE_STORAGE_ACCOUNT", "").strip()
    if override:
        return override
    h = hashlib.sha1(f"{subscription}/{rg}".encode("utf-8")).hexdigest()[:12]
    return f"sharktopus{h}"


def _ensure_storage(
    client, rg: str, account: str, location: str, container: str,
) -> None:
    """Create (or update) the storage account + blob container + lifecycle."""
    from azure.core.exceptions import ResourceNotFoundError

    try:
        existing = client.storage_accounts.get_properties(rg, account)
        log.info("Storage account %s already exists (%s)",
                 account, existing.provisioning_state)
    except ResourceNotFoundError:
        log.info("Creating storage account %s (%s, StandardLRS)", account, location)
        poller = client.storage_accounts.begin_create(
            rg, account,
            {
                "sku": {"name": "Standard_LRS"},
                "kind": "StorageV2",
                "location": location,
                "allow_blob_public_access": False,
                "minimum_tls_version": "TLS1_2",
            },
        )
        poller.result()

    log.info("Ensuring blob container %s", container)
    try:
        client.blob_containers.get(rg, account, container)
    except Exception:
        client.blob_containers.create(
            rg, account, container, {"public_access": "None"},
        )

    log.info("Setting 7-day lifecycle on container %s", container)
    policy = {
        "policy": {
            "rules": [{
                "enabled": True,
                "name": "expire-crops",
                "type": "Lifecycle",
                "definition": {
                    "actions": {"base_blob": {"delete": {"days_after_modification_greater_than": 7}}},
                    "filters": {"blob_types": ["blockBlob"], "prefix_match": [f"{container}/"]},
                },
            }],
        },
    }
    client.management_policies.create_or_update(rg, account, "default", policy)


# ---------------------------------------------------------------------------
# Log Analytics workspace (required by Container App Environment)
# ---------------------------------------------------------------------------


def _ensure_log_analytics(client, rg: str, name: str, location: str) -> tuple[str, str]:
    """Return ``(customer_id, primary_shared_key)`` for the workspace.

    Container App Environments require a Log Analytics workspace for
    stdout/stderr piping. The free tier (5 GB/month) is more than
    enough for this service's volume.
    """
    from azure.core.exceptions import ResourceNotFoundError

    try:
        ws = client.workspaces.get(rg, name)
        log.info("Log Analytics workspace %s already exists", name)
    except ResourceNotFoundError:
        log.info("Creating Log Analytics workspace %s", name)
        ws = client.workspaces.begin_create_or_update(
            rg, name,
            {
                "location": location,
                "sku": {"name": "PerGB2018"},
                "retention_in_days": 30,
            },
        ).result()

    keys = client.shared_keys.get_shared_keys(rg, name)
    return ws.customer_id, keys.primary_shared_key


# ---------------------------------------------------------------------------
# Container App Environment
# ---------------------------------------------------------------------------


def _ensure_environment(
    client, rg: str, name: str, location: str,
    workspace_id: str, workspace_key: str,
) -> None:
    """Create or update the Container App Environment (Consumption plan)."""
    from azure.core.exceptions import ResourceNotFoundError

    try:
        client.managed_environments.get(rg, name)
        log.info("Container App Environment %s already exists", name)
        return
    except ResourceNotFoundError:
        pass

    log.info("Creating Container App Environment %s", name)
    poller = client.managed_environments.begin_create_or_update(
        rg, name,
        {
            "location": location,
            "app_logs_configuration": {
                "destination": "log-analytics",
                "log_analytics_configuration": {
                    "customer_id": workspace_id,
                    "shared_key": workspace_key,
                },
            },
            "zone_redundant": False,
        },
    )
    poller.result()


# ---------------------------------------------------------------------------
# Container App
# ---------------------------------------------------------------------------


def _ensure_container_app(
    client, rg: str, name: str, env_name: str, location: str,
    *,
    image: str,
    storage_account: str,
    blob_container: str,
    min_replicas: int,
    max_replicas: int,
) -> tuple[str, str]:
    """Create or update the Container App, return ``(fqdn, principal_id)``."""
    env = client.managed_environments.get(rg, env_name)
    template = {
        "containers": [{
            "name": name,
            "image": image,
            "resources": {"cpu": APP_CPU, "memory": APP_MEMORY},
            "env": [
                {"name": "SHARKTOPUS_AZURE_STORAGE_ACCOUNT", "value": storage_account},
                {"name": "SHARKTOPUS_AZURE_BLOB_CONTAINER", "value": blob_container},
                {"name": "SHARKTOPUS_MEMORY_MB", "value": "2048"},
            ],
        }],
        "scale": {"min_replicas": min_replicas, "max_replicas": max_replicas},
    }
    ingress = {
        "external": True,
        "target_port": 8080,
        "transport": "auto",
        "allow_insecure": False,
    }
    body = {
        "location": location,
        "managed_environment_id": env.id,
        "identity": {"type": "SystemAssigned"},
        "configuration": {
            "ingress": ingress,
            "active_revisions_mode": "Single",
        },
        "template": template,
    }

    log.info("Deploying Container App %s (image %s)", name, image)
    poller = client.container_apps.begin_create_or_update(rg, name, body)
    app = poller.result()

    fqdn = app.configuration.ingress.fqdn
    principal_id = app.identity.principal_id if app.identity else None
    if not principal_id:
        # Fresh-create doesn't always return the identity straight away;
        # refetch once after ARM settles.
        time.sleep(5)
        app = client.container_apps.get(rg, name)
        principal_id = app.identity.principal_id if app.identity else None
    if not principal_id:
        raise RuntimeError(
            f"Container App {name} has no system-assigned identity yet; "
            f"re-run provision.py to retry role assignment"
        )
    return fqdn, principal_id


# ---------------------------------------------------------------------------
# Role assignment: managed identity → Storage Blob Data Contributor
# ---------------------------------------------------------------------------


def _ensure_blob_role(
    client, subscription: str, rg: str, storage_account: str, principal_id: str,
) -> None:
    """Grant the Container App identity blob upload + user-delegation key rights."""
    scope = (
        f"/subscriptions/{subscription}/resourceGroups/{rg}"
        f"/providers/Microsoft.Storage/storageAccounts/{storage_account}"
    )
    role_def_id = (
        f"/subscriptions/{subscription}/providers/Microsoft.Authorization"
        f"/roleDefinitions/{ROLE_BLOB_DATA_CONTRIBUTOR}"
    )
    # Role assignment names must be GUIDs; derive deterministically from
    # (principal, scope, role) so re-runs are idempotent.
    name = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{principal_id}|{scope}|{ROLE_BLOB_DATA_CONTRIBUTOR}",
    ))
    from azure.core.exceptions import ResourceExistsError, HttpResponseError

    log.info("Granting Storage Blob Data Contributor on %s to app identity",
             storage_account)
    try:
        client.role_assignments.create(
            scope, name,
            {
                "role_definition_id": role_def_id,
                "principal_id": principal_id,
                "principal_type": "ServicePrincipal",
            },
        )
    except ResourceExistsError:
        log.info("Role assignment already present")
    except HttpResponseError as e:
        # Principal may not have propagated yet; retry a few times.
        for attempt in range(1, 6):
            time.sleep(attempt * 5)
            try:
                client.role_assignments.create(
                    scope, name,
                    {
                        "role_definition_id": role_def_id,
                        "principal_id": principal_id,
                        "principal_type": "ServicePrincipal",
                    },
                )
                log.info("Role assignment created on retry %d", attempt)
                return
            except ResourceExistsError:
                return
            except HttpResponseError:
                continue
        raise RuntimeError(f"role assignment failed after retries: {e}")


if __name__ == "__main__":
    sys.exit(main())
