"""Azure cloud-side cropping via the ``sharktopus-crop`` Container App.

Container Apps analogue of :mod:`sharktopus.sources.gcloud_crop`:
plain HTTPS POST to an Azure-hosted endpoint that fetches byte-ranges
from ``noaagfs.blob.core.windows.net``, runs wgrib2, and returns only
the cropped bytes.

Why Container Apps and not Functions: the original CONVECT experiment
used Azure Functions with a zip deploy carrying a pre-compiled wgrib2
binary, which fought with the Function runtime's permissions and
portability model. Container Apps accepts any HTTP container image,
so we reuse the same ``ghcr.io/sharktopus-project/sharktopus:cloudrun-latest``
image that Cloud Run serves — wgrib2 issue gone.

Two delivery modes, selected server-side (same contract as AWS/GCloud):

``inline``
    Container App base64-encodes the cropped file in the JSON body.
    Container Apps accepts up to 100 MB response bodies, so inline
    covers everything short of a continent-scale bbox.

``blob``
    For very large crops, the server uploads to a short-lived blob
    on a Storage Account and returns a SAS URL. The client downloads
    then deletes (lifecycle policy on the container is the backstop).

Policy gates (all in :mod:`sharktopus.cloud.azure_quota`):

* ``SHARKTOPUS_LOCAL_CROP=true`` — skip cloud-crop entirely.
* Free tier exhausted + ``SHARKTOPUS_ACCEPT_CHARGES`` unset →
  :class:`SourceUnavailable` so the orchestrator falls back to
  :mod:`sharktopus.sources.azure` (full download + local crop).

Authentication: Container Apps defaults to a public HTTPS ingress with
no auth — the fresh-install UX stays one-command. Callers who need
Azure-AD-gated access can front the app with Easy Auth and pass a
bearer token via ``SHARKTOPUS_AZURE_BEARER``.

The service URL is discovered in this order:

1. Explicit ``service_url=`` argument to :func:`fetch_step`.
2. ``SHARKTOPUS_AZURE_URL`` env var.
3. Azure ARM lookup for the default Container App name
   (``sharktopus-crop``) in the configured resource group/region.

Any failure in discovery → ``SourceUnavailable``.
"""

from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..cloud import azure_quota
from ..io import grib, paths
from .base import (
    SourceUnavailable,
    canonical_filename,
    stream_download,
    supports_date,
    validate_cycle,
    validate_date,
)

__all__ = [
    "DEFAULT_APP_NAME",
    "DEFAULT_RESOURCE_GROUP",
    "DEFAULT_LOCATION",
    "DEFAULT_MAX_WORKERS",
    "EARLIEST",
    "RETENTION_DAYS",
    "fetch_step",
    "have_credentials",
    "supports",
]


DEFAULT_APP_NAME = "sharktopus-crop"
DEFAULT_RESOURCE_GROUP = "sharktopus-rg"
DEFAULT_LOCATION = "eastus2"

# Container Apps scales out, so the client-side cap mostly bounds how
# many parallel invocations this one process triggers. Azure Blob's
# public GFS mirror prefers ≤ 4 parallel clients per source IP.
DEFAULT_MAX_WORKERS = 4

# Azure GFS mirror (``noaagfs.blob.core.windows.net``) started in 2021
# and retains indefinitely.
EARLIEST: datetime | None = datetime(2021, 1, 1, tzinfo=timezone.utc)
RETENTION_DAYS: int | None = None


def supports(date: str, cycle: str | None = None, *, now: datetime | None = None) -> bool:
    """Return ``True`` if Container Apps can serve *date*.

    Three checks: date window, a discoverable service URL, and that
    ``requests`` can actually be imported.
    """
    if not supports_date(date, earliest=EARLIEST, retention_days=RETENTION_DAYS, now=now):
        return False
    return _have_requests() and (_service_url_from_env() is not None or have_credentials())


def _have_requests() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


def _import_requests():
    """Lazy-import requests with a SourceUnavailable if missing."""
    try:
        import requests
        return requests
    except ImportError as e:
        raise SourceUnavailable(
            "azure_crop requires requests (pip install 'sharktopus[azure]')"
        ) from e


def have_credentials() -> bool:
    """Return ``True`` if the Azure SDK can resolve a credential right now.

    Optional: the default deploy is public-ingress (no auth), so the
    client doesn't need credentials when the URL is provided by
    ``SHARKTOPUS_AZURE_URL``. This predicate is used for auto-priority
    and should be truthy whenever we expect the source to succeed.
    """
    if _service_url_from_env():
        return True
    try:
        from azure.identity import DefaultAzureCredential  # noqa: F401
        return True
    except ImportError:
        return False


