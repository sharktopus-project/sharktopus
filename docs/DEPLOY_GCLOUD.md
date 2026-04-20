# Deploying the sharktopus Cloud Run service

Step-by-step runbook for provisioning `sharktopus-crop` in a new GCloud
project. Mirrors the AWS deploy doc but for Cloud Run. The script
`deploy/gcloud/provision.py` is **idempotent** — safe to rerun.

Validated end-to-end on **2026-04-19** against
`project-28fbcdc3-53c9-4298-bed` from megashark.

---

## Prerequisites

- A GCloud project with **billing enabled** (free-tier usage still
  requires a linked billing account, just not charged until the quota
  is exceeded).
- A Google account that is **Owner** or **Editor** on that project.
  Alternatively, a service account with these four roles on the project:
  - `roles/run.admin`
  - `roles/storage.admin`
  - `roles/serviceusage.serviceUsageAdmin`
  - `roles/iam.serviceAccountUser`
- The `sharktopus` GHCR container package marked as **public** (once per
  GitHub org). See [README](../README.md#cloud-side-crop-gcloud_crop)
  for the one-shot visibility change.

## Step 1 — Install the gcloud CLI

Only the deployer needs this. **End users of `pip install sharktopus` never need the gcloud CLI** — the runtime client talks to the Cloud Run service via plain HTTPS and mints its own ID token via ADC.

User-space install (no sudo required):

```bash
cd ~
curl -sSLO https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz
tar -xf google-cloud-cli-linux-x86_64.tar.gz
rm google-cloud-cli-linux-x86_64.tar.gz
./google-cloud-sdk/install.sh --quiet --path-update=true --rc-path="$HOME/.bashrc"
# New shell, or source ~/.bashrc, to pick up the updated PATH.
```

## Step 2 — Authenticate

> **Gotcha.** `gcloud auth login --no-launch-browser` needs an
> **interactive terminal** — a code must be pasted back into stdin
> after logging in via browser. Claude Code's `!` prefix pipes output
> to the chat but does **not** forward subsequent stdin lines, so the
> process fails with `EOFError`. **Run this step in a real SSH
> terminal, not via the Claude `!` prefix.**

In a direct terminal on the deploy host:

```bash
~/google-cloud-sdk/bin/gcloud auth login --no-launch-browser
```

The command prints a long URL. Open it in a browser on any machine
(your laptop is fine), sign in with the Google account that has
Owner/Editor on the target project, copy the verification code shown,
paste it back into the terminal.

The first-time deploy also needs **ADC** (Application Default
Credentials) for programs using `google-auth`. `provision.py` itself
doesn't — it only shells out to `gcloud` — but the `gcloud_crop`
Python client does, when minting ID tokens to call the authenticated
Cloud Run service.

```bash
~/google-cloud-sdk/bin/gcloud auth application-default login --no-launch-browser
```

Same process: open printed URL, sign in, paste code.

Confirm:

```bash
~/google-cloud-sdk/bin/gcloud auth list
# * <your-email>@example.com   ← must have the asterisk
```

## Step 3 — Run the provision script

```bash
cd /path/to/sharktopus
PATH="$HOME/google-cloud-sdk/bin:$PATH" python3 deploy/gcloud/provision.py \
    --project YOUR-PROJECT-ID \
    --authenticated-only
```

- `--authenticated-only` deploys with `--no-allow-unauthenticated`, so
  the Cloud Run URL requires an IAM ID token to invoke. Recommended
  for all institutional / non-trivial deploys.
- Omit the flag for a **fully public URL** (easier for quick demos,
  but anyone knowing the URL consumes your free tier).
- `--min-instances N` keeps N warm instances. Default 0 (cold starts
  allowed); N ≥ 1 eliminates cold starts but is billed continuously
  (~$5/month per warm instance at 1 vCPU + 2 GiB).
- `--dry-run` prints the plan without creating anything.

**What the script does (all idempotent):**

1. Enables four APIs: `run`, `storage`, `iamcredentials`, `artifactregistry`.
2. Creates the GCS bucket `sharktopus-crops-<project>` with a 7-day
   lifecycle on `crops/` so forgotten signed-URL objects expire.
3. Creates an Artifact Registry **remote repository** named
   `ghcr-proxy` in the region, pointing upstream at `https://ghcr.io`.
   **This is required** — Cloud Run rejects `ghcr.io/*` image URLs
   directly; it only accepts `gcr.io`, `<region>-docker.pkg.dev`, and
   `docker.io`. The remote repo is the GCloud analogue of the AWS ECR
   Pull-Through Cache the AWS deploy uses.
4. Deploys `sharktopus-crop` to Cloud Run, referencing the image via
   the AR proxy URL (`<region>-docker.pkg.dev/<project>/ghcr-proxy/sharktopus-project/sharktopus:cloudrun-latest`).
   AR fetches layers from GHCR on first pull and caches them.

Expected output (trimmed):

```
Enabling APIs: run, storage, iamcredentials, artifactregistry
Creating bucket gs://sharktopus-crops-<project>
Creating AR remote repository us-central1/ghcr-proxy → ghcr.io
Deploying Cloud Run service sharktopus-crop ...
Service URL: https://sharktopus-crop-<hash>-uc.a.run.app
Deploy complete.
```

Save the service URL — that's what clients point at via
`SHARKTOPUS_GCLOUD_URL`.

## Step 4 — Smoke test

Healthcheck:

```bash
URL="https://sharktopus-crop-<hash>-uc.a.run.app"
TOKEN=$(~/google-cloud-sdk/bin/gcloud auth print-identity-token)
curl -sS -H "Authorization: Bearer $TOKEN" "$URL/"
# -> {"status":"ok"}
```

Real crop (small bbox, a couple of variables):

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"date":"20260417","cycle":"00","fxx":6,"product":"pgrb2.0p25",
       "response_mode":"inline",
       "bbox":{"lon_w":-50,"lon_e":-40,"lat_s":-25,"lat_n":-20},
       "variables":["TMP","UGRD","VGRD"],
       "levels":["500 mb","850 mb"]}' \
  "$URL/" | python3 -c "
