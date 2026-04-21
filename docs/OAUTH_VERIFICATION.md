# Google OAuth verification — sharktopus

Runbook for moving the `sharktopus-oauth` Google Cloud project's OAuth
consent screen from **Testing** to **In production** with the sensitive
scope `https://www.googleapis.com/auth/cloud-platform` verified.

Maintained on behalf of a browser-side operator (human or Claude)
driving `console.cloud.google.com` while the CLI side has nothing more
to do until Google comes back with questions.

---

## Current state (as of 2026-04-21)

| Item | Status |
|---|---|
| GCP project `sharktopus-oauth` | created |
| OAuth consent screen — App name `sharktopus` | set |
| User type | **External** |
| Publishing status | **Testing** (2 test users: `leandrometeoro@gmail.com`, `sharktopus.convect@gmail.com`) |
| OAuth client ID — Desktop, `sharktopus-cli` | created, client JSON downloaded |
| Sensitive scope requested | `https://www.googleapis.com/auth/cloud-platform` |
| App logo | uploaded (`site/assets/avatar.png`, 400×400) |
| Home / Privacy / ToS URLs | `https://sharktopus.leandrometeoro.com.br/{,privacy,terms}` — all 200 OK |
| Authorized domain | `leandrometeoro.com.br` — **not yet verified in Search Console** |
| Developer contact email | `leandrometeoro@gmail.com` |

In Testing mode the CLI works today for the two test users, with tokens
that expire every 7 days. Full verification is needed to drop that
7-day refresh and to remove the yellow "unverified app" consent banner
for anyone outside the test list.

---

## Blockers to resolve before clicking "Publish App"

### 1. Verify `leandrometeoro.com.br` in Google Search Console

Google will not accept an app whose authorized domain is unverified.
The TXT-record method is preferred — leaves no file in the site repo
and survives site redeploys.

1. Open <https://search.google.com/search-console/welcome> signed in as
   the account that owns the `sharktopus-oauth` project
   (`sharktopus.convect@gmail.com`).
2. Pick **Domain** (left option — covers the apex + all subdomains).
3. Enter `leandrometeoro.com.br`. Search Console shows a DNS TXT record
   starting with `google-site-verification=...` — copy it.
4. In Cloudflare → `leandrometeoro.com.br` zone → DNS → **Add record**:
   - Type: `TXT`
   - Name: `@` (apex)
   - Content: `google-site-verification=<the long string>`
   - TTL: Auto
5. Back in Search Console, click **Verify**. Propagation is typically
   < 1 minute with Cloudflare; retry after 30 s if it fails the first
   time.
6. Once verified, the domain is selectable in the OAuth consent screen's
   *Authorized domains* list.

### 2. Record the demo video

Google wants to see the OAuth flow and the scope actually being used.
Unlisted YouTube is fine. Suggested script (target ~2 min):

1. Open a clean terminal. Say on-camera: "sharktopus is an open-source
   Python library that deploys a tiny GRIB2 cropper to the user's own
   Google Cloud project. The only scope it asks for is
   `cloud-platform`, and it uses that scope only to provision
   infrastructure in the user's own account."
