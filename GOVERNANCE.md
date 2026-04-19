# Governance

sharktopus is a small, community-driven open-source project. Governance scales
with the size of the active contributor base — we don't want more process than
the project actually needs.

## Current stage: solo maintainer

At this stage the project has one active maintainer (see `AUTHORS.md`). All
PRs are reviewed and merged by the lead maintainer. Decisions are documented
in PR discussions, issues, or `CHANGELOG.md`.

## How decisions are made

1. **Trivial changes** (typos, docstring tweaks, obvious bug fixes with tests):
   one maintainer approves and merges.
2. **Non-trivial changes** (new source module, new public API, dependency
   changes, behavior changes): open an issue first to discuss the approach,
   then submit a PR referencing the issue.
3. **Breaking changes** (changing existing function signatures, removing
   features, renaming modules): require explicit maintainer approval and a
   migration note in `CHANGELOG.md`.

## Becoming a maintainer

We add maintainers when the project has people who are already reviewing and
contributing regularly. Criteria, informally:

- Multiple merged PRs that touched non-trivial code.
- Visible participation in issue triage or PR review.
- Willingness to uphold the code of conduct and the review standards here.

There is no vote or formal process yet — the lead maintainer invites, and the
invitee accepts in a PR that adds their name to `AUTHORS.md#maintainers`.
When the project has 3+ active maintainers, we will write a formal promotion
process into this file.

## Review standards

Maintainers reviewing PRs should:

- Label comments by intent: `blocker:` (must fix), `nit:` (stylistic),
  `question:` (asking), `future:` (follow-up idea).
- Approve only when CI is green, tests cover the change, and docs are in sync.
- Never merge your own PR when it changes public API — get a second maintainer
  to approve once we have more than one active.

## Long-term intent

The goal is for sharktopus to become community-owned in the same pattern as
NumPy, SciPy, xarray, and pandas: a project-owned GitHub organization where
the institutional origin (IEAPM) is acknowledged but not gatekeeping. As the
contributor base grows we will formalize:

- Voting on breaking changes.
- A technical steering committee.
- A conflict-resolution policy.

Until then, this document is the whole governance model.
