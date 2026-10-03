"""Layering guard: entry-layer modules must not import engine concretes.

step5d introduced the platform entry execution service so entry chains
(HTTP chat, MCP, IM, A2A) stop assembling execution themselves. This test
pins the boundary: entry-layer modules must not import ``PregelRuntime``
or ``GraphCompiler`` — execution assembly happens only inside the entry
service and the shared runtime assembly. A violation fails naming the
module and the offending import.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "hecate"

# Entry-layer trees whose jobs are protocol adaptation, not assembly.
ENTRY_TREES = (
    SRC_ROOT / "channel" / "api",
    SRC_ROOT / "channel" / "a2a" / "server",
    SRC_ROOT / "channel" / "im",
    SRC_ROOT / "tools" / "mcp",
)

FORBIDDEN_SYMBOLS = ("PregelRuntime", "GraphCompiler")

# Migrated tail executors: llm_service may be imported as the provider
# seam handed to the runtime port, but invoking it directly bypasses the
# entry execution service.
TAIL_EXECUTOR_MODULES = (
    SRC_ROOT / "channel" / "a2a" / "server" / "executor.py",
    SRC_ROOT / "ops" / "scheduling" / "executors.py",
)
DIRECT_LLM_CALLS = ("llm_service.chat(", "llm_service.chat_stream(")

# step5 hardening: entry-side shared assembly must come from the single
# composition-layer source. Entry modules must not define their own
# process-wide store singletons (the default in-memory backends make each
# module-level instance a private, mutually invisible store) and must not
# import other entries' private assembly helpers.
ASSEMBLY_SCAN_TREES = ENTRY_TREES + (SRC_ROOT / "ops" / "scheduling",)
STORE_SINGLETON_NAMES = ("_shared_event_store", "_shared_session_state_store", "_shared_stores")
FORBIDDEN_PRIVATE_ASSEMBLY_IMPORTS = (
    "_build_tool_registry",
    "_load_agent_tools",
    "_get_shared_event_store",
    "_get_shared_session_state_store",
)


def _module_level_imports(path: Path) -> list[tuple[int, str, str | None]]:
    """Collect (line, module, imported_name) for all import statements.

    Mirrors the layering-domain scanner: function-local, TYPE_CHECKING and
    try/except ImportError imports are invisible to the runtime's import
    graph and therefore allowed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str, str | None]] = []

    def _visit(node: ast.AST, top_level: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom) and child.module and top_level:
                for alias in child.names:
                    hits.append((child.lineno, child.module, alias.name))
            elif isinstance(child, ast.Import) and top_level:
                for alias in child.names:
                    hits.append((child.lineno, alias.name, None))
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.If | ast.Try):
                _visit(child, top_level=False)
            else:
                _visit(child, top_level=top_level)

    _visit(tree, top_level=True)
    return hits


