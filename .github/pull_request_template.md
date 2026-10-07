## Summary

<!-- Describe the user-visible or operational change. -->

## Verification

- [ ] `python -m ruff check --extend-ignore I001,UP017,UP035,UP045 .`
- [ ] `python -m pytest -q`
- [ ] `python -m compileall -q pipelines agents evals`
- [ ] No live provider calls were used, or they are described below.

## Review pipeline

- Connected analysis modes:
- Modes still marked `NOT_CONNECTED`:
- Human approval behavior and digest checked:
- Does this change alter source/evidence traceability?

## Live API use

<!-- State whether live API calls were made and any cost-bearing verification. -->
