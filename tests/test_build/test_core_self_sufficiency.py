"""Core self-sufficiency guard.

Verifies that ``hecate`` (the main package) can be imported and exercised
without ``hecate_enterprise`` being importable — the "core-only install"
invariant from the package-split plan (PR1.1). If any main-package module
structurally requires ``hecate_enterprise``, core-only deployments
(self-hosted without the enterprise wheel) break at import time.

Companion to ``tests/test_engine/test_runtime_self_sufficiency.py`` (which
guards the runtime domain's independence from hecate.services). This file
guards the main package's independence from hecate_enterprise.

Why subprocess? An in-process monkeypatch of ``builtins.__import__`` cannot
catch imports that already happened during conftest / collection. A fresh
interpreter guarantees we start with ``hecate_enterprise`` unimportable
(blocked via an import hook) and no cached module state.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Main-package modules that must remain importable without hecate_enterprise.
# Covers the wiring surface: the FastAPI app factory (routers mount lazily
# inside main), the auth/vault contract halves, and key infrastructure.
CORE_MAIN_MODULES: tuple[str, ...] = (
    "hecate",
    "hecate.main",
    "hecate.auth",
    "hecate.auth.provider",
    "hecate.auth.resolver",
    # hecate.auth.registration removed in PR1.2 (replaced by entry_points).
    "hecate.auth.api_key_provider",
    "hecate.auth.jwt_provider",
    "hecate.vault",
    "hecate.vault.provider",
    "hecate.vault.resolver",
    "hecate.core.config",
    "hecate.core.database",
    "hecate.core.deps",
    "hecate.core.deps_workspace",
    "hecate.api.management.budget",
)

_PROBE_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    # Block hecate_enterprise at the import machinery level for the whole
    # subprocess lifetime. Any attempt to import it raises immediately.
    import importlib.abc

    class _EnterpriseBlocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "hecate_enterprise" or fullname.startswith("hecate_enterprise."):
                raise ImportError(
                    "blocked by core-self-sufficiency guard: " + fullname
                )
            return None

    sys.meta_path.insert(0, _EnterpriseBlocker())
    sys.modules.pop("hecate_enterprise", None)

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
    """Spawn a Python subprocess that imports main modules with enterprise blocked."""
    script = _PROBE_SCRIPT.replace("__MODULES__", repr(list(CORE_MAIN_MODULES)))
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


def test_main_package_importable_without_enterprise() -> None:
    """Every core main-package module must import without hecate_enterprise.

    The core-only invariant: self-hosted installs without the
    hecate-enterprise wheel must be able to build the FastAPI app
    (hecate.main) and use the base auth mechanisms.
    """
    result = _run_subprocess_probe()

    errors = result.get("errors", [])
    assert not errors, (
        "main-package modules failed to import without hecate_enterprise:\n"
        + "\n".join(f"  {e['module']}: {e['error']}" for e in errors)
        + "\n\nThese imports prove that src/hecate/ still structurally depends on"
        " hecate_enterprise — move the code back to core or lazy-mount it."
    )

    attempted = sorted(result["attempted"])
    imported = sorted(result["imported"])
    assert imported == attempted, f"some main-package modules were skipped: imported={imported} attempted={attempted}"
