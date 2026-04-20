# Contributor guide: the container-image publishing pipeline

End users of `pip install sharktopus` never build containers — they run
the pure-Python client and talk HTTPS to whatever the deployer
provisioned. This doc is for **contributors who touch the server-side
code** under `deploy/` (the Flask/gunicorn handler that runs inside
AWS Lambda, GCloud Cloud Run, or Azure Container Apps). If you change
`deploy/<cloud>/main.py`, `handler.py`, `requirements.txt`, or
`Dockerfile`, a new image has to be published to GHCR so that the
`provision.py` scripts can deploy it.

## Why GHCR at all

All three cloud-crop sources (`aws_crop`, `gcloud_crop`, `azure_crop`)
deploy the **same logical service** — a tiny HTTP server that reads
GFS byte-ranges from a public mirror, crops with `wgrib2`, and returns
GRIB2 bytes. Publishing one image per cloud variant in a single
registry (GHCR) means:

- Any user can run `provision.py` and get the service deployed without
  authenticating to the image host — GHCR packages are public.
- The deployer's own cloud caches the pulled image (ECR pull-through
  cache on AWS, Artifact Registry remote repo on GCloud, direct pull
  on Azure Container Apps); after the first deploy they're decoupled
  from upstream GHCR. See `docs/IMAGE_STORAGE_AND_BILLING.md`.
- Updates to server behaviour ship by bumping the image tag. Users
  re-run `provision.py`; no client release is needed.

## The workflow: `.github/workflows/build-image.yml`

Triggers:

- Push to `main` (publishes `<variant>-latest` + `<variant>-sha-<short>`).
- Git tag `v*` (publishes `<variant>-<ref>` + `<variant>-<major>.<minor>`).
- Manual dispatch from the Actions tab.

Jobs: a single `build-and-push` job with a three-entry matrix:

| variant    | context          | Dockerfile                |
|------------|------------------|---------------------------|
| `lambda`   | `deploy/aws`     | `deploy/aws/Dockerfile`   |
| `cloudrun` | `deploy/gcloud`  | `deploy/gcloud/Dockerfile`|
| `azure`    | `deploy/azure`   | `deploy/azure/Dockerfile` |

Each matrix entry builds one image and pushes all tags for that
variant. Entries run in parallel; `fail-fast: false` means a broken
Dockerfile in one variant doesn't cancel the others.

Published tag shape (per variant, with `{variant}` ∈ `lambda`,
`cloudrun`, `azure`):

- `:{variant}-latest` — moving tag, updated on every `main` push.
- `:{variant}-sha-<short>` — immutable, one per commit.
- `:{variant}-<ref>` — git tag (e.g. `azure-v0.1.0`).
- `:{variant}-{major}.{minor}` — semver floor (e.g. `azure-0.1`).

The `provision.py` scripts default to `:{variant}-latest`; pass
`--image-tag <variant>-sha-<short>` to pin.

## Repository permissions the workflow needs

In the job block:

```yaml
permissions:
  contents: read
  packages: write
```

`packages: write` is what lets the auto-provided `GITHUB_TOKEN` push to
GHCR. No PAT, no external secret. First push from a new repo creates
the GHCR package automatically **and it's private by default**.

## One-time setup: make the GHCR package public

After the **first** successful build, the GHCR package
(`ghcr.io/<org>/sharktopus`) is private. Users' cloud accounts can't
pull a private image over anonymous HTTPS, so the `provision.py`
scripts would fail.

Fix (once per repo, by an org admin):

1. Go to `https://github.com/orgs/<org>/packages`.
2. Click `sharktopus` → `Package settings`.
3. Scroll to `Danger Zone` → `Change visibility` → `Public`.

After this, any subsequent tag pushed by CI is publicly pullable with
no auth. Verification:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:<org>/sharktopus:pull&service=ghcr.io" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -sI -H "Authorization: Bearer $TOKEN" \
     -H "Accept: application/vnd.oci.image.index.v1+json" \
     "https://ghcr.io/v2/<org>/sharktopus/manifests/azure-latest" | head -1
