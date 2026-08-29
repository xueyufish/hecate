"""Main-package layering invariant: no structural hecate_enterprise imports.

Pin the package-split plan's invariant 1: ``src/hecate/`` must never
structurally import ``hecate_enterprise.*``. The only permitted contact
points are lazy / guarded mounts inside ``hecate/main.py`` and
``hecate/auth/registration.py`` (both wrapped in ``try/except ImportError``
or function-local inside a method), which this scanner identifies by
allowing imports that are syntactically nested inside a ``try`` block or
function body.

Companion to ``tests/test_engine/test_layering.py`` (engine/ → services
invariant). Static AST scan — no module import, no side effects.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MAIN_PKG_DIR = Path("src/hecate")


def _iter_main_package_files() -> list[Path]:
    """Discover every Python file under src/hecate/."""
    return sorted(p for p in MAIN_PKG_DIR.rglob("*.py") if p.name != "__init__.py")


def _structural_enterprise_imports(path: Path) -> list[tuple[int, str]]:
    """Return (line_no, stmt) for top-level / structural enterprise imports.

    "Structural" = executed at module import time outside a try block or
    function body. Lazy imports inside functions and guarded mounts inside
    ``try/except ImportError`` are the sanctioned pattern and are skipped.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []

    def _is_enterprise(node: ast.Import | ast.ImportFrom) -> bool:
        if isinstance(node, ast.Import):
            return any(a.name == "hecate_enterprise" or a.name.startswith("hecate_enterprise.") for a in node.names)
        return bool(
            node.module and (node.module == "hecate_enterprise" or node.module.startswith("hecate_enterprise."))
        )

    def _stmt_text(node: ast.Import | ast.ImportFrom) -> str:
        if isinstance(node, ast.Import):
            return f"import {', '.join(a.name for a in node.names)}"
        names = ", ".join(a.name for a in node.names)
        return f"from {node.module} import {names}"

    def _walk_in_try(node: ast.Try) -> None:
        # imports inside try blocks: allowed only if the handler catches ImportError
        handles_import_error = any(_handles_import_error(handler) for handler in node.handlers)
        if not handles_import_error:
            for child in ast.walk(node):
                if isinstance(child, ast.Import | ast.ImportFrom) and _is_enterprise(child):
                    hits.append((child.lineno, _stmt_text(child)))

    def _handles_import_error(handler: ast.ExceptHandler) -> bool:
        if handler.type is None:
            return False
        names = []
        if isinstance(handler.type, ast.Name):
            names = [handler.type.id]
        elif isinstance(handler.type, ast.Tuple):
            names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
        return "ImportError" in names or "ModuleNotFoundError" in names

    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom) and _is_enterprise(node):
            hits.append((node.lineno, _stmt_text(node)))
        elif isinstance(node, ast.Try):
            _walk_in_try(node)

    return hits


@pytest.mark.parametrize("path", _iter_main_package_files(), ids=lambda p: str(p.relative_to(MAIN_PKG_DIR)))
def test_no_structural_enterprise_import(path: Path) -> None:
    """src/hecate/ files must not structurally import hecate_enterprise.

    Allowed: imports inside function bodies (lazy) or inside
    ``try/except ImportError`` blocks (guarded mounts in main.py).
    """
    hits = _structural_enterprise_imports(path)
    rel = path.relative_to(MAIN_PKG_DIR)
    assert not hits, (
        f"{rel} has structural hecate_enterprise imports (violates package-split"
        " invariant 1: main package must not structurally depend on enterprise):\n"
        + "\n".join(f"  line {ln}: {stmt}" for ln, stmt in hits)
        + "\n\nFix: move the import inside a function body (lazy) or wrap in"
        " try/except ImportError (guarded mount)."
    )
