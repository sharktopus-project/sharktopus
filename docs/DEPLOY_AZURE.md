# Deploying the sharktopus Container App on Azure

Step-by-step runbook for provisioning `sharktopus-crop` as an Azure
Container App in a new subscription. Mirrors the AWS / GCloud deploy
docs but for Container Apps. The script `deploy/azure/provision.py`
is **idempotent** — safe to rerun.

The whole flow is also wrapped behind one command:

```bash
sharktopus --setup azure
```

which installs the CLI (if missing), guides you through `az login`,
prompts for subscription / region / resource group, and finally
shells out to `provision.py`.

## Why Container Apps and not Functions

The original CONVECT experiment ran the cropper on Azure Functions with
a zip-deployed wgrib2 binary. That fought with the Functions runtime's
permissions and binary-portability model: the same binary that worked
on Cloud Run / Lambda failed loader resolution on Functions.

Container Apps accepts any HTTP container image on the same OCI
contract Cloud Run uses, so we **reuse the same image** —
`ghcr.io/sharktopus-project/sharktopus:azure-latest` is a Cloud Run
image with `azure-storage-blob` swapped for `google-cloud-storage` in
the requirements. wgrib2 issue gone.

## Authentication — your password never touches sharktopus

**Always prefer the browser/device-code flow (Option A below).** You
type your Azure password on Microsoft's own sign-in page, in your own
browser. sharktopus only ever sees the short-lived **refresh token**
that the Azure CLI writes to `~/.azure/` afterwards — never the
password. You can inspect or revoke that token at any time at
<https://myaccount.microsoft.com/>.

Under the hood, `provision.py` uses `azure-identity`'s
`DefaultAzureCredential`, which looks for credentials in this order:

1. Environment variables (`AZURE_CLIENT_ID` / `_SECRET` / `_TENANT_ID`).
2. Managed identity (when running inside Azure itself).
3. **The Azure CLI session written by `az login`** ← default for humans.
4. A few SDK-specific fallback paths.

Option B (service principal via env vars) exists purely for **CI
runners and sudo-less shared hosts** — situations where there's no
browser and no way to keep a CLI session around. A human on their own
laptop should never need Option B. If a tutorial tells you to paste
a password into a terminal to use sharktopus, it's wrong — ask.

## Prerequisites

- An Azure subscription with billing set up (free-tier usage still
  needs a linked card, just not charged until the quota is exceeded).
- The signed-in account must be **Owner** or **Contributor** on the
  subscription (or have `Microsoft.Authorization/roleAssignments/write`
  on the resource group, since the script grants the Container App
  identity blob-data access).
- `Microsoft.App`, `Microsoft.Storage` and
  `Microsoft.OperationalInsights` resource providers — `provision.py`
  registers these on first run.
- The `sharktopus` GHCR container package marked **public** (once per
  GitHub org). Contributor-side details — CI matrix, tag naming, how
  to rebuild the image locally — are in
  [`docs/CONTRIBUTING_IMAGES.md`](CONTRIBUTING_IMAGES.md). End users
  deploying from an upstream image don't need to touch any of that.

## Step 1 — Authenticate

Two supported auth paths. Only one is needed.

### Option A — Azure CLI + browser sign-in (recommended)

**This is the path you want 99% of the time.** You sign in on
Microsoft's own website; no secret ever touches this repo or your
terminal history.

Install the CLI (sudo **once**):

```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash    # Debian/Ubuntu
brew update && brew install azure-cli                     # macOS
```

Other distros: <https://learn.microsoft.com/cli/azure/install-azure-cli>.

Then pick one sign-in flow:

```bash
az login                           # opens the default browser
az login --use-device-code         # prints a code + URL (headless / SSH)
```

Both flows send you to `microsoft.com`, you type your Azure password
**there**, Microsoft redirects back with a refresh token, and the CLI
writes that token to `~/.azure/`. If you have multiple subscriptions:

```bash
az account set --subscription <ID>
```

`DefaultAzureCredential` in `provision.py` picks up the CLI session
automatically. **No env vars, no secrets in the shell.**

### Option B — Service principal (no sudo, headless CI, shared hosts)

