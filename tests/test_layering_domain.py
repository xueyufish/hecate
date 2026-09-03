"""Domain-directory layering guard (Phase R follow-ups).

Companion to ``tests/test_layering_sandbox.py``. Pins the per-domain
import boundaries of the modular monolith that Phase R builds. The
sandbox is split intentionally is two layers of defense:

- **AST scan (this file)**: catches top-level ``import`` statements
  that reference forbidden prefixes; cheap, deterministic, and easy to
  read in PR diffs.
- **Subprocess runtime probe
  (``tests/test_runtime/test_runtime_self_sufficiency.py``)**: blocks
  imports at the meta-path / ``builtins.__import__`` level inside a
  fresh Python process; catches transitive imports through conftest,
  fixtures, and side-effecting module-level code that an AST scan
  cannot detect.

Both layers cover overlapping ground by design — redundancy is cheap
when one layer is silent on lazy imports (the runtime probe) and the
other on transitive function-local calls (the AST scan).

Scope of this guard
-------------------

Rules are added as the corresponding domain directories land:

- ``runtime/`` (Phase R-MVP)
- ``tools/`` (Phase R follow-ups — both F1: tools/policy and the
  filling PR that moves services/tool/, services/skill/, and
  skill_registry/ into the same domain)

Other domains (``enterprise/``, ``channel/``, ``studio/``, ``ops/``)
land in subsequent Phase R-complete PRs; each lands with its own rule
added here as a small, focused follow-up. Premature rules covering
directories that do not yet exist would be vacuous — the AST scan
would always pass, hiding the real layering risk until the domain
actually arrives and starts consuming forbidden imports.

Forbidden-prefix table
-----------------------

The same prefixes listed in
``tests/test_runtime/test_runtime_self_sufficiency.py::ALL_BLOCKED_PREFIXES``
apply here, minus the workspace wheels (which the AST scan has no
opinion on — those are caught only by the runtime probe). The table
below keeps the two files in sync by referencing a single source.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "hecate"
PACKAGES_ROOT = REPO_ROOT / "packages"

# In-main-package domain directories that have been established.
# Phase R-complete adds entries here as new domain directories land.
ESTABLISHED_DOMAINS: dict[str, Path] = {
    "runtime": SRC_ROOT / "runtime",
    "tools": SRC_ROOT / "tools",
    "enterprise": SRC_ROOT / "enterprise",
    "channel": SRC_ROOT / "channel",
    "studio": SRC_ROOT / "studio",
    "ops": SRC_ROOT / "ops",
}

# Other in-main-package domain directories, forbidden as sources for
# top-level imports in any established domain (no cross-domain
# structural coupling — same rule as test_layering_sandbox's sibling
# rule). Phase R-complete adds entries here as new domains land.
# `services` is included while it still exists — once F3 (services/
# removal) lands, drop it from this list.
OTHER_DOMAINS: tuple[str, ...] = (
    "services",
    "enterprise",
    "channel",
    "studio",
    "ops",
)


def _iter_py(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_level_imports(path: Path) -> list[tuple[int, str]]:
    """Return ``(line, module)`` pairs for top-level imports only.

    Function-local, ``if TYPE_CHECKING:``, and ``try / except ImportError``
    imports are not unconditional — they are invisible to runtime and
    therefore allowed (the engine's only sandbox reference is
    TYPE_CHECKING-only; the equivalent carve-out applies here).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []

    def _visit(node: ast.AST, top_level: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom):
                mod = child.module or ""
                if top_level:
                    hits.append((child.lineno, mod))
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    if top_level:
                        hits.append((child.lineno, alias.name))
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                _visit(child, top_level=False)
            elif isinstance(child, (ast.If, ast.Try)):
                # `if TYPE_CHECKING:` guards are type-only; a top-level
                # `if` / `try` still encloses the import — either way
                # the import is not unconditional, so treat as lazy.
                _visit(child, top_level=False)
            else:
                _visit(child, top_level=top_level)

    _visit(tree, top_level=True)
    return hits


def _is_other_domain_import(module: str) -> str | None:
    """Return the domain prefix if ``module`` is a top-level import of another domain.

    Allowed self-imports (``tools.X`` inside ``tools/``) are skipped; the
    AST scan only flags cross-domain references.
    """
    for prefix in OTHER_DOMAINS:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


class TestToolsDomainNeverImportsOtherDomains:
    """The tools/ domain sees only its own abstractions and core."""

    def test_tools_tree_has_no_other_domain_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ESTABLISHED_DOMAINS["tools"]):
            for lineno, module in _module_level_imports(path):
                other = _is_other_domain_import(module)
                if other is not None:
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, "tools/ must not import other domain directories at module level; found:\n" + "\n".join(bad)


