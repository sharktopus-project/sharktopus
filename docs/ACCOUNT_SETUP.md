# Account setup — AWS, Google Cloud, Azure

**Audience:** you've just `pip install`ed sharktopus and want to deploy the
cropper to at least one cloud, but you don't have an account on that cloud yet
(or you do, but never created the right IAM principal).

This guide covers the **pre-authentication** part: sign-up, billing, and the
minimum IAM role so the `sharktopus --setup {provider}` wizard can do its job.
For the post-authentication technical deploy, the authoritative docs are:

- [DEPLOY_AWS.md](DEPLOY_AWS.md)
- [DEPLOY_GCLOUD.md](DEPLOY_GCLOUD.md)
- [DEPLOY_AZURE.md](DEPLOY_AZURE.md)

**You only need one cloud.** Sharktopus will happily run the cropper on any of
the three (or all three with request-splitting). AWS has the lowest per-request
latency at our payload sizes; GCloud has the highest free-tier quota; Azure
is the cheapest past the free tier.

---

## AWS

### 1. Create the account

Go to <https://portal.aws.amazon.com/billing/signup>.

- Email, password, account name (any string — you can rename later).
- Contact info (individual or company).
- **Credit or debit card** — AWS authorises a small amount (often a trial $1)
  to verify the card. Free-tier usage won't be charged until you exceed the
  quota, but the card on file is mandatory.
- Phone verification (SMS or voice).
- Pick the **Basic support plan** (free).

First login lands you on the **root user**. **Do not** use the root user to
deploy anything — it has unrestricted access and AWS will nag you to create
an admin IAM identity.

### 2. Enable billing alerts (recommended before anything else)

1. Top-right user menu → **Billing and Cost Management**.
2. Left sidebar → **Billing preferences** → enable "Receive Free Tier Usage
   Alerts" and add your email.
3. **Budgets** → *Create budget* → *Zero spend budget* template → your email.
   This emails you the moment any charge lands, even $0.01. Stops surprises.

### 3. Create the IAM identity sharktopus will use

You have two paths. Pick one.

#### Path 1 — IAM Identity Center (SSO), recommended for humans

IAM Identity Center replaces long-lived access keys with short-lived tokens
from a browser sign-in. Matches `sharktopus --setup aws` → Option A / B.

1. AWS Console → **IAM Identity Center** (may need to enable on first open).
2. **Enable** IAM Identity Center in your home region (e.g. `us-east-1`).
3. **Users** → *Add user* → yourself (email, first/last, username).
4. **Groups** → *Create group* (e.g. `sharktopus-deploy`). Add yourself.
5. **AWS accounts** → tick your account → *Assign users or groups* →
   pick the group → *Assign permission sets* → *Create permission set* →
   *Predefined* → **AdministratorAccess** (for the provisioning step;
   you can tighten later — see "Minimum permissions" below).
6. Copy the **SSO start URL** from the IAM Identity Center dashboard. It
   looks like `https://d-XXXXXXXXXX.awsapps.com/start`. You'll paste this
   into `sharktopus --setup aws`.

#### Path 2 — IAM user with long-lived access keys

Simpler but less secure. Matches `sharktopus --setup aws` → Option C.

1. AWS Console → **IAM** → **Users** → *Create user*.
2. Name (e.g. `sharktopus-deploy`). Leave *Console access* off.
3. *Attach policies directly* → **AdministratorAccess**.
4. On the user page → *Security credentials* → *Access keys* → *Create*
   → *Application running outside AWS* → acknowledge → *Create*.
5. **Copy both** the Access Key ID and the Secret Access Key. You can't see
   the secret again; if you lose it, delete the key and make a new one.

### 4. Minimum permissions (when you're ready to tighten)

`AdministratorAccess` is convenient for the first deploy. Once the Lambda is
running, you can swap the permission set / policy down to:

- `AWSLambda_FullAccess`
- `AmazonEC2ContainerRegistryFullAccess`
- `AmazonS3FullAccess`
- `IAMFullAccess` (needed to (re)create the Lambda execution role)