import json, sys, base64
d = json.load(sys.stdin)
b = d['body']
print('statusCode=', d['statusCode'], 'mode=', b.get('mode'),
      'billed_ms=', b.get('billed_duration_ms'))
raw = base64.b64decode(b.get('b64') or '')
print('grib_bytes=', len(raw), 'magic=', raw[:4])
"
# -> statusCode= 200 mode= inline billed_ms= 1287
#    grib_bytes= 6782 magic= b'GRIB'
```

Benchmarks from the validation run (us-central1, cold start):

| Operation | Time | Notes |
|---|---|---|
| First POST (cold start) | ~7-9 s | container pulled + started + crop |
| Warm POST (subsequent)  | ~1.5 s | 1.3 s billed server-side |
| Healthcheck (warm)      | ~2.5 s | + TLS + regional round-trip |

## Step 5 — Point the Python client at it

Two ways. For an interactive session:

```bash
export SHARKTOPUS_GCLOUD_URL="https://sharktopus-crop-<hash>-uc.a.run.app"
python3 -c "
from sharktopus.sources import gcloud_crop
p = gcloud_crop.fetch_step(
    '20260417', '00', 6,
    bbox=(-50, -40, -25, -20),
    variables=['TMP','UGRD','VGRD'], levels=['500 mb','850 mb'],
)
print(p, p.stat().st_size, 'bytes')
"
```

For `fetch_batch`:

```python
import sharktopus
sharktopus.fetch_batch(
    timestamps=['2026041700'],
    lat_s=-25, lat_n=-20, lon_w=-50, lon_e=-40,
    ext=24, interval=3,
    priority=['gcloud_crop'],      # force it; default priority also includes it
    variables=['TMP','UGRD','VGRD'],
    levels=['500 mb','850 mb'],
)
```

With `--authenticated-only`, the client mints an ID token automatically
via ADC (`google.oauth2.id_token.fetch_id_token`). Make sure the
calling account has `roles/run.invoker` on the service — grant to
additional principals with:

```bash
~/google-cloud-sdk/bin/gcloud run services add-iam-policy-binding sharktopus-crop \
    --member="user:other.person@example.com" \
    --role="roles/run.invoker" \
    --region=us-central1 --project=YOUR-PROJECT-ID
```

## Step 6 — Monitor quota and cost

From any shell with `pip install sharktopus`:

```bash
sharktopus --quota gcloud
# -> sharktopus cloud quota — gcloud — month 2026-04
#    invocations   :            3 /    2,000,000  ( 0.00%)
#    vCPU-seconds  :          3.9 /      180,000  ( 0.00%)
#    GB-seconds    :          7.8 /      360,000  ( 0.00%)
#    spend (paid)  : $0.0000
#    avg duration  : 1.30 s  (1.0 vCPU, 2.0 GiB assumed)
#    est next call : $0.000023
#    next call     : allowed
```

The counter is client-side (`~/.cache/sharktopus/quota.json`, keyed by
`gcloud`). Paid invocations start counting only after the monthly free
tier is exceeded. See the README for the `SHARKTOPUS_ACCEPT_CHARGES` /
`SHARKTOPUS_MAX_SPEND_USD` env gates.

## Rollback / cleanup

```bash
# Delete the Cloud Run service
~/google-cloud-sdk/bin/gcloud run services delete sharktopus-crop --region=us-central1 --project=YOUR-PROJECT-ID

# Delete the AR remote repo (optional — no ongoing cost)
~/google-cloud-sdk/bin/gcloud artifacts repositories delete ghcr-proxy --location=us-central1 --project=YOUR-PROJECT-ID

# Delete the bucket (check for orphaned objects first)
~/google-cloud-sdk/bin/gcloud storage rm --recursive gs://sharktopus-crops-YOUR-PROJECT-ID
```

APIs stay enabled; no cost from that. Service account keys you might
have added stay in IAM until explicitly removed.

## Known surprises

1. **`gcloud auth login --no-launch-browser` needs a real terminal**
   — Claude Code's `!` prefix does not forward stdin, so the code-paste
   step fails with `EOFError`. Use SSH.
2. **Cloud Run does not pull from GHCR directly** — error
   `spec.template.spec.containers[0].image: Expected an image path
   like [region.]gcr.io, [region-]docker.pkg.dev or docker.io`. Fix:
   the AR remote repository layer handled by `ensure_ghcr_proxy()`.
3. **The `gfs-downloader` service account** that ships cached on
   megashark for GCS reads **cannot deploy** — it only has read
   permission on the GFS bucket. A user account (Owner/Editor) or a
   new SA with the 4 admin roles is required.
4. **URL copied from Claude chat may be HTML-entity-escaped** (`&amp;`
   instead of `&`), which makes Google reject the auth URL with
   `Missing required parameter: client_id`. Always copy from a real
   terminal buffer, not from the web chat output.
