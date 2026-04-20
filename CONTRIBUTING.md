# Contributing to sharktopus

Thanks for your interest. This document explains how to contribute code,
documentation, or bug reports.

## Before you start

- Read `GOVERNANCE.md` to understand how decisions get made.
- Read `CODE_OF_CONDUCT.md` — it applies to every interaction on this repo.
- For non-trivial changes, open an issue first to discuss the approach.

## Development setup

```bash
git clone https://github.com/<org>/sharktopus.git
cd sharktopus
python -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'
pytest
```

The test suite should pass before you open a PR. Run it with:

```bash
PYTHONPATH=src python -m pytest
```

## Code style

- Python 3.10+. Type annotations are encouraged but not mandatory.
- No runtime dependency additions without discussion — sharktopus aims to stay
  installable in minimal scientific Python environments.
- Keep public API surface small. Prefer a new keyword argument to a new
  function when possible.
- Follow the patterns in existing source modules under `src/sharktopus/sources/`
  when adding a new mirror or cloud-side crop backend.

## Server-side changes (cloud-crop handlers)

Code under `deploy/<cloud>/` (Dockerfile, `main.py` / `handler.py`,
`requirements.txt`) runs inside AWS Lambda / GCloud Cloud Run / Azure
Container Apps. Changing anything there triggers a new container image
build on GHCR via `.github/workflows/build-image.yml`. See
[`docs/CONTRIBUTING_IMAGES.md`](docs/CONTRIBUTING_IMAGES.md) for:

- how the three-variant build matrix (`lambda` / `cloudrun` / `azure`) works,
- how to test the handler locally with `docker build` + `docker run`,
- how to verify a published tag on GHCR,
- what to do when adding a fourth cloud variant.

End-user client changes (under `src/sharktopus/`) don't need any of
that — you only care about the image pipeline if you touched a
`deploy/` directory.

## Writing tests

- Every code change needs at least one test. If the change is a bug fix, the
  test should fail before the fix and pass after.
- Use `monkeypatch` to avoid real network calls in unit tests.
- Integration tests that hit real cloud services must be gated behind an
  environment variable and excluded from the default `pytest` run.

## Commit messages

- First line ≤ 70 characters, imperative mood: "Add X" not "Added X".
- Reference issues in the body: `Closes #42`, `Refs #42`.
- Explain the **why**, not the **what** — the diff shows what changed.

## Pull requests

- One logical change per PR. Mixed refactors + features are harder to review.
- Fill out the PR template. Check that CI is green before requesting review.
- Be patient with review — this is a small project and maintainers are
  volunteers. If a PR has been open for more than two weeks without a
  response, ping it in the comments.

## Review comment conventions

When reviewing or being reviewed, comments are tagged by intent so authors
know what to address:

- `blocker:` must be fixed before merge.
- `nit:` stylistic; author may ignore.
- `question:` not a change request, just asking.
- `future:` follow-up idea, not for this PR.

## What happens after merge

- `CHANGELOG.md` gets updated in the same PR (or in a follow-up for trivial
  changes). Every public-facing change must appear in `Unreleased`.
- Your name is added to `AUTHORS.md#contributors` (first-time contributors).
- Releases are cut from `main` by maintainers; see the `Release` section in
  `GOVERNANCE.md` (TBD).

## Questions

Open a GitHub Discussion or file an issue labelled `question`.
