"""Composition root structural guards (PR-E.1).

The composition package is the single assembly point for the
application (plan §1.1). These guards pin its shape so future
refactors cannot accidentally scatter assembly across main.py or
domain modules again.

Three classes of guards:

1. ``TestCompositionExports`` — every public function the lifespan
   depends on is exported from ``hecate.core.composition.wiring``.
2. ``TestCompositionHasNoBusinessLogic`` — composition must stay
   a *wiring* layer: no domain logic, no SQL, no model imports.
   The single legitimate exception is the workspace-wheel imports
   (``hecate-enterprise``, ``hecate_ops``, ``hecate_sandbox``,
   ``hecate_llm``, ``hecate_memory``) that the wiring helpers
   require for the plugin / tracer / vault / pool lifecycles.
3. ``TestComposeApplicationCompositionOrder`` — the order of
   helpers in ``compose_application`` is significant (cheap
   singletons first, state stores second, scanners / pipelines
   third, scheduled tasks last). Pin the order via AST inspection
   so an accidental reorder surfaces in tests, not production.

These guards do not exercise the actual wiring at runtime — that
is what the existing main.py integration tests do. They are
shape-only checks; running them is cheap.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WIRING_FILE = REPO_ROOT / "src" / "hecate" / "core" / "composition" / "wiring.py"


_PUBLIC_HELPERS = (
    "seed_builtin_tools",
    "register_secret_providers",
    "attach_state_stores",
    "attach_dlp_scanner",
    "discover_plugins",
    "replay_agent_plugin_mcp",
    "start_meta_agents",
    "register_im_channels",
    "start_budget_scheduler",
    "configure_tracing",
    "start_monitoring",
    "start_audit_batch_writer",
    "prewarm_sandbox_pool",
    "start_tool_decision_pipeline",
    "start_security_findings",
    "start_siem_export",
    "compose_application",
)


class TestCompositionExports:
    """All public helpers compose_application depends on must exist."""

    def test_all_helpers_defined(self) -> None:
        text = WIRING_FILE.read_text(encoding="utf-8")
        missing = [name for name in _PUBLIC_HELPERS if not (f"def {name}(" in text or f"async def {name}(" in text)]
        assert not missing, "composition/wiring.py is missing required helpers: " + ", ".join(missing)


class TestCompositionHasNoBusinessLogic:
    """Composition is wiring only; it does not import models or domain modules.

    Domain modules must never be imported at composition-time — that
    would couple the assembly layer to the business surface. The
    composition helpers themselves can do runtime imports (the way
    ``seed_builtin_tools`` does), but the * module-level imports *
    of wiring.py should be limited to stdlib + FastAPI + settings
    (settings is a config facade, not a domain).
    """

    @pytest.fixture
    def wiring_imports(self) -> list[str]:
        tree = ast.parse(WIRING_FILE.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        return imports

    def test_no_top_level_domain_imports(self, wiring_imports: list[str]) -> None:
        """Composition should not import any domain package at module level."""
        forbidden_prefixes = (
            "hecate.runtime",
            "hecate.tools",
            "hecate.enterprise",
            "hecate.channel",
            "hecate.studio",
            "hecate.ops",
        )
        offenders = [
            mod for mod in wiring_imports if any(mod == p or mod.startswith(p + ".") for p in forbidden_prefixes)
        ]
        assert not offenders, (
            "composition/wiring.py must not import domain modules at "
            "module level (lazy-import inside helpers instead): " + ", ".join(offenders)
        )

    def test_no_top_level_models_imports(self, wiring_imports: list[str]) -> None:
        """Composition must not import ORM models — those are domain concerns."""
        offenders = [m for m in wiring_imports if m.startswith("hecate.models")]
        assert not offenders, "composition/wiring.py must not import ORM models: " + ", ".join(offenders)


class TestComposeApplicationCompositionOrder:
    """The helpers must run in a documented startup order.

    Composition order (cheap singletons first, then state stores,
    then scanners / pipelines, then scheduled tasks):

    1. seed_builtin_tools         (cheap; no side-effects on app.state)
    2. register_secret_providers  (cheap; populates vault resolver)
    3. attach_state_stores        (cheap; populates app.state)
    4. attach_dlp_scanner         (cheap; populates app.state)
    5. discover_plugins           (DB read; populates plugin registry)
    6. replay_agent_plugin_mcp    (DB write; populates plugin registry)
    7. start_meta_agents          (background scheduler)
    8. register_im_channels       (PluginRegistry + IMMessageBus)
    9. start_budget_scheduler     (background cron)
    10. configure_tracing         (global tracer)
    11. start_monitoring          (background thread)
    12. start_audit_batch_writer  (background queue + writer)
    13. prewarm_sandbox_pool      (container prewarm)
    14. start_tool_decision_pipeline (sink wiring)
    15. start_security_findings   (sink wiring)
    16. start_siem_export         (background collector)
    """

    def test_compose_application_calls_helpers_in_documented_order(self) -> None:
        text = WIRING_FILE.read_text(encoding="utf-8")
        # Find the body of compose_application and extract call names
        # in source order. We only care about top-level call lines
        # (not nested) — the wiring helpers are awaited/called
        # directly at function body indent level.
        tree = ast.parse(text)
        compose = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "compose_application"
            ),
            None,
        )
        assert compose is not None, "compose_application not found in wiring.py"
        calls: list[str] = []
        for stmt in compose.body:
            if not isinstance(stmt, ast.Expr):
                continue
            value = stmt.value
            # Call can appear directly (value is ast.Call) or wrapped
            # in ``await`` (value is ast.Await whose .value is ast.Call).
            if isinstance(value, ast.Await):
                value = value.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                calls.append(value.func.id)
        # Spot-check the documented order: each helper should appear
        # in the same relative order as in the wiring.py body.
        expected_subset = (
            "seed_builtin_tools",
            "register_secret_providers",
            "attach_state_stores",
            "attach_dlp_scanner",
            "discover_plugins",
            "replay_agent_plugin_mcp",
            "start_meta_agents",
            "register_im_channels",
            "start_budget_scheduler",
            "configure_tracing",
            "start_monitoring",
            "start_audit_batch_writer",
            "prewarm_sandbox_pool",
            "start_tool_decision_pipeline",
            "start_security_findings",
            "start_siem_export",
        )
        last_idx = -1
        for name in expected_subset:
            assert name in calls, (
                f"compose_application is missing the documented helper {name!r}; calls present: {calls}"
            )
            idx = calls.index(name, last_idx + 1)
            assert idx > last_idx, (
                f"compose_application call order changed: {name!r} should "
                f"appear after the previous helper but appears at {idx} "
                f"before position {last_idx}"
            )
            last_idx = idx
