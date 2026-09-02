"""Channel-plugin-package boundary invariant test (PR5b).

Mirrors ``tests/test_layering_sandbox.py``. Pins three rules established
by the hecate-channel-{slack,feishu} extraction:

1. The engine tree never references ``hecate_channel_*`` — the engine
   sees only its own abstractions.
2. Core's production imports of the channel plugin packages are lazy:
   inside function/method bodies or guarded by ``try/except ImportError``
   (the ``gateway/registration.py`` fallback pattern), never at module
   top level — so ``hecate.main`` stays importable in a core-only
   environment.
3. No other package imports a channel package — cross-package edges go
   through core's channel contract. A channel package's own tree is the
   only place its module name may appear.

Technique: AST scan (like ``tests/test_engine/test_layering.py``) rather
than grep, because rule 2 needs the distinction between module-level and
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
CHANNELS_ROOT = PACKAGES_ROOT / "channels"

# Channel plugin package import-name → package directory.
CHANNEL_PACKAGES: dict[str, Path] = {
    "hecate_channel_slack": CHANNELS_ROOT / "hecate-channel-slack",
    "hecate_channel_feishu": CHANNELS_ROOT / "hecate-channel-feishu",
}


def _iter_py(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _matching_prefix(module: str) -> str | None:
    for prefix in CHANNEL_PACKAGES:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _top_level_channel_imports(path: Path) -> list[str]:
    """Imports at module top level only (not nested in functions/classes/if/try).

    Function-local, ``if TYPE_CHECKING:``, and ``try/except ImportError``
    imports are invisible at runtime and therefore allowed in core (the
    registration fallback pattern).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []

    def _visit(node: ast.AST, top_level: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom):
                mod = child.module or ""
                if _matching_prefix(mod) and top_level:
                    hits.append(f"line {child.lineno}: from {mod} import ...")
                continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if _matching_prefix(alias.name) and top_level:
                        hits.append(f"line {child.lineno}: import {alias.name}")
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                _visit(child, top_level=False)
            elif isinstance(child, (ast.If, ast.Try)):
                # `if TYPE_CHECKING:` guards are type-only; a top-level
                # `if`/`try` still encloses the import — either way the
                # import is not unconditional, so treat it as lazy.
                _visit(child, top_level=False)
            else:
                _visit(child, top_level=top_level)

    _visit(tree, top_level=True)
    return hits


def _channel_imports(path: Path) -> list[tuple[int, str]]:
    """All channel-package imports (module-level and lazy) as (line, module)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _matching_prefix(node.module or ""):
                hits.append((node.lineno, node.module or ""))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _matching_prefix(alias.name):
                    hits.append((node.lineno, alias.name))
    return hits


def _package_dirs() -> list[Path]:
    dirs = [p for p in PACKAGES_ROOT.iterdir() if p.is_dir() and p.name != "channels"]
    if CHANNELS_ROOT.is_dir():
        dirs += [p for p in CHANNELS_ROOT.iterdir() if p.is_dir()]
    return dirs


class TestEngineNeverImportsChannelPackages:
    """The engine sees only its own abstractions — never hecate_channel_*."""

    def test_engine_tree_free_of_channel_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ENGINE_DIR):
            for lineno, module in _channel_imports(path):
                bad.append(f"{path.relative_to(REPO_ROOT)}:line {lineno}: {module}")
        assert not bad, "engine/ must never import hecate_channel_*; found:\n" + "\n".join(bad)


class TestCoreChannelImportsAreLazy:
    """Core imports the channel packages lazily (function bodies / guarded)."""

    def test_no_top_level_channel_imports_in_core(self) -> None:
        bad: list[str] = []
        for path in _iter_py(SRC_ROOT):
            for hit in _top_level_channel_imports(path):
                bad.append(f"{path.relative_to(REPO_ROOT)}:{hit}")
        assert not bad, (
            "Core must import hecate_channel_* lazily (inside function bodies or "
            "under try/except), so hecate.main stays importable without the "
            "channel wheels in test environments. Found top-level:\n" + "\n".join(bad)
        )


class TestPackagesOnlyImportOwnChannelPackage:
    """Cross-package edges go through core; a channel package's module name
    may only appear inside its own package tree."""

    def test_other_packages_never_import_channel_packages(self) -> None:
        if not _package_dirs():
            pytest.skip("no sibling packages present")
        bad: list[str] = []
        for pkg in _package_dirs():
            for path in _iter_py(pkg):
                for lineno, module in _channel_imports(path):
                    prefix = _matching_prefix(module)
                    assert prefix is not None  # _channel_imports only yields matches
                    if path.is_relative_to(CHANNEL_PACKAGES[prefix]):
                        continue
                    bad.append(f"{path.relative_to(REPO_ROOT)}:line {lineno}: {module}")
        assert not bad, (
            "hecate_channel_* must only be imported by core consumers and its own "
            "package; sibling packages must route through core's channel contract. "
            "Found:\n" + "\n".join(bad)
        )
