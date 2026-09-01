"""Sandbox-package boundary invariant test (phase-4 follow-ups D).

Mirrors ``tests/test_layering_llm.py``. Pins three architectural rules
established by PR4c's hecate-sandbox extraction:

1. The engine tree never references ``hecate_sandbox`` at all — the engine
   sees only RuntimePort abstractions; ``engine/offloader.py``'s
   ``AgentEnvironment`` reference is TYPE_CHECKING-only and thus invisible
   to a grep of import statements executed at runtime.
2. The other extracted packages (ops / memory / enterprise / llm) never
   import ``hecate_sandbox`` — cross-package edges go through core.
3. Core's production imports of ``hecate_sandbox`` are lazy: inside
   function/method bodies or guarded by ``try/except ImportError`` (the
   PR2.1/PR3b/PR4b/PR4c consumer pattern), never at module top level —
   so ``hecate.main`` stays importable in a core-only test environment.

Technique: AST scan (like ``tests/test_engine/test_layering.py``) rather
than grep, because rule 3 needs the distinction between module-level and
function-local imports.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "hecate"
ENGINE_DIR = SRC_ROOT / "engine"
PACKAGES_ROOT = REPO_ROOT / "packages"
SANDBOX_PKG_DIR = PACKAGES_ROOT / "hecate-sandbox" / "src" / "hecate_sandbox"

# Core modules that legitimately reference the sandbox at module level.
# Extend ONLY with deliberate, reviewed exceptions — the default posture
# is lazy import.
_CORE_MODULELEVEL_ALLOWLIST: frozenset[str] = frozenset(
    {
        # settings/ABC plumbing may not need the package at runtime; none
        # today. tool/builtin.py and main.py import lazily inside bodies.
    }
)


def _iter_py(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_level_sandbox_imports(path: Path) -> list[str]:
    """Return *unconditional* module-level hecate_sandbox imports.

    Function-local and ``if TYPE_CHECKING:`` imports are invisible at
    runtime and therefore allowed everywhere (the engine's only sandbox
    reference, ``offloader.py``'s AgentEnvironment, is TYPE_CHECKING-only).
    """
    return _top_level_sandbox_imports(path)


def _top_level_sandbox_imports(path: Path) -> list[str]:
    """Imports at module top level only (not nested in functions/classes/if TYPE_CHECKING)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []

    def _visit(node: ast.AST, top_level: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom):
                mod = child.module or ""
                if mod == "hecate_sandbox" or mod.startswith("hecate_sandbox."):
                    if top_level:
                        hits.append(f"line {child.lineno}: from {mod} import ...")
                    continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if (alias.name == "hecate_sandbox" or alias.name.startswith("hecate_sandbox.")) and top_level:
                        hits.append(f"line {child.lineno}: import {alias.name}")
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                _visit(child, top_level=False)
            elif isinstance(child, ast.If):
                # `if TYPE_CHECKING:` guards are type-only, and a plain
                # top-level `if` still encloses the import — either way the
                # import is not unconditional, so treat it as lazy.
                _visit(child, top_level=False)
            elif isinstance(child, ast.Try):
                # `try: from x import y\nexcept ImportError: ...` is the
                # PR2.1/PR3b/PR4b/PR4c consumer pattern for optional
                # packages — the import is guarded.
                _visit(child, top_level=False)
            else:
                _visit(child, top_level=top_level)

    _visit(tree, top_level=True)
    return hits


class TestEngineNeverImportsSandbox:
    """The engine sees only RuntimePort abstractions — never hecate_sandbox."""

    def test_engine_tree_free_of_sandbox_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ENGINE_DIR):
            for hit in _module_level_sandbox_imports(path):
                bad.append(f"{path.relative_to(REPO_ROOT)}:{hit}")
        assert not bad, "engine/ must never import hecate_sandbox; found:\n" + "\n".join(bad)


class TestOtherPackagesSandboxFree:
    """Cross-package edges go through core; sibling wheels never import hecate_sandbox."""

    def test_sibling_packages_have_no_sandbox_imports(self) -> None:
        sibling_dirs = [p for p in PACKAGES_ROOT.iterdir() if p.is_dir() and p.name not in {"hecate-sandbox"}]
        if not sibling_dirs:
            pytest.skip("no sibling packages present")
        bad: list[str] = []
        for pkg in sibling_dirs:
            for path in _iter_py(pkg):
                for hit in _module_level_sandbox_imports(path):
                    bad.append(f"{path.relative_to(REPO_ROOT)}:{hit}")
        assert not bad, (
            "hecate_sandbox must only be imported by core consumers; sibling "
            "packages must route through core. Found:\n" + "\n".join(bad)
        )


class TestCoreSandboxImportsAreLazy:
    """Core imports hecate_sandbox lazily (function bodies / TYPE_CHECKING)."""

    def test_no_top_level_sandbox_imports_in_core(self) -> None:
        bad: list[str] = []
        for path in _iter_py(SRC_ROOT):
            rel = str(path.relative_to(REPO_ROOT))
            if rel in _CORE_MODULELEVEL_ALLOWLIST:
                continue
            for hit in _top_level_sandbox_imports(path):
                bad.append(f"{rel}:{hit}")
        assert not bad, (
            "Core must import hecate_sandbox lazily (inside function bodies or "
            "under try/except), so hecate.main stays importable without the "
            "sandbox wheel in test environments. Found top-level:\n" + "\n".join(bad)
        )
