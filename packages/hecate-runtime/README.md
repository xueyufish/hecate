# hecate-runtime

Hecate runtime kernel — the Pregel execution engine, its workers, and the
shared execution assembly (`assemble_execution`), packaged for standalone
consumption (evolution plan step5b).

This package is the one deliberate exception to the workspace convention:
every other `packages/*` wheel depends on the full `hecate` application,
while **`hecate-runtime` must never depend on `hecate`**. The dependency
direction is one-way: the platform (`hecate`) depends on this kernel; the
kernel depends only on its own code, its declared dependencies, and the
optional extras below.

## Install

```bash
uv add hecate-runtime          # from the workspace
# or, from a built wheel in a clean environment:
pip install hecate_runtime-*.whl
```

## Optional extras

| Extra | Enables | Without it |
|---|---|---|
| `memory` | `hecate_memory.consolidation` backends for compaction/context processors | capability declared `unsupported` at assembly time |
| `sandbox` | `hecate_sandbox` offload execution environments | capability declared `unsupported` |
| `security` | `llm_guard` scanners for the security hooks | hooks degrade to pass-through per their DI contract |
| `otel` | OpenTelemetry instrumentation | telemetry no-ops |

The A2A handoff transport (`agent_tool`) has no extra: the implementation
lives in the platform's `channel/` domain, so a standalone kernel declares
the capability `unsupported` unless the host injects a transport.

## Configuration

The kernel never reads platform settings. `assemble_execution(...,
config=RuntimeConfig(...))` receives a `hecate_runtime.config.RuntimeConfig`
built by the caller (platform composition root or standalone host); defaults
allow a bare assembly for development.

## Compatibility

`hecate.runtime.*` inside the main application is a thin forwarding shim
over this package (step5b). The shim is slated for removal in step19; new
code should import `hecate_runtime` directly.
