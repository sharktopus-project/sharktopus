# Deploying the sharktopus Lambda

Step-by-step runbook for provisioning the `sharktopus` Lambda in a new
AWS account. The script `deploy/aws/provision.py` is **idempotent** —
safe to rerun.

Validated against account `120811381286` region `us-east-1`.

---

## Authentication — your password never touches sharktopus

**Always prefer the browser/SSO flow.** You type your password at your
company's SSO page, in your own browser. sharktopus only ever sees the
short-lived **access token** that the flow returns afterwards — never
the password. The token sits in `~/.aws/sso/cache/` (when using the
`aws` CLI) or `~/.cache/sharktopus/aws_sso_token.json` (when using the
pure-Python flow).

Three supported auth paths. Pick the one that matches your host:

| Path | Requires `aws` CLI? | Requires sudo? | Best for |
|---|---|---|---|
| **A. Pure Python SSO** | No | No | Single-machine developers who don't want extra tooling |
| **B. AWS CLI + SSO** | Yes (one user-space install) | No | Users who already have `aws` or want the richer CLI |
| **C. Static IAM access keys** | No | No | Accounts without IAM Identity Center, CI runners |

Options A and B both use the same IAM Identity Center browser sign-in
page — your password is always typed at your SSO start URL, never in
your terminal or anywhere in this repo.

### Option A — Pure Python SSO, no CLI install (recommended for laptops)

`provision.py --auth sso-oidc` runs the IAM Identity Center OIDC
device-code flow using only `boto3` (already a dependency). It prints a
short code + URL, opens your browser, you approve, and temporary role
credentials land in the running process. Token is cached to
`~/.cache/sharktopus/aws_sso_token.json` so you don't re-authorize on
every run.

```bash
python deploy/aws/provision.py \
    --auth sso-oidc \
    --sso-start-url https://mycorp.awsapps.com/start \
    --sso-region us-east-1 \
    --region us-east-1
# → opens browser at the start URL
# → if multiple accounts/roles are visible, provision.py prompts
# → deploy proceeds with short-lived role credentials
```

Ask your AWS admin for the **SSO start URL** and **SSO region**. If you
already have the `aws` CLI configured for SSO, the start URL lives in
`~/.aws/config` under `sso_start_url =`.

### Option B — AWS CLI + SSO sign-in

For users who prefer having the `aws` CLI for unrelated reasons, or
who already installed it. Functionally equivalent to Option A: same
browser sign-in, same token lifetime. The CLI just persists the token
to `~/.aws/sso/cache/` so `boto3`'s default credential chain picks it
up without any env vars.

### Option C — Static IAM access keys (no CLI, no SSO)

For accounts that don't have IAM Identity Center, or for headless
hosts where browser sign-in isn't feasible. `provision.py --auth
access-key` prompts for `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
interactively; they live in the running process only (nothing is
written to `~/.aws/`). You can also set the standard env vars ahead of
time and use `--auth default`.

---

## Prerequisites

- An AWS account with **billing enabled** (Lambda free tier covers
  1M requests + 400k GB-s / month before charges kick in).
- An IAM identity with these permissions (or AdministratorAccess):
  - `AWSLambda_FullAccess`
  - `AmazonEC2ContainerRegistryFullAccess`
  - `AmazonS3FullAccess`
  - `IAMFullAccess` (needed to create the Lambda execution role)
- **AWS CLI v2** — only needed for Option B. Pure-Python (Option A) and
  static-key (Option C) paths never touch it. Download from
  <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>
  (choose the Linux/macOS/Windows installer).
- `boto3` (comes with `pip install sharktopus`) — required in all
  three paths since `provision.py` itself uses it.

## Step 1 — Authenticate

### Option A — Pure-Python SSO (recommended)

Skip the `aws` CLI entirely. You'll need from your org admin:

- `sso_start_url` (e.g. `https://mycorp.awsapps.com/start`)
- `sso_region` (e.g. `us-east-1`)

Then:

