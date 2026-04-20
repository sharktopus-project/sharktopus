# Image storage: how it works and what it costs

This doc answers two questions that come up when explaining the cloud-
crop deploy model to end users:

1. **Does every call re-pull the container image from GHCR?** No — the
   image is cached **once** in the user's cloud account; every
   subsequent cold start reads from that cache.
2. **Is the image big enough to bust the always-free storage tier?** No
   — by a wide margin. Measured 2026-04-19.

## The "pull once" model

Both deploy paths (GCloud Cloud Run via `deploy/gcloud/provision.py`,
AWS Lambda via `deploy/aws/provision.py`) follow the same pattern:

```
GHCR (public image)
   │ upstream — pulled ONCE on first deploy
   ▼
User's Artifact Registry / ECR          ← storage lives here
   │ cached layers served from user's project
   ▼
Cloud Run instance / Lambda execution   ← cold start reads from cache
```

The mechanism:

- **GCloud:** `provision.py` creates an Artifact Registry
  **remote repository** named `ghcr-proxy` pointing at `https://ghcr.io`.
  On first `gcloud run deploy`, AR pulls the layers from GHCR, caches
  them in the user's project, and serves subsequent pulls from that
  cache.
- **AWS:** `provision.py` creates an ECR **pull-through cache rule** for
  the `ghcr-public` registry. On first Lambda function creation, ECR
  pulls from GHCR, caches in the user's private ECR registry, and
  serves from there thereafter.

**Consequence for the user:** after their first successful deploy,
**they are independent from GHCR**. If upstream GHCR vanished tomorrow,
their deployed service keeps running indefinitely.

**Updating to a new version:** re-run `provision.py`. The upstream tag
(`cloudrun-latest` on GCloud, `:latest` on AWS) is re-resolved; if the
upstream digest changed, the proxy fetches the new layers and the
service deploys to the new revision. No upstream tag change → no
upstream pull.

## Measured image sizes

Measured against the built-and-published images on 2026-04-19:

| Target | Uncompressed runtime | Compressed (stored in cloud) |
|---|---|---|
| `ghcr.io/sharktopus-project/sharktopus:cloudrun-latest` (Cloud Run) | 287 MB | **~66 MB** |
| `ghcr.io/sharktopus-project/sharktopus:latest` (AWS Lambda)         | 237 MB | **~90 MB**¹ |

¹ Compressed size for ECR. Amazon Linux 2 base layers compress less
aggressively than Debian slim.

**What matters for billing is the compressed size** — that's what AR
and ECR charge for. The uncompressed size only matters for instance
cold-start disk I/O and runtime memory footprint.

### Composition (Cloud Run image)

Breakdown of the ~66 MB compressed total (from
`gcloud artifacts files list`):

| Layer                                               | Size    |
|-----------------------------------------------------|---------|
| Debian bookworm-slim base                           | ~27 MB  |
| Python 3.11 install (+ deps)                        | ~15 MB  |
| Runtime apt packages (libgfortran5, libgomp1, etc.) | ~15 MB  |
| Pip packages (flask, gunicorn, google-cloud-storage, requests) | ~3 MB |
| wgrib2 binary + supporting libs                     | ~4 MB   |
| `main.py`, `requirements.txt`, metadata             | < 100 KB |

`deploy/gcloud/requirements.txt` is intentionally minimal — no numpy,
scipy, or xarray. Everything heavy lives in the client (which runs on
the user's machine, not in Cloud Run). The server only needs to cut
GRIB2 and ship bytes.

## Free-tier headroom

| Provider     | Always-free storage                     | Our image  | Headroom |
|--------------|-----------------------------------------|------------|----------|
| GCloud AR    | 0.5 GB/month                            | ~66 MB     | **~7.5×** |
| AWS ECR      | 0.5 GB/month (first 12 months only²)    | ~90 MB     | **~5.5×** |

² After the 12-month AWS Free Tier elapses, ECR private storage is
billed at `$0.10/GB-month`. At 90 MB that's `$0.009/month` — below the
minimum billable unit on most AWS statements.

**Practical conclusion:** we have enough headroom to *add* stuff
(numpy for server-side postprocessing, xarray if we start returning
NetCDF, etc.) without leaving the free tier on either provider. There
is no current pressure to shrink.

## If we ever need to shrink

In priority order (best ROI first):

1. **Multi-stage build + consolidate `RUN` steps** — small win, ~5 MB
   compressed, zero runtime change. Already partially done.
2. **Distroless base** (`gcr.io/distroless/python3`) — ~20 MB smaller,
   also removes shell (security+). Complicates `apt-get` for
   libgfortran5 runtime dep; would require vendoring it.
3. **Alpine base** (musl) — ~30-40 MB smaller, but wgrib2 would need
   to be rebuilt against musl. Significant effort; not worth unless
   AR/ECR costs become non-trivial.
4. **Statically-linked wgrib2** — drops libgfortran runtime dependency.
   Possible but fragile — wgrib2's Makefiles don't love it.

None of the above is worth pursuing until we have a concrete cost
reason. As of this doc, we don't.

## What end users (pip install) pay in storage

**Zero.** End users of `pip install sharktopus` never touch a container.
The client speaks HTTPS to the deployed service and receives GRIB2
bytes back. All storage costs land on the **deployer** (whoever runs
`provision.py`), and even those are within free tier as shown above.