def _service_url_from_env() -> str | None:
    url = os.environ.get("SHARKTOPUS_AZURE_URL", "").strip()
    return url or None


def _bearer_from_env() -> str | None:
    tok = os.environ.get("SHARKTOPUS_AZURE_BEARER", "").strip()
    return tok or None


def _resource_group_from_env() -> str:
    return os.environ.get("SHARKTOPUS_AZURE_RG", "").strip() or DEFAULT_RESOURCE_GROUP


def _subscription_from_env() -> str | None:
    sub = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
    return sub or None


def _discover_service_url(
    app_name: str, resource_group: str, subscription: str | None,
) -> str | None:
    """Look up a Container App FQDN via Azure ARM.

    Falls back to ``None`` if azure-sdk isn't installed or the lookup
    fails — caller then raises :class:`SourceUnavailable` with context.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.appcontainers import ContainerAppsAPIClient
    except ImportError:
        return None

    try:
        sub = subscription or _subscription_from_env()
        if not sub:
            return None
        cred = DefaultAzureCredential()
        client = ContainerAppsAPIClient(cred, sub)
        app = client.container_apps.get(resource_group, app_name)
        fqdn = getattr(app.configuration.ingress, "fqdn", None) if app.configuration else None
        return f"https://{fqdn}" if fqdn else None
    except Exception:
        return None


def _build_payload(
    date: str,
    cycle: str,
    fxx: int,
    *,
    bbox: grib.Bbox | None,
    pad_lon: float,
    pad_lat: float,
    variables: Sequence[str] | None,
    levels: Sequence[str] | None,
    product: str,
    response_mode: str,
    blob_container: str | None,
    blob_expires_s: int,
) -> dict:
    """Compose the JSON body the Container App expects.

    Shape mirrors the AWS Lambda / Cloud Run handlers — same fields
    (``gcs_*`` becomes ``blob_*`` server-side) so the three endpoints
    share validation helpers.
    """
    validate_cycle(cycle)
    validate_date(date)
    payload: dict = {
        "date": date,
        "cycle": cycle,
        "fxx": int(fxx),
        "product": product,
        "response_mode": response_mode,
        "blob_expires_s": int(blob_expires_s),
    }
    if bbox is not None:
        lon_w, lon_e, lat_s, lat_n = grib.expand_bbox(bbox, pad_lon=pad_lon, pad_lat=pad_lat)
        payload["bbox"] = {
            "lon_w": lon_w, "lon_e": lon_e,
            "lat_s": lat_s, "lat_n": lat_n,
        }
    if variables:
        payload["variables"] = list(variables)
    if levels:
        payload["levels"] = list(levels)
    if blob_container:
        payload["blob_container"] = blob_container
    return payload


def _retain_blob() -> bool:
    return os.environ.get("SHARKTOPUS_RETAIN_BLOB", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _materialize_response(body: dict, final: Path, *, timeout: float) -> None:
    """Write the crop bytes referenced by *body* to *final*.

    Handles both ``inline`` (base64 in response) and ``blob`` (SAS URL)
    modes. After a successful blob download, deletes the object unless
    ``SHARKTOPUS_RETAIN_BLOB=true``.
    """
    mode = body.get("mode", "inline")
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_suffix(final.suffix + ".part")

    if mode == "inline":
        raw = body.get("b64")
        if not raw:
            raise SourceUnavailable("inline response missing 'b64' payload")
        part.write_bytes(base64.b64decode(raw))
        part.replace(final)
        return

    if mode == "blob":
        url = body.get("blob_url")
        if not url:
            raise SourceUnavailable("blob response missing 'blob_url'")
        stream_download(url, part, timeout=timeout, max_retries=3)
        part.replace(final)
        if not _retain_blob():
            _delete_blob(
                body.get("storage_account"),
                body.get("blob_container"),
                body.get("blob_key"),
            )
        return

    raise SourceUnavailable(f"unknown response mode: {mode!r}")


def _delete_blob(account: str | None, container: str | None, key: str | None) -> None:
    """Best-effort delete of the Container Apps scratch blob."""
    if not account or not container or not key:
        return
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        cred = DefaultAzureCredential()
        url = f"https://{account}.blob.core.windows.net"
        client = BlobServiceClient(account_url=url, credential=cred)
        client.get_blob_client(container, key).delete_blob()
    except Exception:
        pass


def fetch_step(
    date: str,
    cycle: str,
    fxx: int,
    *,
    dest: str | Path | None = None,
    root: str | Path | None = None,
    bbox: grib.Bbox | None = None,
    pad_lon: float = grib.DEFAULT_WRF_PAD_LON,
    pad_lat: float = grib.DEFAULT_WRF_PAD_LAT,
    product: str = "pgrb2.0p25",
    variables: Sequence[str] | None = None,
    levels: Sequence[str] | None = None,
    app_name: str = DEFAULT_APP_NAME,
    resource_group: str | None = None,
    subscription: str | None = None,
    service_url: str | None = None,
    response_mode: str = "auto",
    blob_container: str | None = None,
    blob_expires_s: int = 24 * 3600,
    timeout: float = 900.0,
    verify: bool = True,
    wgrib2: str | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,  # noqa: ARG001 — API parity
    max_retries: int = 3,  # noqa: ARG001 — API parity
    retry_wait: float = 10.0,  # noqa: ARG001 — API parity
    deadline: float | None = None,  # noqa: ARG001 — enforced by service timeout
) -> Path:
    """POST to the Container App and materialise the cropped GRIB2.

    Quota-gated via :func:`sharktopus.cloud.azure_quota.can_use_cloud_crop`.
    Raises :class:`SourceUnavailable` when the free tier is exhausted
    and the user hasn't authorised paid spend — the batch orchestrator
    then falls back to :mod:`sharktopus.sources.azure` (full download
    + local crop, no Container Apps cost).
    """
    allowed, reason = azure_quota.can_use_cloud_crop("azure")
    if not allowed:
        raise SourceUnavailable(f"azure_crop policy gate: {reason}")

    requests = _import_requests()

    url = service_url or _service_url_from_env()
    if not url:
        rg = resource_group or _resource_group_from_env()
        url = _discover_service_url(app_name, rg, subscription)
    if not url:
        raise SourceUnavailable(
            f"azure_crop could not resolve service URL for {app_name!r} "
            f"in {resource_group or _resource_group_from_env()!r}. "
            f"Set SHARKTOPUS_AZURE_URL or ensure "
            f"`az containerapp show` would succeed."
        )

    if dest is None:
        dest_dir = paths.output_dir(
            date=date, cycle=cycle, bbox=bbox, mode="fcst", root=root,
        )
    else:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / canonical_filename(cycle, fxx, product=product)

    payload = _build_payload(
        date, cycle, fxx,
        bbox=bbox, pad_lon=pad_lon, pad_lat=pad_lat,
        variables=variables, levels=levels,
        product=product, response_mode=response_mode,
        blob_container=blob_container, blob_expires_s=blob_expires_s,
    )

    headers = {"Content-Type": "application/json"}
    bearer = _bearer_from_env()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    start = time.monotonic()
    duration_s: float | None = None
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        duration_s = time.monotonic() - start
    except requests.RequestException as e:
        _record_best_effort(None)
        raise SourceUnavailable(f"container apps invoke failed: {e}") from e

    if resp.status_code >= 400:
        _record_best_effort(duration_s)
        body_preview = resp.text[:500] if resp.text else "<empty>"
        raise SourceUnavailable(
            f"container apps error ({resp.status_code}): {body_preview}"
        )

    try:
        envelope = resp.json()
    except ValueError as e:
        _record_best_effort(duration_s)
        raise SourceUnavailable(f"container apps returned non-JSON: {e}") from e

    inner = envelope.get("body") if isinstance(envelope.get("body"), dict) else envelope
    try:
        _materialize_response(inner, final, timeout=timeout)
    finally:
        billed_ms = inner.get("billed_duration_ms") if isinstance(inner, dict) else None
        if isinstance(billed_ms, (int, float)):
            duration_s = float(billed_ms) / 1000.0
        _record_best_effort(duration_s)

    _verify_or_raise(final, verify=verify, wgrib2=wgrib2)
    return final


def _record_best_effort(duration_s: float | None) -> None:
    """Persist an invocation against the quota counter; swallow errors."""
    try:
        azure_quota.record_invocation("azure", duration_s=duration_s)
    except Exception:
        pass


def _verify_or_raise(final: Path, *, verify: bool, wgrib2: str | None) -> None:
    if not verify or not grib.have_wgrib2(wgrib2):
        return
    try:
        n = grib.verify(final, wgrib2=wgrib2)
    except grib.GribError as e:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        raise SourceUnavailable(f"azure_crop output unparseable: {e}") from e
    if n <= 0:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        raise SourceUnavailable("azure_crop output has no records")
