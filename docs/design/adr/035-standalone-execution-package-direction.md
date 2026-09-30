# ADR-035: Standalone Execution Component — Package and Directory Migration Direction

## Status

Accepted (2026-09-29; approves the package/direction target that the evolution plan's standalone-consumption path implements; implementation itself lands with the shared-assembly and standalone-distribution changes, not in this ADR)

## Context

The evolution plan requires that a business app can install only the execution components it needs and run them without the management platform, Studio, or the platform management database (I-Ca profile). Today the runtime kernel lives inside the main `hecate` package: there is no independently installable runtime wheel, no execution host, and platform-definition queries are entangled with the execution service (static closure in `docs/refactor/standalone-consumption-baseline.md`).

Three naming/boundary questions must be fixed before any directory moves or wheel builds, so that step3's contracts and step5's packaging work toward one approved target instead of ad-hoc layouts:

1. What do the new distribution units carry, and what must never be pulled in?
2. Which source dependency directions are fixed?
3. How do existing imports keep working during the migration?

## Decision

### Package layout (target)

- **`hecate-runtime`** — the execution kernel and Agent execution semantics: Pregel engine, compiler, workers, extension points, and the engine-facing contracts. Depends only on neutral contracts and its declared runtime needs; no platform ORM, no management-plane services.
- **`hecate-runner`** — the standalone execution host: local manifest loading, trusted configuration/secret-reference resolution, business identity and policy adapters, model/tool assembly, minimal execution/state/evidence interfaces, health and shutdown handling. Assembles `hecate-runtime`; never requires platform management tables to start.
- **Neutral contracts** — language-agnostic schemas/types published per capability (execution backend, later Memory/Knowledge/Evaluation/Observability/Gateway), independently versioned. A shared all-platform version is prohibited; a contract must be implementable without importing Python classes, the ORM, composition, or Pregel modules.

### Dependency direction (fixed)

Source dependencies: domain services → neutral contracts; adapters → neutral contracts and vendor SDKs; composition → application interfaces and concrete implementations. Runtime call flow: entry → application service → injected adapter → backend. Domain services and adapters each depend on neutral contracts; adapters may depend on vendor SDKs; domain services never import concrete adapters; `core/composition/` assembles. Platform adapters may call the host or the shared execution assembly to avoid duplicating execution flow — the platform never re-implements a second execution loop. The runtime never imports the host or the control plane.

### Compatibility and migration rules

- Old import paths keep working through thin compatibility shims that forward to the new packages; shims carry explicit lifetimes and are removed by the legacy-consolidation step once callers are migrated. No new code may target a shim.
- The standalone wheel must install cleanly from built artifacts: depending on the full `hecate` package, editable installs, or repository source paths does not count as standalone distribution. Optional capabilities that are not installed must declare `unsupported` at startup, never fail mid-run with an import error.
- The control plane remains a modular monolith; this ADR approves distribution units, not a microservice split. Directory moves happen with the packaging work, one slice at a time, each verified by the domain layering tests and the runtime self-sufficiency probe.

### Explicitly deferred

Actual package extraction, wheel builds, host implementation, and clean-install verification are implementation work under the evolution plan's standalone path (shared assembly → standalone distribution → local reliability/governance). This ADR fixes names and directions only; it grants no production support and pre-claims no conformance results.

## Consequences

- step3 contract artifacts and step5 packaging share one vocabulary; ADR-034's governance semantics apply unchanged to both in-process and out-of-process implementations.
- Existing `src/hecate/runtime/` imports remain valid during migration via shims; the layering tests gain package-boundary rules as slices land.
- Renaming later would break the approved direction; package names are stable once first published.
