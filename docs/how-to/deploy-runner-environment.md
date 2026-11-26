# Install the Runner from a locked workspace

Use Python 3.12+, uv, and a checkout at the intended revision, including
its lockfile. Synchronize the Runner into a dedicated environment.

```bash
UV_PROJECT_ENVIRONMENT=/path/runner-venv uv sync --locked --package hecate-runner --no-dev --no-editable
```

For an offline target, provision Python and uv in advance. Prepare a uv
cache on a connected machine with the same Python version, operating
system, and architecture. Use the same checkout revision and sync command,
with `UV_CACHE_DIR` pointing at the cache you will transfer. On the
target, use the checkout and prepared cache with a fresh environment.

```bash
UV_CACHE_DIR=/path/runner-cache UV_PROJECT_ENVIRONMENT=/path/runner-venv uv sync --locked --package hecate-runner --no-dev --no-editable --offline
/path/runner-venv/bin/hecate-runner --help
```

uv fails if a requirement is missing from the cache. Provision any model
and business service separately. See [Install and start signed Runner
artifacts](install-runner-releases.md) for artifact selection.