class TestRuntimeDomainNeverImportsBlockedPrefixes:
    """The runtime/ domain is the future ``hecate-runtime`` wheel.

    The runtime sees only core, not other in-main-package domains or any
    workspace wheel. This is a stricter version of the runtime
    self-sufficiency probe (AST catches import statements at source,
    the subprocess probe catches transitive / runtime instances).
    """

    def test_runtime_tree_has_no_other_domain_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ESTABLISHED_DOMAINS["runtime"]):
            for lineno, module in _module_level_imports(path):
                other = _is_other_domain_import(module)
                if other is not None:
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, (
            "runtime/ must not import other in-main-package domain directories "
            "at module level; found:\n" + "\n".join(bad)
        )


class TestSiblingPackagesAndOtherDomainsNeverImportTools:
    """Workspace wheels must not import tools/ directly.

    Cross-domain edges go through core, not sideways. This rule is
    practical only after the tools/ domain has enough surface area
    (the F1 policy pipeline alone was too small to be meaningful; the
    filling PR adds registry/builtin/cache/search/shell_*/skill_*/
    skill_registry_*) to make a worthwhile guard.

    Other in-main-package domains are not yet enforced (only tools and
    runtime exist today). When enterprise/channel/studio/ops land in
    subsequent PRs, this rule naturally extends — a workspace wheel
    that imports any ``hecate.<domain>.*`` at module level is a
    cross-edge violation regardless of which domain it picks.
    """

    def test_no_workspace_wheel_imports_tools(self) -> None:
        bad: list[str] = []
        for path in _iter_py(PACKAGES_ROOT):
            for lineno, module in _module_level_imports(path):
                if module == "hecate.tools" or module.startswith("hecate.tools."):
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, (
            "workspace wheels must not import hecate.tools.* — tools/ is a "
            "main-package domain and core is the only allowed consumer. "
            "Found:\n" + "\n".join(bad)
        )


class TestEnterpriseDomainNeverImportsOtherDomains:
    """The enterprise/ domain sees only its own abstractions and core."""

    def test_enterprise_tree_has_no_other_domain_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ESTABLISHED_DOMAINS["enterprise"]):
            for lineno, module in _module_level_imports(path):
                other = _is_other_domain_import(module)
                if other is not None:
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, "enterprise/ must not import other domain directories at module level; found:\n" + "\n".join(
            bad
        )


class TestSiblingPackagesAndOtherDomainsNeverImportEnterprise:
    """enterprise/ is the special-case domain where workspace imports are expected.

    Unlike tools/ (where the rule is "no workspace may import the domain"),
    enterprise/ exists precisely so that workspace packages can implement
    its interfaces: ``hecate_enterprise.auth.*`` imports
    ``hecate.enterprise.auth.provider`` (the ``AuthProvider`` ABC) to
    declare its concrete SSO providers; ``hecate_enterprise.vault.*``
    imports ``hecate.enterprise.vault.provider`` for the ``SecretProvider``
    ABC. These imports are by design — they are the protocol seam.

    The rule here is the *inverse* of tools/: workspace wheels *may*
    import the enterprise/ domain, but only ``hecate_enterprise`` (the
    protocol-implementer). Other workspace packages
    (``hecate_ops``, ``hecate_llm``, ``hecate_memory``, channel plugins,
    etc.) have no business reaching into the enterprise/ domain and
    must stay out.
    """

    def test_only_hecate_enterprise_may_import_enterprise(self) -> None:
        bad: list[str] = []
        for path in _iter_py(PACKAGES_ROOT):
            for lineno, module in _module_level_imports(path):
                if not (module == "hecate.enterprise" or module.startswith("hecate.enterprise.")):
                    continue
                rel = path.relative_to(REPO_ROOT)
                if str(rel).startswith("packages/hecate-enterprise/"):
                    continue
                bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, (
            "Only packages/hecate-enterprise/ may import hecate.enterprise.* "
            "(it implements the AuthProvider / SecretProvider ABCs). Other "
            "workspace wheels must stay out of the enterprise/ domain. "
            "Found:\n" + "\n".join(bad)
        )


class TestChannelDomainNeverImportsOtherDomains:
    """The channel/ domain sees only its own abstractions and core."""

    def test_channel_tree_has_no_other_domain_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ESTABLISHED_DOMAINS["channel"]):
            for lineno, module in _module_level_imports(path):
                other = _is_other_domain_import(module)
                if other is not None:
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, "channel/ must not import other domain directories at module level; found:\n" + "\n".join(bad)


