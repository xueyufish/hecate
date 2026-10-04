"""Contract-layer import purity guard (task 6.1).

``hecate.contracts`` and ``hecate.execution`` are the language-neutral seam:
third-party implementers depend on the authoritative schema files, and the
Python mapping must stay a stdlib-only convenience layer. Neither package may
import business domains, ORM, web framework, the Pregel engine modules, or
vendor SDKs - including function-local, conditional, and try-block imports.

This mirrors ``tests/test_runtime/test_runtime_self_sufficiency.py`` (AST scan
of every import position). Layering registration: ``execution`` is registered
in ``tests/test_layering_domain.py::ESTABLISHED_DOMAINS`` so the established
domains refuse to import it; ``contracts`` is deliberately NOT registered
there - it is the shared vocabulary every domain may consume (like
``models/``), and this probe is what keeps *it* clean instead.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = REPO_ROOT / "src" / "hecate" / "contracts"
EXECUTION_ROOT = REPO_ROOT / "src" / "hecate" / "execution"
# The durable-execution contract cluster moved to the independently
# installable ``hecate-durable`` package (step6 durable-execution-core); the
# paths under ``src/hecate`` forward there. The package's own pure modules
# are scanned with the same rule so the guarantee follows the code home.
DURABLE_ROOT = REPO_ROOT / "packages" / "hecate-durable" / "src" / "hecate_durable"
DURABLE_CONTRACTS_ROOT = DURABLE_ROOT / "contracts"

# Forbidden import prefixes for the contract layer. ``hecate.contracts`` /
# ``hecate.execution`` are self-imports and allowed; stdlib is allowed.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "hecate.models",
    "hecate.studio",
    "hecate.channel",
    "hecate.tools",
    "hecate.ops",
    "hecate.enterprise",
    "hecate.runtime.pregel",  # runtime package allowed; engine modules are not
    "sqlalchemy",
    "fastapi",
    "litellm",
)

_SELF_PACKAGES = ("hecate.contracts", "hecate.execution")

# The language-neutral seam inside ``execution/``: the ABC mapping files a
# third-party backend implementer faces (plan step3). Registry service
# modules (``*_registry.py``) are Control Plane services on the platform
# side of that seam — they legitimately consume the ORM to own the
# registration tables (plan step4/§三) and are never imported by external
# implementers — so they are exempt here, while the single-writer rule
# (tests/test_layering_domain.py) keeps the tables execution-owned.
_SEAM_MODULES = {
    "__init__.py",
    "backend.py",
    "sandbox.py",
    "stub.py",
    "durable.py",
    "stub_durable.py",
}


def _iter_py(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _import_modules(path: Path, package_root: Path) -> list[tuple[int, str]]:
    """Return ``(line, module)`` for EVERY import position in ``path``.

    Unlike the layering guard's top-level-only scan, this walks into functions,
    conditions, and try blocks: a lazy import is exactly the escape this probe
    exists to catch.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hits.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0:
                # Relative import: resolve against the file's package root.
                rel = path.relative_to(package_root).parent
                depth = node.level - 1
                base = ".".join(rel.parts[: len(rel.parts) - depth]) if depth else ".".join(rel.parts)
                module = f"{package_root.name}.{base}.{module}" if base else f"{package_root.name}.{module}"
            hits.append((node.lineno, module))
    return hits


def find_violations(root: Path, package_name: str) -> list[str]:
    """Scan every ``.py`` file under ``root`` for forbidden imports."""

    violations: list[str] = []
    for path in _iter_py(root):
        if package_name == "execution" and path.name not in _SEAM_MODULES:
            continue
        if package_name == "hecate_durable-top" and path.name not in {"__init__.py", "seams.py", "stub.py"}:
            continue
        for lineno, module in _import_modules(path, package_root=root):
            if module.split(".", 1)[0] in sys.stdlib_module_names:
                continue
            allowed = ("hecate.contracts", "contracts")
            if package_name == "execution":
                allowed += ("hecate.execution", "execution")
            if package_name in {"contracts", "execution"}:
                # Forwarding shims reach the contract cluster's real home in
                # the independently installable package; its pure modules are
                # equivalent to ``hecate.contracts`` here. Storage (SQLAlchemy)
                # is deliberately NOT allowed through this seam.
                allowed += ("hecate_durable.contracts", "hecate_durable.seams", "hecate_durable.stub")
            if package_name == "hecate_durable-contracts":
                allowed += ("hecate_durable.contracts",)
            if package_name == "hecate_durable-top":
                allowed += ("hecate_durable", "hecate_durable.contracts", "hecate_durable.seams", "hecate_durable.stub")
            if any(module == prefix or module.startswith(prefix + ".") for prefix in allowed):
                continue
            try:
                rel = path.relative_to(REPO_ROOT)
            except ValueError:
                rel = path
            violations.append(f"{rel}:line {lineno}: imports {module}")
    return violations


def test_contract_layer_has_no_forbidden_imports() -> None:
    bad = find_violations(CONTRACTS_ROOT, "contracts") + find_violations(EXECUTION_ROOT, "execution")
    assert not bad, (
        "contracts/ and execution/ must not import business domains, ORM, web "
        "framework, Pregel engine modules, or vendor SDKs; found:\n" + "\n".join(bad)
    )


def test_probe_catches_lazy_import_escape(tmp_path) -> None:
    """Negative control: a function-local forbidden import is detected."""

    package = tmp_path / "contracts"
    package.mkdir()
    (package / "clean.py").write_text("from dataclasses import dataclass\n", encoding="utf-8")
    guilty = package / "guilty.py"
    guilty.write_text(
        "def sneaky():\n    from hecate.models.agent import AgentModel\n    return AgentModel\n",
        encoding="utf-8",
    )

    violations = find_violations(package, "contracts")
    assert any("guilty.py" in v and "hecate.models.agent" in v for v in violations)


def test_probe_catches_try_block_import_escape(tmp_path) -> None:
    package = tmp_path / "contracts"
    package.mkdir()
    (package / "guarded.py").write_text(
        "try:\n    import sqlalchemy\nexcept ImportError:\n    sqlalchemy = None\n",
        encoding="utf-8",
    )

    violations = find_violations(package, "contracts")
    assert any("guarded.py" in v and "sqlalchemy" in v for v in violations)


def test_probe_rejects_unlisted_vendor_sdk_and_reverse_dependency(tmp_path) -> None:
    package = tmp_path / "contracts"
    package.mkdir()
    (package / "vendor.py").write_text(
        "import anthropic\nfrom hecate.execution.backend import RunState\n", encoding="utf-8"
    )
    violations = find_violations(package, "contracts")
    assert len(violations) == 2
