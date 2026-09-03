"""Core self-sufficiency guard.

Verifies that ``hecate`` (the main package) can be imported and exercised
without ``hecate_enterprise`` (PR1.1) or the channel plugin packages
``hecate_channel_slack`` / ``hecate_channel_feishu`` (PR5b) being
importable — the "core-only install" invariant from the package-split
plan. If any main-package module structurally requires one of the
optional wheels, core-only deployments (self-hosted without them) break
at import time.

Companion to ``tests/test_engine/test_runtime_self_sufficiency.py`` (which
guards the runtime domain's independence from hecate.services). This file
guards the main package's independence from the optional wheels.

Why subprocess? An in-process monkeypatch of ``builtins.__import__`` cannot
catch imports that already happened during conftest / collection. A fresh
interpreter guarantees we start with the optional packages unimportable
(blocked via an import hook) and no cached module state.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Optional package import-prefixes that must not be required by core.
_BLOCKED_PREFIXES: tuple[str, ...] = (
    "hecate_enterprise",
    "hecate_channel_slack",
    "hecate_channel_feishu",
)

# Main-package modules that must remain importable without the optional
# wheels. Covers the wiring surface: the FastAPI app factory (routers
# mount lazily inside main), the auth/vault contract halves, and key
# infrastructure — including the two modules that lazy-import the channel
# plugin packages (registration fallback + webhook router).
CORE_MAIN_MODULES: tuple[str, ...] = (
    "hecate",
    "hecate.main",
    "hecate.enterprise.auth",
    "hecate.enterprise.auth.provider",
    "hecate.enterprise.auth.resolver",
    # hecate.enterprise.auth.registration removed in PR1.2 (replaced by entry_points).
    "hecate.enterprise.auth.api_key_provider",
    "hecate.enterprise.auth.jwt_provider",
    "hecate.enterprise.vault",
    "hecate.enterprise.vault.provider",
    "hecate.enterprise.vault.resolver",
    "hecate.core.config",
    "hecate.core.database",
    "hecate.core.deps",
    "hecate.core.deps_workspace",
    "hecate.api.management.budget",
    "hecate.channel.gateway.registration",
    "hecate.channel.api.v1.channels",
)

_PROBE_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    # Block the optional packages at the import machinery level for the
    # whole subprocess lifetime. Any attempt to import them raises
    # immediately.
    import importlib.abc

    _BLOCKED = __BLOCKED__

    class _OptionalPackageBlocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            for prefix in _BLOCKED:
                if fullname == prefix or fullname.startswith(prefix + "."):
                    raise ImportError(
                        "blocked by core-self-sufficiency guard: " + fullname
                    )
            return None

    sys.meta_path.insert(0, _OptionalPackageBlocker())
    for prefix in _BLOCKED:
        sys.modules.pop(prefix, None)

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
    """Spawn a Python subprocess that imports main modules with the optional wheels blocked."""
    script = _PROBE_SCRIPT.replace("__MODULES__", repr(list(CORE_MAIN_MODULES)))
    script = script.replace("__BLOCKED__", repr(list(_BLOCKED_PREFIXES)))
    proc = subprocess.run(  # noqa: S603 — sys.executable is trusted; no shell
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__") :])
    raise AssertionError(f"subprocess did not emit __RESULT__. stdout={proc.stdout!r} stderr={proc.stderr!r}")


def test_main_package_importable_without_optional_wheels() -> None:
    """Every core main-package module must import without the optional wheels.

    The core-only invariant: self-hosted installs without the
    hecate-enterprise and channel-plugin wheels must be able to build the
    FastAPI app (hecate.main) and use the base auth mechanisms.
    """
    result = _run_subprocess_probe()

    errors = result.get("errors", [])
    assert not errors, (
        "main-package modules failed to import without the optional wheels:\n"
        + "\n".join(f"  {e['module']}: {e['error']}" for e in errors)
        + "\n\nThese imports prove that src/hecate/ still structurally depends on"
        " an optional package — move the code back to core or lazy-mount it."
    )

    attempted = sorted(result["attempted"])
    imported = sorted(result["imported"])
    assert imported == attempted, f"some main-package modules were skipped: imported={imported} attempted={attempted}"