```bash
python deploy/aws/provision.py \
    --auth sso-oidc \
    --sso-start-url https://mycorp.awsapps.com/start \
    --sso-region us-east-1 \
    --region us-east-1
```

The script prints a short code + verification URL, opens your browser,
and polls until you approve. Temp credentials are used for that run;
the SSO access token is cached to `~/.cache/sharktopus/aws_sso_token.json`
so subsequent runs skip the browser until the token expires (typically
8-12 h).

### Option B — IAM Identity Center via AWS CLI

One-shot setup (first time only):

```bash
aws configure sso
```

This walks you through: SSO session name, SSO start URL, SSO region,
account+role selection, default region, default output, and a
**profile name** (let's say `sharktopus-deploy`).

Daily use:

```bash
aws sso login --profile sharktopus-deploy
```

This opens a browser window, asks you to authorize the CLI, and caches
a short-lived token in `~/.aws/sso/cache/`. `provision.py` with
`--auth default --profile sharktopus-deploy` picks it up via the boto3
credential chain.

### Option C — Static IAM User access keys

If your organization doesn't have IAM Identity Center, or you prefer an
IAM User: create an access key pair in the IAM console (Users → your
user → Security credentials → Create access key), then either:

```bash
# Interactive (no files touched):
python deploy/aws/provision.py --auth access-key --region us-east-1

# Or via env vars (same effect as --auth default):
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
python deploy/aws/provision.py --region us-east-1
```

If you prefer the aws CLI to persist the keys to `~/.aws/credentials`:

```bash
aws configure --profile sharktopus-deploy
# AWS Access Key ID [None]: AKIA...
# AWS Secret Access Key [None]: ...
```

Credentials then land in `~/.aws/credentials` as plaintext. **Rotate
every 90 days**, and never commit them.

### Confirm

If you used Options B or C with a named profile:

```bash
aws sts get-caller-identity --profile sharktopus-deploy
# -> {"UserId":"...", "Account":"123...", "Arn":"..."}
```

For Option A / access-key, `provision.py` makes the equivalent
`sts:GetCallerIdentity` call itself on startup and exits 2 with a
clear hint if credentials don't resolve.

## Step 2 — Run the provision script

```bash
cd /path/to/sharktopus
python deploy/aws/provision.py --profile sharktopus-deploy --region us-east-1
```

- `--profile`: names a profile from `~/.aws/config` or `~/.aws/credentials`
  (or rely on `AWS_PROFILE` env var). If omitted, `boto3`'s default
  credential chain is used (env → default profile → instance role).
- `--region`: where to create the Lambda. Defaults to `us-east-1`.
- `--hot-start N`: keeps N execution environments warm at all times.
  Default `0` (cold starts allowed, pure free-tier). `N=1` eliminates
  cold starts but is billed continuously (~$5/month per warm instance
  at 2048 MB).
- `--image-tag`: defaults to `lambda-latest`. Change to pin a specific
  release.
- `--dry-run`: prints the plan without touching AWS.

**What the script does (all idempotent):**

1. Creates an ECR **pull-through cache rule** named `ghcr` pointing at
   `ghcr.io`. This lets Lambda pull the public sharktopus image without
   GitHub credentials — the first pull warms the cache; subsequent cold
   starts serve from your ECR.
2. Creates IAM role `sharktopus-lambda-role` with CloudWatch Logs and
   scoped S3 write access to the crops bucket.
3. Creates S3 bucket `sharktopus-crops-<account>-<region>` with a 7-day
   lifecycle rule on the `crops/` prefix (so forgotten presigned-URL
   objects don't accumulate).
4. Creates or updates Lambda `sharktopus`: container-image package,
   2048 MB RAM, 4096 MB ephemeral disk, 300 s timeout.

Expected output (trimmed):

```
Target account 123456789012 region us-east-1 (ARN=arn:aws:iam::...)
Creating pull-through cache rule ghcr → ghcr.io
Warming pull-through cache: ...dkr.ecr.us-east-1.amazonaws.com/ghcr/...
Creating IAM role sharktopus-lambda-role
Creating S3 bucket sharktopus-crops-123456789012-us-east-1
Creating Lambda sharktopus
Deploy complete.
Function ARN : arn:aws:lambda:us-east-1:123...:function:sharktopus
```

## Step 3 — Smoke test

```bash
aws lambda invoke --function-name sharktopus --profile sharktopus-deploy \
  --cli-binary-format raw-in-base64-out \
  --payload '{"date":"20260419","cycle":"00","fxx":6,"product":"pgrb2.0p25",
              "response_mode":"inline",
              "bbox":{"lon_w":-50,"lon_e":-40,"lat_s":-25,"lat_n":-20},
              "variables":["TMP","UGRD","VGRD"],
              "levels":["500 mb","850 mb"]}' \
  /tmp/out.json
python3 -c "
import json, base64
d = json.load(open('/tmp/out.json'))
b = d['body'] if isinstance(d['body'], dict) else json.loads(d['body'])
print('statusCode=', d['statusCode'], 'mode=', b.get('mode'),
      'billed_ms=', b.get('billed_duration_ms'))
raw = base64.b64decode(b.get('b64') or '')
print('grib_bytes=', len(raw), 'magic=', raw[:4])
"
# -> statusCode= 200 mode= inline billed_ms= 2150
#    grib_bytes= 6782 magic= b'GRIB'
```

## Step 4 — Point the Python client at it

`aws_crop` is already in the default priority list — once the Lambda
exists in your account, the client finds it automatically via boto3.

```python
import sharktopus
sharktopus.fetch_batch(
    timestamps=['2026041900'],
    lat_s=-25, lat_n=-20, lon_w=-50, lon_e=-40,
    ext=24, interval=3,
    priority=['aws_crop'],   # force it; default priority includes it
    variables=['TMP','UGRD','VGRD'],
    levels=['500 mb','850 mb'],
)
```

Override the function name or region with `SHARKTOPUS_AWS_LAMBDA_NAME`
and `AWS_REGION` env vars if your setup differs from the defaults.

## Rollback / cleanup

```bash
# Delete the Lambda
aws lambda delete-function --function-name sharktopus --profile sharktopus-deploy

# Delete the IAM role (after detaching policies)
aws iam detach-role-policy --role-name sharktopus-lambda-role \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
    --profile sharktopus-deploy
aws iam delete-role-policy --role-name sharktopus-lambda-role \
    --policy-name sharktopus-s3-output --profile sharktopus-deploy
aws iam delete-role --role-name sharktopus-lambda-role --profile sharktopus-deploy

# Delete the pull-through cache rule (optional — no ongoing cost)
aws ecr delete-pull-through-cache-rule --ecr-repository-prefix ghcr \
    --profile sharktopus-deploy

# Delete the bucket (check for objects first; lifecycle is 7d, empty is fastest)
aws s3 rb s3://sharktopus-crops-<account>-us-east-1 --force \
    --profile sharktopus-deploy
```

## Known surprises

1. **`provision.py` fails with "Unable to locate credentials"** — your
   SSO token expired (normal, they last 8 h by default). Run
   `aws sso login --profile <your-profile>` and retry. `provision.py`
   will detect the auth failure and point you at this command
   automatically.
2. **ECR pull-through cache may take ~1-2 min on first pull** — the
   image is ~90 MB compressed; ECR streams it from GHCR. Subsequent
   cold starts of the Lambda serve the cached image in ~1 s.
3. **IAM role propagation delay** — a fresh role may fail the first
   `create_function` call with "role cannot be assumed". `provision.py`
   retries up to 10 times with backoff; usually resolves in 10-30 s.
4. **Lambda free tier** covers 1M requests + 400k GB-s/month
   perpetually. At 2048 MB and ~2 s billed per crop, that's ~100k
   crops/month before charges — more than enough for any single-user
   workflow.
