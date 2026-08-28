"""Engine-layer layering invariant test.

Pin the AGENTS.md rule that ``engine/`` modules must not import anything from
``hecate.services.*``. We statically scan every Python file under
``src/hecate/engine/`` for ``import`` / ``import from`` statements whose target
starts with ``hecate.services``.

Companion to ``tests/test_engine/test_ports.py::test_default_does_not_import_sandbox_module``
which guards only the ``RuntimePort.tool_execute_sandbox`` default. This file
extends the invariant to every engine/ module via AST inspection — no module
import, no class-identity corruption, no subprocess cost.

Note: this catches only top-level ``import`` statements. Lazy / function-local
``from hecate.services... import ...`` lines also appear at module top level
and are caught. Conditional imports inside ``if TYPE_CHECKING:`` are filtered
out — those are type hints only and do not create a runtime dependency.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE_DIR = Path("src/hecate/engine")
SKIP_MODULES: frozenset[str] = frozenset(
    {
        # Entry-point scripts that legitimately read core.config (documented
        # legacy exception in AGENTS.md).
        "hecate.engine.temporal.run_worker",
    }
)


def _iter_engine_files() -> list[Path]:
    """Discover every leaf Python file under src/hecate/engine/."""
    out: list[Path] = []
    for path in sorted(ENGINE_DIR.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to("src").with_suffix("")
        if ".".join(rel.parts) in SKIP_MODULES:
            continue
        out.append(path)
    return out


def _is_services_module(name: str) -> bool:
    return name == "hecate.services" or name.startswith("hecate.services.")


def _direct_services_imports(path: Path) -> list[tuple[int, str]]:
    """Return (line_no, statement) for every top-level services import in path."""
    tree = ast.parse(path.read_text())
    hits: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_services_module(alias.name):
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module and _is_services_module(node.module):
            names = ", ".join(a.name for a in node.names)
            hits.append((node.lineno, f"from {node.module} import {names}"))
    return hits


@pytest.mark.parametrize("path", _iter_engine_files(), ids=lambda p: str(p.relative_to(ENGINE_DIR)))
def test_engine_module_has_no_top_level_services_import(path: Path) -> None:
    """No top-level ``import hecate.services.*`` statement is allowed in engine/."""
    hits = _direct_services_imports(path)
    assert not hits, (
        f"{path.relative_to(ENGINE_DIR)} has top-level services imports:\n"
        + "\n".join(f"  line {ln}: {stmt}" for ln, stmt in hits)
        + "\nengine/ must not depend on services/ — move the helper into engine/ or extend RuntimePort."
    )