### 5. Verify

```bash
# Pure-Python SSO (Path 1, Option A)
sharktopus --setup aws
# → prompts for SSO start URL + region → browser opens → deploy proceeds

# With the aws CLI (Path 1, Option B — needs aws installed)
aws sts get-caller-identity
# → prints Account, UserId, Arn (no error = credentials are live)

# Static keys (Path 2)
AWS_ACCESS_KEY_ID=AKIA... AWS_SECRET_ACCESS_KEY=... \
  sharktopus --setup aws
```

### 6. Gotchas (learned the hard way)

<!-- TODO: Leandro — fill these from the two times you did this -->
- *(placeholder)* Region mismatch: the SSO region and the deploy region can
  differ. Keep them equal unless you know why not.
- *(placeholder)* Root user MFA: console nags forever until the root account
  has MFA. Easiest to just do it.
- *(placeholder)* Verification card: some Brazilian debit cards are rejected;
  a credit card (even zero limit) tends to work.

---

## Google Cloud (GCP)

### 1. Create the account

Go to <https://cloud.google.com/free>.

- Sign in with a Google account (personal gmail works; a workspace account
  is fine too).
- Agree to the free-trial terms: **$300 USD credit valid for 90 days**,
  card required but not charged during the trial.
- Country + card.

### 2. Create a billing account + project

GCP separates **billing accounts** (the wallet) from **projects** (the
namespace your resources live in). You need one of each.

1. Console top-left → **Billing** → *Manage billing accounts* → the default
   one created during sign-up should already be there.
2. Top-left project dropdown → *New Project*.
   - Name: `sharktopus-crop` (or anything).
   - Organization: `No organization` is fine for personal accounts.
   - Copy the **Project ID** (looks like `sharktopus-crop-123456`); you'll
     paste it into `sharktopus --setup gcloud`.
3. Link the project to your billing account: *Billing* → *Link a billing
   account* → pick it.

**Important:** free-tier usage still requires a billing-enabled project.
You're not charged until you exceed the quota, but the link must exist.

### 3. Choose the auth path

`sharktopus --setup gcloud` will offer three. You pick at the CLI; nothing
to configure in the console up front.

- **(a) Browser OAuth** — no `gcloud` CLI needed, no service-account key. You
  sign in at accounts.google.com, authorise the sharktopus app (the one we
  submit for Google verification), and the deploy runs with your user
  identity. Simplest for one-shot deploys.
- **(b) gcloud CLI** — install `gcloud`, run `gcloud auth login` and
  `gcloud auth application-default login`. You get a richer CLI and can
  inspect sessions with `gcloud auth list`.
- **(c) Service account JSON key** — download a key from the console,
  point `GOOGLE_APPLICATION_CREDENTIALS` at it. Best for CI runners.

### 4. Minimum roles

For path (a) and (b), your Google account needs one of:

- **Owner** on the project, **or**
- **Editor** on the project, **or**
- this set of roles:
  - `roles/run.admin`
  - `roles/storage.admin`
  - `roles/serviceusage.serviceUsageAdmin`
  - `roles/iam.serviceAccountUser`

For path (c), grant the above roles to the service account instead (IAM
& Admin → Service Accounts → select → *Permissions*).

### 5. Verify

```bash
# Path (a) — no CLI
sharktopus --setup gcloud
# → asks for project ID → browser opens → OAuth consent → deploy proceeds

# Path (b) — with gcloud CLI
gcloud auth list             # shows active account with asterisk
gcloud config get-value project   # prints the project ID
sharktopus --setup gcloud    # picks up the above automatically
```

### 6. Gotchas

<!-- TODO: Leandro — fill from your experience -->
- *(placeholder)* OAuth consent screen: today the sharktopus app is in
  **Testing** status with Google, meaning only accounts listed as test
  users can complete the flow. If the browser says "app not verified"
  and blocks you, either add yourself as a test user in the GCP console
  (project `sharktopus-oauth`) or fall back to path (b) or (c).
