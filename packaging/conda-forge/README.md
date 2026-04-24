# conda-forge recipe — submission guide

This directory stages the conda-forge recipe for `sharktopus`. It's
**not** the feedstock itself; conda-forge feedstocks live in their
own repos under the `conda-forge/` GitHub org, created by the
`staged-recipes` flow.

Workflow to get sharktopus onto conda-forge:

1. Fork [`conda-forge/staged-recipes`](https://github.com/conda-forge/staged-recipes).
2. Create a branch `add-sharktopus` in your fork.
3. Copy this directory's `recipe/` into the fork at
   `recipes/sharktopus/`:
   ```
   cp -r packaging/conda-forge/recipe/. /path/to/staged-recipes/recipes/sharktopus/
   ```
4. Verify the recipe locally with
   [`conda-smithy`](https://github.com/conda-forge/conda-smithy) or
   [`rattler-build`](https://github.com/prefix-dev/rattler-build):
   ```
   mamba install -c conda-forge conda-build conda-smithy
   conda build recipes/sharktopus
   ```
   (Run this from inside the staged-recipes fork.)
5. Open a PR against `conda-forge/staged-recipes:main` titled
   `Add sharktopus`.
6. The conda-forge team will review, then create
   `conda-forge/sharktopus-feedstock` and grant maintainer access
   to the usernames listed in `extra.recipe-maintainers`.
7. Every future release follows this loop:
   - Tag + release on PyPI.
   - Bump `version` + `sha256` in `sharktopus-feedstock/recipe/meta.yaml`.
   - Open a PR; conda-forge-linter auto-merges if CI passes.

## Verifying the sdist sha256

The `meta.yaml` pins the sha256 of the PyPI sdist. Keep it in sync
with whatever is served by `https://pypi.io/packages/source/s/sharktopus/`:

```
curl -sSL https://pypi.io/packages/source/s/sharktopus/sharktopus-0.1.0.tar.gz \
  | sha256sum
```

Current pin (2026-04-23, v0.1.0):
`43e52b067032f6457b556d75d20c7b83cc206aa9c94f246649a72363db5077f5`

## Why this is a `noarch: python` recipe

The PyPI wheel ships a bundled `wgrib2` binary under
`sharktopus/_bin/`, so that users on a clean host don't have to
compile Fortran. The conda-forge recipe is different: conda-forge
already has a maintained [`wgrib2` package](https://anaconda.org/conda-forge/wgrib2),
so we depend on that and skip bundling. The sdist deliberately
excludes `_bin/wgrib2*` (see `pyproject.toml` →
`tool.hatch.build.targets.sdist.exclude`) so the conda-forge build
produces a pure-Python wheel + a `wgrib2` run dep.

`sharktopus.io.wgrib2` resolves in this order at runtime: explicit
argument → `$SHARKTOPUS_WGRIB2` env → bundled `_bin/wgrib2` →
`$PATH`. On conda-forge the fourth entry picks up the feedstock's
`wgrib2` — the bundled slot is empty.

## Optional extras

The base recipe does **not** pull in the `[ui]` or `[xarray]`
optional-dependency groups. Users install those explicitly:

```
mamba install -c conda-forge sharktopus \
    fastapi 'uvicorn-standard' jinja2 python-multipart    # for --ui
mamba install -c conda-forge sharktopus cfgrib xarray     # for xarray integration
```

A future revision may split these into sub-packages
(`sharktopus-ui`, `sharktopus-xarray`) via recipe outputs. Not done
in the initial submission to keep review easy.