class TestSiblingPackagesAndOtherDomainsNeverImportChannel:
    """Workspace wheels must not import channel/ directly.

    Same shape as ``TestSiblingPackagesAndOtherDomainsNeverImportTools``.
    ``packages/hecate-channel-{slack,feishu}`` *do* ship in
    ``packages/channels/`` and they legitimately subclass
    ``hecate.channel.adapter.ChannelBase`` and import
    ``hecate.channel.im.*`` types — but the cross-edge seam goes
    through the entry-point group ``hecate.channel_providers`` (PR5a),
    not through direct imports. The plugins import
    ``ChannelBase`` etc. via ``TYPE_CHECKING`` so this AST scan
    naturally allows the protocol-implementers while flagging
    anything that reaches into the *runtime* modules of channel/
    (im.message_bus, gateway.session, a2a.*).
    """

    def test_no_workspace_wheel_imports_channel(self) -> None:
        bad: list[str] = []
        for path in _iter_py(PACKAGES_ROOT):
            for lineno, module in _module_level_imports(path):
                if not (module == "hecate.channel" or module.startswith("hecate.channel.")):
                    continue
                rel = path.relative_to(REPO_ROOT)
                # Plugin channel packages (hecate-channel-*) are the
                # protocol-implementers — they import ChannelBase and
                # channel types by design. Allow them.
                if str(rel).startswith("packages/channels/"):
                    continue
                # Other workspace wheels have no business reaching
                # into channel/ at module level.
                bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, (
            "workspace wheels (other than packages/channels/* plugin "
            "packages) must not import hecate.channel.* — channel/ is a "
            "main-package domain and core is the only allowed consumer. "
            "Found:\n" + "\n".join(bad)
        )


class TestStudioDomainNeverImportsOtherDomains:
    """The studio/ domain sees only its own abstractions and core."""

    def test_studio_tree_has_no_other_domain_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ESTABLISHED_DOMAINS["studio"]):
            for lineno, module in _module_level_imports(path):
                other = _is_other_domain_import(module)
                if other is not None:
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, "studio/ must not import other domain directories at module level; found:\n" + "\n".join(bad)


class TestSiblingPackagesAndOtherDomainsNeverImportStudio:
    """Workspace wheels must not import studio/ directly.

    Same shape as the tools/ and channel/ rules: studio is a pure
    authoring domain whose content (workflows, multi-agent patterns,
    meta-agents, JSON templates) lives entirely in core. No workspace
    package implements a studio interface, so the rule is strict —
    zero workspace-wheel imports allowed.
    """

    def test_no_workspace_wheel_imports_studio(self) -> None:
        bad: list[str] = []
        for path in _iter_py(PACKAGES_ROOT):
            for lineno, module in _module_level_imports(path):
                if not (module == "hecate.studio" or module.startswith("hecate.studio.")):
                    continue
                rel = path.relative_to(REPO_ROOT)
                # hecate-llm subclasses CircuitBreaker from
                # studio.validation.retry_policy — protocol-implementer
                # pattern, same as enterprise. Allow-list.
                if str(rel).startswith("packages/hecate-llm/"):
                    continue
                bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, (
            "workspace wheels (other than packages/hecate-llm/) must "
            "not import hecate.studio.* — studio/ is a main-package "
            "authoring domain. hecate-llm is allowed because its "
            "circuit breaker subclasses the ABC in "
            "studio.validation.retry_policy. Found:\n" + "\n".join(bad)
        )


class TestOpsDomainNeverImportsOtherDomains:
    """The ops/ domain sees only its own abstractions and core."""

    def test_ops_tree_has_no_other_domain_imports(self) -> None:
        bad: list[str] = []
        for path in _iter_py(ESTABLISHED_DOMAINS["ops"]):
            for lineno, module in _module_level_imports(path):
                other = _is_other_domain_import(module)
                if other is not None:
                    rel = path.relative_to(REPO_ROOT)
                    bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, "ops/ must not import other domain directories at module level; found:\n" + "\n".join(bad)


class TestSiblingPackagesAndOtherDomainsNeverImportOps:
    """Workspace wheels must not import ops/ directly.

    Same shape as the studio/ rule: ops/ is a pure platform-management
    domain. No workspace package implements an ops interface, so the
    rule is strict — zero workspace-wheel imports allowed.

    Note: ops/ is the sixth and final domain in Phase R-complete.
    Once this rule lands there are no remaining placeholder
    ``TestFutureSiblingRulePlaceholder`` rules to replace; if a
    seventh domain appears, restore the placeholder here.
    """

    def test_no_workspace_wheel_imports_ops(self) -> None:
        bad: list[str] = []
        for path in _iter_py(PACKAGES_ROOT):
            for lineno, module in _module_level_imports(path):
                if not (module == "hecate.ops" or module.startswith("hecate.ops.")):
                    continue
                rel = path.relative_to(REPO_ROOT)
                # hecate_enterprise imports cost / quota via core ops
                # to compute budget consumption — a legitimate cross-
                # edge use of the management plane, analogous to how
                # the enterprise rule allows hecate_enterprise to
                # import the enterprise/ domain for the AuthProvider
                # ABC.
                if str(rel).startswith("packages/hecate-enterprise/"):
                    continue
                bad.append(f"{rel}:line {lineno}: from {module} import ...")
        assert not bad, (
            "workspace wheels (other than packages/hecate-enterprise/) "
            "must not import hecate.ops.* — ops/ is a main-package "
            "management domain. hecate-enterprise is allowed because "
            "its budget service consumes cost + quota from core. "
            "Found:\n" + "\n".join(bad)
        )