- *(placeholder)* `application-default login` vs `auth login`: both are
  needed for path (b). The first mints user credentials; the second mints
  ADC credentials that the Python SDK reads.
- *(placeholder)* Project ID vs Project Name: they're different. The ID
  (lowercase-with-dashes-and-numbers) is what the SDK wants.

---

## Azure

### 1. Create the account

Go to <https://azure.microsoft.com/free>.

- Sign in with a Microsoft account (personal Outlook/Hotmail works; a
  Microsoft work/school account works too).
- Identity verification: phone + card. The card is charged **€0.50** or
  similar as proof-of-life, then refunded.
- Agreement → complete. You land on the Azure portal.

Free benefits: **$200 USD credit for 30 days**, plus "always free"
allowances for a handful of services (Container Apps included — 180k
vCPU-seconds / month).

### 2. Get your subscription ID

Portal home → **Subscriptions** (or search for it). You should see one:
`Azure subscription 1` or similar. Copy the **Subscription ID**
(36-char UUID). `sharktopus --setup azure` prompts for this.

### 3. Choose the auth path

Three paths, picked at `sharktopus --setup azure`:

- **(a) Browser (azure-identity)** — no `az` CLI needed. Opens Microsoft's
  sign-in page in your browser, uses the public Azure CLI OAuth client
  to obtain a token.
- **(b) Azure CLI** — install `az`, run `az login`. Richer CLI.
- **(c) Service principal env vars** — set `AZURE_CLIENT_ID`,
  `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`. For CI.

### 4. Minimum roles

Your Microsoft account needs **Owner** or **Contributor** on the
subscription. The free account starts you as Owner by default, so the
typical user has nothing to do.

If you'll use Path (c), create the service principal:

1. Portal → **Microsoft Entra ID** (used to be "Azure AD").
2. **App registrations** → *New registration* → name (e.g.
   `sharktopus-deploy-sp`) → *Register*.
3. Copy the **Application (client) ID** and **Directory (tenant) ID**.
4. *Certificates & secrets* → *New client secret* → copy the *Value* (not
   the ID). Save it now — won't be shown again.
5. Subscription → *Access control (IAM)* → *Add role assignment* →
   **Contributor** → select your app registration → *Save*.

### 5. Verify

```bash
# Path (a) — no CLI
sharktopus --setup azure
# → asks for subscription ID → browser opens → deploy proceeds

# Path (b) — with az CLI
az account show              # prints subscription + tenant
sharktopus --setup azure     # picks up session

# Path (c)
AZURE_CLIENT_ID=... AZURE_CLIENT_SECRET=... AZURE_TENANT_ID=... \
  AZURE_SUBSCRIPTION_ID=... sharktopus --setup azure
```

### 6. Gotchas

<!-- TODO: Leandro — fill from your experience -->
- *(placeholder)* "No access to any subscription" after sign-up: sometimes
  the subscription takes 2-5 minutes to appear after creation. Wait, refresh.
- *(placeholder)* az CLI install on Ubuntu: the official one-liner requires
  sudo and touches `/usr/local`. No user-space tarball. Hence we recommend
  Path (a) or (c).
- *(placeholder)* Container Apps in some regions: if the region you picked
  doesn't have Container Apps GA yet, `provision.py` will fail with a
  resource-provider error. `eastus2` and `westeurope` are safe defaults.

---

## Where to go from here

Once `sharktopus --setup {provider}` completes successfully:

- The cropper endpoint is saved to `~/.cache/sharktopus/endpoints.json`.
- The corresponding `{provider}_crop` source becomes available — see
  `sharktopus --list-sources`.
- A job submitted via the WebUI or CLI with `--priority {provider}_crop`
  routes through your freshly-deployed endpoint.

If something goes wrong, the CLI wizard prints the exact command it ran and
the error from the underlying SDK. Most failures are IAM-related and point
back to sections 3-4 of the relevant cloud above.

Questions or gotchas we should add here? Open an issue at
<https://github.com/sharktopus-project/sharktopus/issues>.
