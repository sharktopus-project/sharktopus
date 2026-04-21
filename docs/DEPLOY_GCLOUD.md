# Deploying the sharktopus Cloud Run service

Step-by-step runbook for provisioning `sharktopus-crop` in a new GCloud
project. Mirrors the AWS deploy doc but for Cloud Run. The script
`deploy/gcloud/provision.py` is **idempotent** — safe to rerun.

Validated end-to-end on **2026-04-19** against
`project-28fbcdc3-53c9-4298-bed` from megashark.

---

## Authentication — your password never touches sharktopus

**Always prefer browser/token-based flows.** You type your password at
Google's own sign-in page, in your own browser. sharktopus only ever
sees the short-lived **access token** that Google issues afterwards —
never the password. Revoke at any time at
<https://myaccount.google.com/permissions>.

Three execution modes for `provision.py`, selected via `--auth`:

| Path | Requires `gcloud` CLI? | Needs a service-account key? | Best for |
|---|---|---|---|
| **A. `--auth browser`** | No | No | Fresh `pip install` on a laptop — smoothest path |
| **B. `--auth cli`** (default) | Yes (one user-space install) | No | Users who already have `gcloud` or want the richer CLI |
| **C. `--auth sdk`** | No | Yes (or ADC) | CI, headless hosts, GCE/Cloud Shell |

All three ultimately use OAuth2: Option A pops a browser against the
sharktopus Desktop OAuth client, Option B delegates to the `gcloud`
CLI, Option C uses `google-auth` with a service-account JSON or a
pre-seeded ADC file.

### Option A — browser OAuth (no CLI, no key)

`provision.py --auth browser` launches the standard Google "installed
app" flow: a random localhost port listens for the OAuth callback,
your browser opens against `accounts.google.com`, you pick an account,
consent to the `cloud-platform`, `openid`, and `email` scopes, and
sharktopus caches the refresh token at
`~/.cache/sharktopus/gcloud_token.json` (0o600). Subsequent deploys
skip the browser — the cached token refreshes silently.

**Bonus when combined with `--authenticated-only`:** the deploy
automatically creates a `sharktopus-invoker` service account, grants
it `roles/run.invoker` on the Cloud Run service, and grants *your*
user `roles/iam.serviceAccountTokenCreator` on the SA. The client
then invokes the authenticated service using only the cached browser
OAuth token — no downloaded key, no gcloud CLI. The deploy prints
the `SHARKTOPUS_GCLOUD_INVOKER_SA` env var to export.

> **Unverified app warning.** Until Google finishes verifying the
> sharktopus OAuth app, the consent screen shows a yellow "Google
> hasn't verified this app" banner. Click *Advanced → Go to sharktopus
> (unsafe)* to proceed during the verification window. See
> [`OAUTH_VERIFICATION.md`](OAUTH_VERIFICATION.md) for the
> submission runbook; until it completes the app stays in Testing
> mode with a 2-user allowlist.

Pre-req: a Python install with `google-auth-oauthlib` (see Step 1b).

### Option B — gcloud CLI (recommended when already installed)

Two subcommands cover both surfaces:

| Purpose                 | Command                                         | Creates                       |
|-------------------------|--------------------------------------------------|-------------------------------|
| `gcloud` CLI itself     | `gcloud auth login --no-launch-browser`          | Token used by `gcloud ...`    |
| Python client (ADC)     | `gcloud auth application-default login ...`      | Token used by `google-auth`   |

The deployer needs both (provision.py uses gcloud; the Python client
later uses ADC to mint ID tokens for authenticated Cloud Run calls).

### Option C — pure Python with keys/ADC (`--auth sdk`)

No `gcloud` binary required. Credentials come from the standard
`google-auth` resolution chain:

1. `--service-account-json /path/to/key.json` (CLI flag).
2. `GOOGLE_APPLICATION_CREDENTIALS` → service account JSON path.
3. `~/.config/gcloud/application_default_credentials.json` (ADC file
   copied in from any machine that has `gcloud`).
4. GCE/Cloud Shell metadata server (when running inside GCP).

This is the path for **service-account-based production setups** (CI,
headless boxes). For a single-laptop install, prefer **Option A
(browser)** — no key to manage.

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

## Step 1a — (Option B only) Install the gcloud CLI

