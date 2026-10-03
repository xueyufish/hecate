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