For environments where sudo/Azure-CLI isn't an option. A service
principal is an Azure-AD identity with its own client ID + secret
that stands in for you. You create it **once** from any machine that
does have `az` (a laptop, a colleague's box), export three env vars,
and never need the CLI on the deploy host again.

```bash
# run on a host that already has `az login`
az ad sp create-for-rbac \
    --name sharktopus-deployer \
    --role Contributor \
    --scopes /subscriptions/<SUBSCRIPTION_ID>

# output (save the three values):
#   appId       → AZURE_CLIENT_ID
#   password    → AZURE_CLIENT_SECRET  (shown only once!)
#   tenant      → AZURE_TENANT_ID
```

Grant the SP permission to assign roles (provisioning grants blob
data access to the Container App's managed identity):

```bash
az role assignment create \
    --assignee <appId> \
    --role "User Access Administrator" \
    --scope /subscriptions/<SUBSCRIPTION_ID>
```

Then on the deploy host, export the three env vars:

```bash
export AZURE_CLIENT_ID=<appId>
export AZURE_CLIENT_SECRET=<password>
export AZURE_TENANT_ID=<tenant>
export AZURE_SUBSCRIPTION_ID=<SUBSCRIPTION_ID>
```

`DefaultAzureCredential` prefers env-var credentials when all three
are set, so `provision.py` uses them without any CLI interaction.

**End users of `pip install sharktopus` never need any Azure auth** —
the runtime client talks to the deployed Container App over plain
HTTPS. Auth is a deploy-time concern only.

## Step 2 — Install Python deploy deps

The `provision.py` script imports the management SDKs:

```bash
pip install \
    azure-identity \
    azure-mgmt-resource \
    azure-mgmt-storage \
    azure-mgmt-appcontainers \
    azure-mgmt-loganalytics \
    azure-mgmt-authorization
```

(The runtime client only needs `requests` + optionally
`azure-identity` + `azure-storage-blob` for SAS-mode large crops.)

## Step 3 — Run the deploy

```bash
python deploy/azure/provision.py \
    --subscription <SUBSCRIPTION_ID> \
    --location eastus2 \
    --resource-group sharktopus-rg
```

Resources created (everything name-prefixed `sharktopus`):

| Kind                     | Name                       | Notes                                           |
|--------------------------|----------------------------|-------------------------------------------------|
| Resource group           | `sharktopus-rg`            | overridable                                     |
| Storage account          | `sharktopus<hash>`         | StorageV2, LRS, TLS 1.2 min                     |
| Blob container           | `crops`                    | private, 7-day lifecycle on `crops/`            |
| Log Analytics workspace  | `sharktopus-logs`          | required by Container App Environment           |
| Container App Environment| `sharktopus-env`           | Consumption plan, no zone redundancy            |
| Container App            | `sharktopus-crop`          | 1 vCPU / 2 GiB, public ingress on 8080, scale 0..10 |
| Role assignment          | Storage Blob Data Contributor | from app's managed identity onto the storage account |

The script prints the public ingress URL on success:

```
Container App URL : https://sharktopus-crop.<env>.<region>.azurecontainerapps.io
```

Export it once for the runtime client (or let `azure_crop` discover it
via `ContainerAppsAPIClient`):

```bash
export SHARKTOPUS_AZURE_URL=https://sharktopus-crop.<env>.<region>.azurecontainerapps.io
```

## Step 4 — Smoke test

```python
from sharktopus.sources import azure_crop

p = azure_crop.fetch_step(
    "20260417", "00", 6,
    bbox=(-50, -40, -25, -20),
    variables=["TMP"], levels=["500 mb"],
)
print(p, p.stat().st_size, "bytes")
```

Expected: a small GRIB2 in `~/sharktopus_out/...` with a few hundred
bytes of TMP at 500 mb cropped to the bbox.

## Free-tier accounting

Container Apps' Consumption plan grants — per month, per
subscription — the same three dimensions as Cloud Run:

- **Requests**: 2,000,000
- **vCPU-seconds**: 180,000
- **GiB-seconds**: 360,000

`sharktopus.cloud.azure_quota` tracks these locally in
`~/.cache/sharktopus/quota.json`. Inspect:

```bash
sharktopus --quota azure
```

Past free tier: `azure_crop` raises `SourceUnavailable` and the
orchestrator falls back to `sharktopus.sources.azure` (full download
+ local crop, no Container Apps cost). To opt in to paid usage:

```bash
export SHARKTOPUS_ACCEPT_CHARGES=true
export SHARKTOPUS_MAX_SPEND_USD=2.00
```

## Tear-down

```bash
az group delete --name sharktopus-rg --yes
```

removes everything in one shot — Container App, environment, storage
account, log workspace, role assignment.

## Troubleshooting

- `403 Forbidden` from the Container App on first call — the managed
  identity's role assignment on the storage account hasn't propagated
  yet (~30 s). Retry.
- `RoleAssignmentRequestExceeded` — the role assignment GUID collided
  with a stale one. `az role assignment list --scope ...` and delete
  the duplicate, then rerun `provision.py`.
- `ContainerAppQuotaExceeded` — the subscription's per-region
  Container Apps quota is at zero. Request an increase via
  `az support tickets create` or pick a different region.