Skip this section if you picked **Option A** (`--auth browser`) or
**Option C** (`--auth sdk`). **End users of `pip install sharktopus`
never need the gcloud CLI** — the runtime client talks to the Cloud
Run service via plain HTTPS and mints its own ID token via ADC.

User-space install (no sudo required):

```bash
cd ~
curl -sSLO https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz
tar -xf google-cloud-cli-linux-x86_64.tar.gz
rm google-cloud-cli-linux-x86_64.tar.gz
./google-cloud-sdk/install.sh --quiet --path-update=true --rc-path="$HOME/.bashrc"
# New shell, or source ~/.bashrc, to pick up the updated PATH.
```

## Step 1b — (Options A + C only) Install Python SDKs

Skip this section if you picked **Option B** (`--auth cli`).

```bash
# Core SDKs both pure-Python modes need:
pip install \
    google-auth \
    google-cloud-run \
    google-cloud-artifact-registry \
    google-cloud-service-usage \
    google-cloud-storage

# Extra dependency for Option A (browser OAuth):
pip install google-auth-oauthlib
```

Then run `provision.py --auth browser ...` or `provision.py --auth sdk ...`;
see Step 3.

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

### Option A — Browser OAuth (`--auth browser`)

```bash
cd /path/to/sharktopus
python3 deploy/gcloud/provision.py \
    --auth browser \
    --project YOUR-PROJECT-ID \
    --authenticated-only
# Browser window opens → sign in → close tab when it says "login succeeded".
# Re-runs reuse the cached token at ~/.cache/sharktopus/gcloud_token.json.
```

Pass `--oauth-client-json /path/to/oauth_client.json` if you built a
sharktopus fork with your own Desktop OAuth client; otherwise the
bundled one at `deploy/gcloud/oauth_client.json` is used.

### Option B — CLI-driven (`--auth cli`, default)

```bash
cd /path/to/sharktopus
PATH="$HOME/google-cloud-sdk/bin:$PATH" python3 deploy/gcloud/provision.py \
    --project YOUR-PROJECT-ID \
    --authenticated-only
```

### Option C — Pure-Python SDK (`--auth sdk`)

```bash
cd /path/to/sharktopus
# Option 1: service account JSON on disk
python3 deploy/gcloud/provision.py \
    --auth sdk \
    --service-account-json /path/to/key.json \
    --project YOUR-PROJECT-ID \
    --authenticated-only

# Option 2: env var
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
python3 deploy/gcloud/provision.py \
    --auth sdk --project YOUR-PROJECT-ID --authenticated-only

# Option 3: ADC file already present (from a prior gcloud auth ADC login)
python3 deploy/gcloud/provision.py \
    --auth sdk --project YOUR-PROJECT-ID --authenticated-only
```

Common flags (all three options):

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

With `--authenticated-only`, the client mints an audience-scoped ID
token in one of four ways (resolved in order):

1. `SHARKTOPUS_GCLOUD_ID_TOKEN` — explicit token override.
2. ADC / metadata server via `google.oauth2.id_token.fetch_id_token`
   — service-account ADC, GCE / Cloud Run / GKE.
3. **Browser-OAuth cache + SA impersonation** — if you deployed via
   `--auth browser`, the client reads
   `~/.cache/sharktopus/gcloud_token.json`, impersonates
   `sharktopus-invoker@<project>.iam.gserviceaccount.com` (via the
   `generateIdToken` IAM API), and mints the audience-scoped token.
   No gcloud CLI required. Requires
   `SHARKTOPUS_GCLOUD_INVOKER_SA` or `GOOGLE_CLOUD_PROJECT` to be
   set.
4. `gcloud auth print-identity-token` — last-resort fallback when the
   gcloud CLI is present and user-ADC is the only credential source.

Grant additional principals `roles/run.invoker` with:

```bash
~/google-cloud-sdk/bin/gcloud run services add-iam-policy-binding sharktopus-crop \
    --member="user:other.person@example.com" \
    --role="roles/run.invoker" \
    --region=us-central1 --project=YOUR-PROJECT-ID
```

Or — for the browser-OAuth path — grant them
`roles/iam.serviceAccountTokenCreator` on the `sharktopus-invoker` SA
instead, and they impersonate from their own machine with only a
browser OAuth token.

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
