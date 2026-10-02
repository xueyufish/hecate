"""Kernel purity guard: ``hecate_runtime`` must not import the main app.

Static, absolute rule for **executed module-level imports**: importing the
kernel must never pull any module from the ``hecate`` main-application
namespace (``hecate.core``, ``hecate.models``, ``hecate.tools``, ...).
Separate distribution roots (``hecate_memory``, ``hecate_sandbox``, ...)
are allowed as optional lazy imports and are policed by the
self-sufficiency probe.

Function-level lazy imports into the platform are the sanctioned inventory
in the package AGENTS.md (each with an exit condition); the dynamic probe
enforces that inventory. This test closes the stronger guarantee: a bare
``import hecate_runtime`` executes zero main-application imports.

Executable form of the runtime-standalone-distribution spec requirement
"No reverse dependency on the full application".
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

KERNEL_ROOT = Path("packages/hecate-runtime/src/hecate_runtime")


def _executed_module_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Yield (lineno, module) for imports executed at module import time.

    Descends into top-level ``if``/``try`` blocks (they execute) but skips
    ``if TYPE_CHECKING:`` guards (annotations only) and function bodies
    (not executed at import time).
    """
    hits: list[tuple[int, str]] = []

    def mod_name(node: ast.Import | ast.ImportFrom) -> str | None:
        if isinstance(node, ast.Import):
            return node.names[0].name if len(node.names) == 1 else None
        return node.module if node.level == 0 else None

    def visit_body(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                name = mod_name(cast("ast.Import | ast.ImportFrom", stmt))
                if name is not None:
                    hits.append((stmt.lineno, name))
            elif isinstance(stmt, ast.If):
                test = stmt.test
                is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                if not is_type_checking:
                    visit_body(stmt.body)
                    visit_body(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                visit_body(stmt.body)
                visit_body(stmt.orelse)
                visit_body(stmt.finalbody)
            # FunctionDef/AsyncFunctionDef/ClassDef bodies: skip — class
            # bodies execute, but a class-level import is still inside the
            # class namespace at import time; include them for strictness.
            elif isinstance(stmt, ast.ClassDef):
                visit_body(stmt.body)

    visit_body(tree.body)
    return hits


def test_kernel_module_level_imports_are_application_free() -> None:
    assert KERNEL_ROOT.exists(), "kernel package missing"
    violations: list[str] = []
    for path in sorted(KERNEL_ROOT.rglob("*.py")):
        rel = path.relative_to(KERNEL_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, name in _executed_module_imports(tree):
            if name == "hecate" or name.startswith("hecate."):
                violations.append(f"{rel}:{lineno}: {name}")
    assert not violations, "kernel executes main-application imports at module level:\n" + "\n".join(violations)
