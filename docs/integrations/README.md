# Third-Party Integrations

Hecate's extension points allow third-party packages to plug into the platform
without forking the core. This section documents how to ship your own
integration — the kind that arrives as a PyPI wheel (`pip install
hecate-something-vendor`) and gets discovered at runtime via Python
[entry points](https://docs.python.org/3/library/importlib.metadata.html#entry-points).

## How selection works

Every integration surface ships with a default implementation registered by
Hecate itself. The active backend is selected through a single configuration
variable (one per surface); unset or misconfigured values **degrade to a no-op
rather than crash**, so a missing wheel never brings down the host process.

| Surface | Entry-point group | Selector env var | Default | Guide |
|---|---|---|---|---|
| Memory search backend | `hecate.memory_providers` | `HECATE_MEMORY_PROVIDER` | `builtin` | [memory/third-party-memory.md](memory/third-party-memory.md) |

> The selection mechanism is single-valued (one active backend per surface),
> unlike auth or vault which iterate every installed provider as a fallback
> chain. Memory is global — not per-request — so a misconfiguration is a
> process-wide setting, not a per-call policy.

## Conventions every integration must follow

These mirror the existing `auth`/`vault` extension points (see
[../how-to/develop-extensions.md](../how-to/develop-extensions.md)):

1. **Zero-arg factory** — `def provider() -> Backend:` reads its own settings and
   returns the integration instance, or `None` when unconfigured (the host
   treats `None` as "skip me").
2. **Duck-typed contract** — implement the method signatures Hecate expects; no
   base class or ABC inheritance required. See the per-surface guide for the
   exact protocol.
3. **Fail closed, not loud** — if your factory raises, Hecate logs and falls
   back to the default backend. Do not raise during normal startup.
4. **Package name**: `<surface>-<vendor>` (e.g. `hecate-memory-mem0`,
   `hecate-memory-zep`), with `hecate` as a runtime dependency so the
   resolver contract is importable.
5. **Document under `docs/integrations/<surface>/`** when your integration is
   upstreamed; in-repo vendors follow the same path inside their own package.

## See also

- [../how-to/develop-extensions.md](../how-to/develop-extensions.md) — engine
  extension points (ports and adapters pattern).
- [../concepts/plugins.md](../concepts/plugins.md) — the legacy
  filesystem-based plugin SDK (unrelated to entry-point discovery).