# → HTTP/2 200
```

(Anonymous `ghcr.io` pulls still need a Bearer token — it's a handshake
token, not auth. The `token` endpoint issues one unconditionally for
public packages. That's the `docker pull` flow under the hood.)

## Adding a new cloud variant

Four steps:

1. **Create `deploy/<newcloud>/`** with `Dockerfile`, `main.py` (or
   `handler.py` for Lambda-style), and `requirements.txt`. Mirror an
   existing variant — `deploy/azure/` is the most recent reference.
2. **Add a matrix entry** in `.github/workflows/build-image.yml`:
   ```yaml
   - variant: newcloud
     context: deploy/<newcloud>
     dockerfile: deploy/<newcloud>/Dockerfile
   ```
3. **Update the header comment** in that workflow file to list the new
   variant (it's the only contributor-facing place that enumerates
   variants outside the matrix).
4. **Write `deploy/<newcloud>/provision.py`** and
   `src/sharktopus/sources/<newcloud>_crop.py` + quota module. See the
   Azure PR (`16e92b7`) for the full change-set shape.

## Local testing before you push

Always build the image locally first. A Dockerfile regression that
only surfaces in CI wastes everyone's minutes.

```bash
# From the repo root, e.g. for the Azure variant:
docker build -t sharktopus-azure-local -f deploy/azure/Dockerfile deploy/azure

# Smoke the handler without any cloud:
docker run --rm -p 8080:8080 sharktopus-azure-local &
curl -s -XPOST -H 'Content-Type: application/json' http://localhost:8080/ \
    -d '{"date":"20260417","cycle":"00","fxx":6,
         "bbox":[-50,-40,-25,-20],
         "variables":["TMP"],"levels":["500 mb"],
         "mode":"inline"}' | python3 -m json.tool | head -5
```

For the `blob` / `s3` / `gcs` modes you need real credentials because
the handler tries to mint a signed URL — that part is CI-only-testable
unless you mock the SDK. The `inline` mode exercises the
byte-range-fetch + wgrib2-crop path end-to-end and is usually enough to
catch Dockerfile / Python-dep regressions.

## Checking that a push published what you expect

After pushing to `main`:

```bash
gh run watch                       # streams the latest run
gh run list --workflow=build-image.yml --limit=3
```

Then verify the tag landed on GHCR. The tags list endpoint uses a
plain token and needs no accept headers:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:<org>/sharktopus:pull&service=ghcr.io" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" \
     "https://ghcr.io/v2/<org>/sharktopus/tags/list" | python3 -m json.tool
```

Look for the two new entries: `<variant>-latest` (updated digest) and
`<variant>-sha-<short-commit>` (new).

Note: the manifest endpoint requires the right `Accept` header because
the workflow publishes an **OCI image index** (multi-platform wrapper),
not a plain Docker v2 manifest. Without
`Accept: application/vnd.oci.image.index.v1+json`, GHCR returns 404
even though the tag exists. This tripped us once; budget for it.

## Re-deploying after an image update

The image tag that matters for each cloud:

| Cloud  | Default tag        | Re-deploy command                     |
|--------|--------------------|---------------------------------------|
| AWS    | `:latest`          | `python deploy/aws/provision.py --subscription-role-arn …` |
| GCloud | `:cloudrun-latest` | `python deploy/gcloud/provision.py --project-id …`         |
| Azure  | `:azure-latest`    | `python deploy/azure/provision.py --subscription …`        |

`provision.py` is idempotent on all three clouds: re-running it detects
the existing deployment, resolves the image tag against GHCR, and
creates a new cloud-side revision only if the digest changed. Zero
downtime, no flag needed.

If you need to deploy a specific commit (not `latest`):

```bash
python deploy/azure/provision.py ... --image-tag azure-sha-16e92b7
```

## When you probably don't need to touch any of this

- Pure client-side changes under `src/sharktopus/` that don't cross
  into `deploy/` — the deployed image is unchanged, CI still rebuilds
  it on every `main` push, but re-deploying is optional.
- Doc-only changes — the workflow still runs (cheap) but no behaviour
  change ships.
- Test-only changes — same.