2. `pip install sharktopus` (or show it's already installed).
3. `python3 deploy/gcloud/provision.py --auth browser --project <demo-project> --authenticated-only`
4. Browser opens → sign in → consent screen shows `sharktopus` with
   the logo and the `cloud-platform` scope → click Allow.
5. Script prints the four APIs it enables, the bucket it creates, the
   Artifact Registry remote repo, and the Cloud Run service URL.
6. `curl -sS -H "Authorization: Bearer $(gcloud auth print-identity-token)" $URL/` →
   `{"status":"ok"}`.
7. Show that `~/.cache/sharktopus/gcloud_token.json` is `0600` and
   the token is scoped — `cat` the file (mask the refresh token in
   post if you want, not required).
8. Close with: "All data stays in the user's project. sharktopus
   never receives user data on any server we control."

Upload to YouTube as **Unlisted**, keep the URL — the submission form
asks for it.

### 3. Have the scope justification text ready to paste

See [Scope justification](#scope-justification-paste-as-is) below.

---

## Hand-off to the browser-side operator

Once the two blockers above are done, the rest is point-and-click.
The operator (human or parallel Claude running in the user's browser)
should follow these exact steps on
<https://console.cloud.google.com> signed in as
`sharktopus.convect@gmail.com` with the `sharktopus-oauth` project
selected.

### Step A — Confirm App information is complete

Navigate: **APIs & Services** → **OAuth consent screen** (or **Google
Auth Platform** → **Branding** in the newer UI).

Check every field matches this table; fix anything that is empty or
outdated:

| Field | Value |
|---|---|
| App name | `sharktopus` |
| User support email | `sharktopus.convect@gmail.com` |
| App logo | `site/assets/avatar.png` (upload if not already present) |
| Application home page | `https://sharktopus.leandrometeoro.com.br/` |
| Application privacy policy link | `https://sharktopus.leandrometeoro.com.br/privacy` |
| Application terms of service link | `https://sharktopus.leandrometeoro.com.br/terms` |
| Authorized domains | `leandrometeoro.com.br` |
| Developer contact information | `leandrometeoro@gmail.com` |

Clean URLs (no `.html`) are what Cloudflare Pages serves — both forms
resolve, but Google's URL check prefers the clean form.

### Step B — Click "Publish App"

Same page, at the top under **Publishing status: Testing**, click
**Publish App**. A modal confirms the move to **In production**. Confirm.

The status changes to **In production — Needs verification** because
the `cloud-platform` scope is sensitive.

### Step C — Start the verification form

Google now shows a **Prepare for verification** or **Submit for
verification** button. Click it. The form has four sections:

**1. App information — already filled from the consent screen.** Confirm
nothing is missing.

**2. Scope justification.** For `cloud-platform`, paste the text from
[Scope justification](#scope-justification-paste-as-is) below. Attach
the YouTube demo URL in the field labeled *YouTube video link* or
*Provide a link to a demo video*.

**3. Data usage & storage.** Confirm:
- Does your app transfer user data to a third party? **No.**
- Does your app store user data on your servers? **No — the app runs
  entirely on the user's machine and inside the user's own GCP
  project. The maintainers operate no servers that receive user
  data.**
- Does your app share user data? **No.**

**4. Submit.** Google's confirmation screen shows the review ticket
number and an estimated timeline (usually 4–6 weeks for sensitive
scopes on an indie project; faster if the reviewer has no follow-up
questions).

### Step D — Save the ticket number

Google sends a confirmation email to `sharktopus.convect@gmail.com`.
Forward it to `leandrometeoro@gmail.com` so both inboxes have the
thread. Record the ticket ID in `COMMUNICATION.md` under the
verification section.

---

## Scope justification — paste as-is

> **Scope:** `https://www.googleapis.com/auth/cloud-platform`
>
> **What the app does:** sharktopus is an open-source Python library
> (MIT-licensed, source at <https://github.com/sharktopus-project/sharktopus>)
> that crops Global Forecast System (GFS) GRIB2 files in the cloud
> before downloading them. It deploys a small serverless container
> running `wgrib2` to the **end user's own Google Cloud project** —
> the maintainers never receive or proxy user data.
>
> **Why cloud-platform specifically:** the deploy flow needs to (a)
> enable the Cloud Run, Cloud Storage, Artifact Registry, and IAM
> Credentials APIs; (b) create a Cloud Storage bucket in the user's
> project; (c) create an Artifact Registry remote repository that
> proxies `ghcr.io` (Cloud Run does not accept GHCR image URLs
> directly); (d) deploy a single Cloud Run service; and (e) optionally
> grant `roles/run.invoker` to a service account so subsequent calls
> can be authenticated with an ID token rather than an unauthenticated
> public URL. Each of these APIs sits under `cloud-platform` in
> Google's OAuth scope taxonomy — Google does not currently expose
> finer-grained OAuth scopes (such as `run.admin` or
> `artifactregistry.admin`) for installed apps, so `cloud-platform`
> is the narrowest scope that covers the deploy path.
>
> **What the app does NOT do with the scope:** no Gmail, Drive,
> Calendar, Contacts, BigQuery, Compute Engine, or any other API
> outside the four listed above are called. No user data is read,
> written, or transmitted to any server outside the user's own
> project. The refresh token is stored locally at
> `~/.cache/sharktopus/gcloud_token.json` with mode `0600` and never
> leaves the user's machine.
>
> **Provenance:** sharktopus was originally developed to support the
> CONVECT research project ("Convective Systems Forecasting:
> Integrated Analysis of Numerical Modeling, Radar and Satellites",
> CNPq Extreme Events Call 15/2023), executed at the Brazilian Navy
> oceanographic institute IEAPM in partnership with UENF and UFPR.
> It is maintained as an independent open-source project; see
> `GOVERNANCE.md` and `AUTHORS.md` in the repository.

---

## After submission

- **Status page:** `console.cloud.google.com` → APIs & Services →
  OAuth consent screen shows *Verification in progress* until a
  reviewer responds.
- **Expected reviewer contact:** 1–6 weeks. Reviewers often ask for
  clarification (video quality, scope narrowing, privacy policy
  wording). Reply on the thread from
  `sharktopus.convect@gmail.com` within 2 weeks or the submission is
  auto-closed.
- **Until verified, the Testing-mode flow keeps working** for the two
  test users on the allowlist. There is no outage during verification.
- **When verification succeeds:** the yellow "Google hasn't verified
  this app" banner disappears, the 7-day refresh-token cap is lifted,
  and the app is usable by anyone signing in with any Google account.

## Troubleshooting

**"Your app can't be verified because the authorized domain isn't
verified."** → You skipped step 1 (Search Console). Go back and add
the TXT record.

**"We couldn't verify your video."** → Must be on YouTube, public or
unlisted (not private), and must audibly or visibly show the scope
consent happening. Re-record with on-screen narration if needed.

**"We need you to narrow your scope."** → This is a common reviewer
ask. Reply on the thread with the justification above verbatim,
emphasising that (a) no finer-grained OAuth scope exists for
installed-app deploys and (b) the refresh token never leaves the
user's machine. Apps like `gcloud` itself use the same scope for the
same reason; cite that precedent if needed.

**"Please confirm sharktopus is not a product of IEAPM/CNPq/etc."** →
Point the reviewer at `site/index.html`'s "Who maintains it?"
section, `site/terms.html` §7 "No institutional endorsement", and
`GOVERNANCE.md`. All three state explicitly that the project is
independent.
