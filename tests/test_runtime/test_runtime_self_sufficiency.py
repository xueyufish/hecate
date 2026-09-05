"""Runtime self-sufficiency guard.

Verifies that ``hecate.runtime`` (the future ``hecate-runtime`` wheel) can be
imported and exercised without the in-main-package optional domains being
importable. Runs in a subprocess with a sanitized ``sys.path`` and
``sys.modules`` so any sneaky lazy import would be caught.

Companion to ``tests/test_layering_domain.py`` (which is a static AST scan).
This file is the dynamic counterpart — it actually imports the runtime
modules in a fresh Python process and verifies each one succeeds without
triggering a forbidden import.

Why subprocess? An in-process monkeypatch of ``builtins.__import__`` cannot
catch imports that already happened during conftest / collection. A fresh
interpreter guarantees we start with no cached forbidden modules and no
conftest side-effects.

Blocked-prefix mechanics
------------------------

As of the ``phase-r-domain-reorg-followups`` change, the blocker list is
module-level (``_BLOCKED_PREFIXES`` below) rather than hardcoded. Each new
domain directory that lands under ``src/hecate/<domain>/`` (e.g.
``tools/`` from this same change, then ``enterprise/``, ``channel/``,
``studio/``, ``ops/`` from subsequent Phase R-complete PRs) gets one
prefix added here. The pre-existing ``packages/hecate-ops`` and
``packages/hecate-sandbox`` wheels are handled as separate entries
because they are real Python distributions with their own import roots
(``hecate_ops`` / ``hecate_sandbox``), not ``hecate.<something>`` prefixes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Core runtime modules that must remain importable without the listed
# domains / packages. Listed in roughly dependency order; conftest and
# other side-effecting modules are intentionally excluded (they require
# pytest fixtures).
CORE_RUNTIME_MODULES: tuple[str, ...] = (
    "hecate.runtime.types",
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
    "hecate.runtime.errors",
    # Port adapter + security assembly bridge files. These historically
    # escaped the probe (not on this allowlist) while carrying module-level
    # sibling-domain imports; they now lazy-import siblings — keep them on
    # the list so a regression resurfaces here, not only in the AST guard.
    "hecate.runtime.agent_execution_port",
    "hecate.runtime.security.egress",
    "hecate.runtime.security.hooks.output_security",
)

# Optional in-main-package domains whose import is forbidden in the runtime
# subprocess. Phase R-complete adds one prefix per new domain directory.
# Tools is added by the tools/ domain-filling PR; enterprise, channel,
# studio, ops by their respective domain PRs.
BLOCKED_IN_MAIN_DOMAINS: tuple[str, ...] = (
    "hecate.tools",
    "hecate.enterprise",
    "hecate.channel",
    "hecate.studio",
    "hecate.ops",
)

# Workspace wheel packages whose import is also forbidden at runtime
# (extracted in PR3b / PR4c / PR4b — they are not part of the future
# ``hecate-runtime`` wheel).
BLOCKED_WHEELS: tuple[str, ...] = (
    "hecate_ops",
    "hecate_sandbox",
    "hecate_memory",
    "hecate_enterprise",
    "hecate_llm",
    "hecate_channel_slack",
    "hecate_channel_feishu",
)

ALL_BLOCKED_PREFIXES: tuple[str, ...] = BLOCKED_IN_MAIN_DOMAINS + BLOCKED_WHEELS

_PROBE_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    # Strip any cached forbidden modules before testing. This matters
    # even though the subprocess is fresh: conftest.py or test files at
    # module level might have pulled those modules in.
    _BLOCKED = __BLOCKED__
    for k in list(sys.modules):
        for prefix in _BLOCKED:
            if k == prefix or k.startswith(prefix + "."):
                del sys.modules[k]
                break

    # Now patch __import__ so any attempt to pull a forbidden module
    # fails loudly.
    import builtins
    _real = builtins.__import__

    def _gate(name, *a, **kw):
        for prefix in _BLOCKED:
            if name == prefix or name.startswith(prefix + "."):
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
    """Spawn a Python subprocess that imports runtime modules with all forbidden prefixes blocked."""
    script = _PROBE_SCRIPT.replace("__MODULES__", repr(list(CORE_RUNTIME_MODULES)))
    script = script.replace("__BLOCKED__", repr(list(ALL_BLOCKED_PREFIXES)))
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


def test_runtime_modules_importable_without_blocked_domains() -> None:
    """Every core runtime module must import without pulling blocked domains.

    The runtime domain is designed to be future-extractable as the
    ``hecate-runtime`` wheel. If any runtime module transitively requires
    a forbidden prefix (``hecate.services.*`` or any of the optional
    workspace wheels) at import time, the wheel cannot be built standalone.

    Forbidden prefixes are listed in ``ALL_BLOCKED_PREFIXES`` above; the
    list grows as Phase R-complete adds new in-main-package domain
    directories (``enterprise``, ``channel``, ``studio``, ``ops``).
    """
    result = _run_subprocess_probe()

    errors = result.get("errors", [])
    assert not errors, (
        "runtime modules failed to import without the blocked prefixes:\n"
        + "\n".join(f"  {e['module']}: {e['error']}" for e in errors)
        + "\n\nThese imports prove that runtime/ still depends on a blocked "
        "domain. Move the helper into runtime/ or extend RuntimePort."
    )

    attempted = sorted(result["attempted"])
    imported = sorted(result["imported"])
    assert imported == attempted, f"some runtime modules were skipped: imported={imported} attempted={attempted}"
