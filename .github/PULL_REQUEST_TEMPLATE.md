## Description

<!-- What does this PR do? Reference any issues it closes. -->

Closes #

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Refactoring (no functional changes)

## Changes

<!-- List the key changes in bullet points. -->

-

## Merging

<!-- Repository settings: "Allow merge commits" is OFF.
     Only "Rebase and merge" or "Squash and merge" are available. -->

- [ ] Merge via **Rebase and merge** (preferred — preserves per-commit history) or **Squash and merge** (acceptable — collapses a multi-commit branch into one commit on `main`).
- [ ] Do **not** use "Create a merge commit" — that button is disabled at the repo level.

## Checklist

- [ ] `ruff check src/hecate/ tests/` passes
- [ ] `ruff format --check src/ tests/` passes
- [ ] `mypy src/` passes
- [ ] `python -m pytest tests/ -q` passes
- [ ] Added/updated tests for new functionality
- [ ] Updated documentation if needed
