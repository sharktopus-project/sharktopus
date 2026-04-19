---
name: Bug report
about: Something sharktopus did wrong or unexpectedly
title: "[bug] "
labels: bug
assignees: ''
---

## What happened

<!-- Short description of the unexpected behavior. -->

## What you expected

<!-- What you thought would happen. -->

## Reproduction

```python
# Minimal code that reproduces the issue.
import sharktopus
# ...
```

If the bug is environment-specific, include:

- OS / distro:
- Python version (`python --version`):
- sharktopus version (`python -c "import sharktopus; print(sharktopus.__version__)"`):
- wgrib2 version (`wgrib2 -version | head -1`):
- Which source was in use (`nomads`, `aws`, `aws_crop`, `gcloud`, `azure`, `rda`):

## Logs / tracebacks

<details>
<summary>Full traceback</summary>

```
paste here
```

</details>

## Anything else

<!-- Workarounds you tried, related issues, etc. -->
