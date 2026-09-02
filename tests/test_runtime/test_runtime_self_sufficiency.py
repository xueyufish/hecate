"""Runtime self-sufficiency guard.

Verifies that ``hecate.runtime`` (the future ``hecate-runtime`` wheel) can be
imported and exercised without ``hecate.services.*`` being importable. Runs
in a subprocess with a sanitized ``sys.path`` and ``sys.modules`` so any
sneaky lazy import would be caught.

Companion to ``tests/test_engine/test_layering.py`` (which is a static AST
scan). This file is the dynamic counterpart — it actually imports the
engine modules in a fresh Python process and verifies each one succeeds
without triggering a services import.

Why subprocess? An in-process monkeypatch of ``builtins.__import__`` cannot
catch imports that already happened during conftest / collection. A fresh
interpreter guarantees we start with no cached ``hecate.services.*``
modules and no conftest side-effects.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Core engine modules that must remain importable without services/.
# Listed in roughly dependency order; conftest and other side-effecting
# modules are intentionally excluded (they require pytest fixtures).
CORE_ENGINE_MODULES: tuple[str, ...] = (
    "hecate.runtime.types",
    "hecate.runtime.errors",
    "hecate.runtime.ports",
    "hecate.runtime.eventstore",
    "hecate.runtime.context",
    "hecate.runtime.dynamic_types",
    "hecate.runtime.channel",
    "hecate.runtime.tool_access",
    "hecate.runtime.tool_gate",
    "hecate.runtime.tool_matcher",
    "hecate.runtime.guardrail",
    "hecate.runtime.middleware",
    "hecate.runtime.retry",
    "hecate.runtime.environment_volumes",
    "hecate.runtime.handoff",
    "hecate.runtime.worker",
    "hecate.runtime.scheduler",
    "hecate.runtime.eviction",
    "hecate.runtime.optimization",
    "hecate.runtime.monotonic_denials",
    "hecate.runtime.routing",
    "hecate.runtime.task_allocator",
    "hecate.runtime.command",
    "hecate.runtime.compiler",
    "hecate.runtime.pregel",
)

_PROBE_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    # Strip any cached hecate.services.* / hecate_ops.* / hecate_sandbox.*
    # before testing. This matters even though the subprocess is fresh:
    # conftest.py or test files at module level might have pulled those
    # modules in.
    for k in list(sys.modules):
        if k == "hecate.services" or k.startswith("hecate.services."):
            del sys.modules[k]
        if k == "hecate_ops" or k.startswith("hecate_ops."):
            del sys.modules[k]
        if k == "hecate_sandbox" or k.startswith("hecate_sandbox."):
            del sys.modules[k]

    # Now patch __import__ so any attempt to pull services / the extracted
    # ops package / the extracted sandbox package fails loudly.
    import builtins
    _real = builtins.__import__

    def _gate(name, *a, **kw):
        if name == "hecate.services" or name.startswith("hecate.services."):
            raise ImportError(
                "blocked by runtime-self-sufficiency guard: " + name
            )
        if name == "hecate_ops" or name.startswith("hecate_ops."):
            raise ImportError(
                "blocked by runtime-self-sufficiency guard: " + name
            )
        if name == "hecate_sandbox" or name.startswith("hecate_sandbox."):
            raise ImportError(
                "blocked by runtime-self-sufficiency guard: " + name
            )
        return _real(name, *a, **kw)

    builtins.__import__ = _gate

    result = {"imported": [], "attempted": [], "errors": []}
    for m in __MODULES__:
        result["attempted"].append(m)
        try:
            __import__(m)
            result["imported"].append(m)
        except Exception as e:
            result["errors"].append({"module": m, "error": type(e).__name__ + ": " + str(e)})
    sys.stdout.write("__RESULT__" + json.dumps(result))
    sys.stdout.flush()
    """
)


def _run_subprocess_probe() -> dict[str, object]:
    """Spawn a Python subprocess that imports engine modules with services blocked."""
    script = _PROBE_SCRIPT.replace("__MODULES__", repr(list(CORE_ENGINE_MODULES)))
    proc = subprocess.run(  # noqa: S603 — sys.executable is trusted; no shell
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = proc.stdout
    for line in stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__") :])
    raise AssertionError(f"subprocess did not emit __RESULT__. stdout={stdout!r} stderr={proc.stderr!r}")


def test_engine_modules_importable_without_services() -> None:
    """Every core engine module must import without pulling in services/.

    Future ``hecate-runtime`` wheel extraction depends on this: if any
    engine module transitively requires ``hecate.services.*`` at import
    time, the wheel cannot be built standalone.
    """
    result = _run_subprocess_probe()

    errors = result.get("errors", [])
    assert not errors, (
        "engine modules failed to import without services/:\n"
        + "\n".join(f"  {e['module']}: {e['error']}" for e in errors)
        + "\n\nThese imports prove that engine/ still depends on services/. "
        "Move the helper into engine/ or extend RuntimePort."
    )

    attempted = sorted(result["attempted"])
    imported = sorted(result["imported"])
    assert imported == attempted, f"some engine modules were skipped: imported={imported} attempted={attempted}"