def _violations() -> list[str]:
    found: list[str] = []
    for tree_root in ENTRY_TREES:
        for path in sorted(tree_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for line, module, name in _module_level_imports(path):
                for symbol in FORBIDDEN_SYMBOLS:
                    if name == symbol or module.endswith(f".{symbol}") or module == symbol:
                        found.append(f"{path.relative_to(SRC_ROOT)}:{line} imports {symbol}")
    return found


def _direct_execution_service_construction() -> list[str]:
    """Entry modules must not construct WorkflowExecutionService themselves.

    The spec's observable rule: execution is only initiated through the
    entry execution service, so an inline platform-adapter construction
    in an entry module is a violation even when the import is lazy.
    """
    found: list[str] = []
    for tree_root in ENTRY_TREES:
        for path in sorted(tree_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "WorkflowExecutionService(" in source:
                for line_number, line in enumerate(source.splitlines(), start=1):
                    if "WorkflowExecutionService(" in line:
                        found.append(f"{path.relative_to(SRC_ROOT)}:{line_number} constructs WorkflowExecutionService")
    return found


def test_entry_layer_has_no_engine_concrete_imports() -> None:
    assert not _violations(), (
        "entry-layer modules must not import engine concretes "
        f"(assemble through the entry execution service instead): {_violations()}"
    )


def test_entry_layer_does_not_construct_execution_service_inline() -> None:
    assert not _direct_execution_service_construction(), (
        "entry-layer modules must initiate execution through "
        f"EntryExecutionService, not construct the platform adapter: {_direct_execution_service_construction()}"
    )


def test_layering_guard_rejects_injected_violation(tmp_path: Path) -> None:
    """The scanner itself works: a planted import is reported precisely."""
    module = tmp_path / "violating_entry.py"
    module.write_text("from hecate.runtime.pregel import PregelRuntime\n", encoding="utf-8")
    tree = ast.parse(module.read_text(encoding="utf-8"))
    hits = [
        (line, mod, name)
        for line, mod, name in _module_level_imports(module)
        for symbol in FORBIDDEN_SYMBOLS
        if name == symbol or mod.endswith(f".{symbol}")
    ]
    assert hits, "scanner must flag a planted engine concrete import"
    assert any("PregelRuntime" in (name or mod) for _, mod, name in hits)
    del tree


def test_tail_executors_have_no_direct_llm_calls() -> None:
    """Migrated tail executors must not invoke llm_service directly.

    Importing llm_service to hand it to the runtime port is the provider
    seam; calling ``llm_service.chat(...)`` in executor code bypasses the
    entry execution service (the pre-migration bypass this change closed).
    """
    for module in TAIL_EXECUTOR_MODULES:
        source = module.read_text(encoding="utf-8")
        for call in DIRECT_LLM_CALLS:
            assert call not in source, f"{module.name} must not call {call} directly (go through the entry service)"


def _label(path: Path) -> str:
    """Repo-relative display path; plain name for files outside the tree."""
    if path.is_relative_to(SRC_ROOT):
        return str(path.relative_to(SRC_ROOT))
    return path.name


def _store_singleton_definitions(path: Path) -> list[str]:
    """Module-level assignments that create a private per-module store singleton."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in STORE_SINGLETON_NAMES:
                found.append(f"{_label(path)}:{node.lineno} defines module-level {target.id}")
    return found


def _private_assembly_imports(path: Path) -> list[str]:
    """Imports of other entries' private assembly helpers from entry modules."""
    found: list[str] = []
    for line, module, name in _module_level_imports(path):
        if name in FORBIDDEN_PRIVATE_ASSEMBLY_IMPORTS:
            found.append(f"{_label(path)}:{line} imports {name} from {module}")
    return found


def test_entry_layer_has_no_private_store_singletons() -> None:
    found: list[str] = []
    for tree_root in ASSEMBLY_SCAN_TREES:
        for path in sorted(tree_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found.extend(_store_singleton_definitions(path))
    assert not found, (
        "entry modules must resolve stores through core.composition.entry_assembly, "
        f"not define per-module singletons (in-memory backends make each a private store): {found}"
    )


def test_entry_layer_has_no_cross_entry_private_assembly_imports() -> None:
    found: list[str] = []
    for tree_root in ASSEMBLY_SCAN_TREES:
        for path in sorted(tree_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found.extend(_private_assembly_imports(path))
    assert not found, (
        "entry modules must use the public assembly source "
        f"(core.composition.entry_assembly), not each other's private helpers: {found}"
    )


def test_layering_guard_rejects_injected_store_singleton(tmp_path: Path) -> None:
    """The singleton scanner itself works: a planted definition is reported."""
    module = tmp_path / "violating_entry.py"
    module.write_text("_shared_event_store = None\n", encoding="utf-8")
    hits = _store_singleton_definitions(module)
    assert hits and "_shared_event_store" in hits[0]


def test_layering_guard_rejects_injected_private_import(tmp_path: Path) -> None:
    """The private-import scanner itself works: a planted import is reported."""
    module = tmp_path / "violating_entry.py"
    module.write_text(
        "from hecate.channel.api.v1.chat import _build_tool_registry\n",
        encoding="utf-8",
    )
    hits = _private_assembly_imports(module)
    assert hits and "_build_tool_registry" in hits[0]
