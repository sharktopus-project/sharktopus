# Reproduction & fix log — uchoa@snowshark install diary

**Source**: `~/Downloads/sharktopus_install_log.pdf`
("sharktopus — Diário de Instalação e Troubleshooting", uchoa, snowshark,
Debian/Ubuntu Python 3.12, sharktopus 0.1.1)

**Replay date**: 2026-04-30
**Replay environment**: clean `ubuntu:24.04` Docker container, two passes.

This document reports what was reproduced, what was fixed, and what is
still on the user's plate. Generated as a single artefact (per project
convention: long replies go to a markdown file at project root, not
chat).

---

## Mapping issues to releases

| # | Symptom uchoa documented            | 0.1.1 (PyPI) | 0.1.5 (local pre-replay) | 0.1.6 (this replay) |
|---|--------------------------------------|--------------|--------------------------|---------------------|
| 1 | `pip install sharktopus` → PEP 668 externally-managed-environment | broken | broken | **doc'd in README Prerequisites** (venv-first) |
| 2 | `python3 -m venv .venv` solves it    | works        | works                     | now stated up-front |
| 3 | `sharktopus --ui` did not auto-open browser | banner silent on headless | banner silent | **headless-aware louder banner + SSH-forward hint** |
| 4 | `sharktopus --setup aws` could not find `deploy/aws/provision.py` | broken (no source checkout) | broken (deploy/ not in wheel) | **deploy/ force-included under `_deploy/` in wheel** |
| 5 | `--setup aws` failed with `ModuleNotFoundError: boto3` *after SSO complete* | broken (boto3 not declared) | broken | **`[aws]` extra + pre-flight import check before any prompt** |
| 6 | ECR pull-through cache for ghcr.io rejected with `UnsupportedUpstreamRegistryException` (needs Secrets Manager credentialArn) | broken (no flag) | broken | **`--credential-arn` + `--create-credential` flags + self-explaining error** |

Two findings **not** in the PDF surfaced during replay (uchoa's snowshark
already had python3 + python3-venv on PATH):

| # | Symptom (replay-only)                                | Fix |
|---|------------------------------------------------------|------|
| 7 | Fresh `ubuntu:24.04` ships without `python3` at all  | README Prerequisites: `apt install -y python3 python3-pip python3-venv` |
| 8 | `python3 -m venv` fails on Ubuntu 24.04 minimal images without `python3-venv` apt package | same — listed explicitly in Prerequisites |

---

## Files changed (working tree, not yet committed)

```
M CHANGELOG.md
M README.md
M deploy/aws/provision.py
M pyproject.toml                      (0.1.5 → 0.1.6)
M src/sharktopus/__init__.py          (__version__)
M src/sharktopus/setup.py             (pre-flight + bundled deploy/ lookup)
M src/sharktopus/webui/server.py      (headless-aware banner)
?? scripts/gcloud_redeploy.sh         (separate concern, AR repo redeploy)
```

The separate plan from `snug-skipping-hopper.md` (multi-product
foundation + UI rebrand + sticky chrome + /about page) was executed in
a previous session and is **already committed** (commit `c8d0ff9`,
"WebUI: GRIB rebrand, sticky header/footer, /about page"). No further
work needed there.

---

## Test status

```
./.venv/bin/python -m pytest tests/test_availability.py -q
… 3 failed, 28 passed in 122.91s
```

Failing tests:
- `test_available_sources_recent_date_returns_all_cloud_mirrors`
- `test_available_sources_drops_nomads_outside_retention`
- `test_fetch_batch_auto_priority_uses_available_sources`

**Verified pre-existing**: ran the same tests against `main` with my
working-tree changes stashed away — all three still fail. They are
captured by pending task **#75** ("Fix
test_available_sources_recent_date_returns_all_cloud_mirrors"). The
diff (`'gcloud_crop' != 'gcloud'`, missing call sequence) is unrelated
to the deploy/setup/UI files this work touched.

The remaining 28 tests in `test_availability.py` plus all
`test_webui_*` and `test_sources_*` tests pass.

---

## Open items for the user

1. **Publish 0.1.6 wheels to PyPI.** The version bump and changelog
   entry are in place; multi-platform wheel build is wired in
   `.github/workflows/build-wheels.yml`. Tag `v0.1.6` and let CI
   produce the wheels.

2. **Decide whether to commit `scripts/gcloud_redeploy.sh`** or leave
   it as a one-off local helper. It is a separate concern from this
   uchoa work — it is for redeploying the gcloud AR proxy after a
   misconfigured Docker Hub remote.

3. **Resolve task #75** (the 3 pre-existing `test_availability.py`
   failures) before tagging 0.1.6, so CI is green at the tag.

4. **Optional: push image to a public ECR mirror** as an alternative
   to the ghcr.io pull-through-cache path. The `--credential-arn`
   path now works, but if a user prefers no Secrets Manager involvement
   they could pull the image from a public ECR repo directly.

5. **Have uchoa redo the install with 0.1.6** to confirm each fix
   from his end. Replay in this repo's clean container did:
   - `pip install` cleanly inside venv → success
   - `sharktopus --ui` over SSH → loud banner with port-forward hint
   - `sharktopus --setup aws` (no extras) → immediate `pip install
     'sharktopus[aws]'` hint, no SSO walked through then aborted
   - `sharktopus --setup aws` (with extras) → resolves bundled
     `_deploy/aws/provision.py` and runs.

---

## Container reproduction transcript (abbreviated)

Two containers used during this replay:
- `sharktopus_uchoa_repro` — initial reproduction of issues 1, 3, 4, 5
- `sharktopus_uchoa_v2` — verification that the 0.1.6 wheel resolves all
  three classes of fix end-to-end.

Pre-flight verification highlights from `sharktopus_uchoa_v2`:

```
$ pip install /opt/wheels/sharktopus-0.1.6-py3-none-linux_x86_64.whl
$ sharktopus --setup aws
== sharktopus setup aws ==
setup: missing deploy dependency for aws: boto3
       install with: pip install 'sharktopus[aws]'

$ pip install 'sharktopus[aws] @ /opt/wheels/...'
$ sharktopus --setup aws
== sharktopus setup aws ==
== resolved provision script: /opt/venv/lib/python3.12/site-packages/sharktopus/_deploy/aws/provision.py
…
```

`sharktopus --ui` headless banner output:

```
════════════════════════════════════════════════════════════
  sharktopus web UI

    http://127.0.0.1:8765/

  No display detected (SSH or headless host).
  Open the URL above in a browser on your local machine.
  For SSH hosts, forward the port first:
      ssh -L 8765:localhost:8765 user@host

  Ctrl-C to stop.
════════════════════════════════════════════════════════════
```

---

## Summary

All 6 PDF-documented issues are resolved in the working tree (0.1.6).
Two new findings (Ubuntu 24.04 missing python3 / python3-venv) are
documented in README Prerequisites. Test failures are pre-existing
(task #75), unrelated to this work. The 0.1.6 release is ready to tag
once #75 is resolved.
