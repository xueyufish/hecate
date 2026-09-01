"""LLM-layer litellm-scoping invariant test (PR4a).

Pin the AGENTS.md + research-doc rule that ``import litellm`` and
``from litellm import ...`` lines must exist in exactly one module:
``src/hecate/services/llm/``. We statically scan ``src/`` and
``packages/`` for any such import plus any ``litellm.<attr>`` reference
(``acompletion``, ``get_valid_models``, ...), and assert each hit lives
under the gateway module.

Companion to ``tests/test_engine/test_layering.py`` which uses the same
static-scan technique for engine→services rules. We use ``grep -rE``
via subprocess instead of AST for cross-platform robustness (BSD grep
on macOS ships with -E).

What this test prevents from regressing:
- New leak sites outside services/llm/ (PR4a explicitly fixed 3 of these)
- Engine / package / model_hub / api paths re-introducing litellm
- A future move of LLMService accidentally leaving litellm imports behind
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "hecate"
PACKAGES_ROOT = REPO_ROOT / "packages"
GATEWAY_DIR = SRC_ROOT / "services" / "llm"

# Two complementary grep patterns:
# 1. module-level litellm imports (the strict acceptance test)
# 2. any litellm.<attr> reference (catches lazy imports inside functions)
LITELLM_IMPORT_PATTERNS: tuple[str, ...] = (
    r"^\s*import\s+litellm\b",
    r"^\s*from\s+litellm\b",
)
LITELLM_ATTR_PATTERN = r"\blitellm\.[a-z_]+\b"


def _grep(pattern: str, roots: list[Path], extra_args: list[str] | None = None) -> list[Path]:
    """Return files matching ``pattern`` under any root. Sorted, deterministic."""
    args = ["grep", "-rE", "-l", pattern]
    if extra_args:
        args.extend(extra_args)
    args.extend(str(r) for r in roots)
    result = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
    files: list[Path] = []
    for line in result.stdout.splitlines():
        path = Path(line)
        if path.is_absolute():
            files.append(path)
        else:
            files.append(REPO_ROOT / line)
    return sorted(set(files))


class TestLitellmScopedToGateway:
    """The PR4a acceptance: litellm lives only under services/llm/."""

    def test_litellm_imports_only_in_services_llm(self) -> None:
        """No `import litellm` or `from litellm` exists outside the gateway module."""
        # First find ALL litellm import hits (any root).
        all_hits = _grep(r"^\s*(import|from)\s+litellm\b", [SRC_ROOT, PACKAGES_ROOT])
        # Filter to absolute paths and exclude __pycache__ / pyc.
        all_hits = [p for p in all_hits if "__pycache__" not in p.parts and p.suffix == ".py"]
        # The only acceptable site is services/llm/{service,gateway}.py.
        bad = [p for p in all_hits if not str(p).startswith(str(GATEWAY_DIR) + str(Path("/")))]
        assert not bad, (
            "litellm imports must live under src/hecate/services/llm/. "
            f"Found leaks at: {[str(p.relative_to(REPO_ROOT)) for p in bad]}"
        )

    def test_services_llm_has_exactly_one_or_two_litellm_importers(self) -> None:
        """The gateway module owns the import — between 1 and 2 files (service + gateway)."""
        hits = _grep(r"^\s*(import|from)\s+litellm\b", [GATEWAY_DIR])
        hits = [p for p in hits if p.suffix == ".py"]
        assert 1 <= len(hits) <= 2, (
            f"Expected 1-2 litellm importers under services/llm/, found {len(hits)}: {[p.name for p in hits]}"
        )

    def test_no_litellm_attribute_calls_outside_services_llm(self) -> None:
        """No `litellm.completion` / `litellm.get_valid_models` / etc. outside gateway."""
        hits = _grep(LITELLM_ATTR_PATTERN, [SRC_ROOT, PACKAGES_ROOT])
        hits = [p for p in hits if p.suffix == ".py" and "__pycache__" not in p.parts]
        bad = [p for p in hits if not str(p).startswith(str(GATEWAY_DIR) + str(Path("/")))]
        assert not bad, (
            "litellm.<attr> calls must live under src/hecate/services/llm/. "
            f"Found leaks at: {[str(p.relative_to(REPO_ROOT)) for p in bad]}"
        )


class TestEngineNeverImportsLitellm:
    """The engine sees only RuntimePort.llm_invoke — never litellm."""

    def test_engine_tree_free_of_litellm(self) -> None:
        engine_dir = SRC_ROOT / "engine"
        hits = _grep(LITELLM_ATTR_PATTERN, [engine_dir])
        hits = [p for p in hits if p.suffix == ".py"]
        assert not hits, f"engine/ must never reference litellm; found: {[str(h.relative_to(REPO_ROOT)) for h in hits]}"

    def test_engine_tree_free_of_litellm_imports(self) -> None:
        engine_dir = SRC_ROOT / "engine"
        hits = _grep(r"^\s*(import|from)\s+litellm\b", [engine_dir])
        hits = [p for p in hits if p.suffix == ".py"]
        assert not hits, f"engine/ must never import litellm; found: {[str(h.relative_to(REPO_ROOT)) for h in hits]}"


class TestModelHubStaysLitellmFree:
    """The management plane stays out of the gateway's litellm surface (PR4b territory)."""

    def test_model_hub_has_no_litellm(self) -> None:
        model_hub_dir = SRC_ROOT / "model_hub"
        hits = _grep(r"^\s*(import|from)\s+litellm\b", [model_hub_dir])
        hits = [p for p in hits if p.suffix == ".py"]
        assert not hits, (
            f"model_hub/ must not import litellm (PR4a converged them apart); "
            f"found: {[str(h.relative_to(REPO_ROOT)) for h in hits]}"
        )


class TestPackagesLitellmFree:
    """Extracted packages stay litellm-free until PR4b brings hecate-llm online."""

    def test_packages_no_litellm(self) -> None:
        if not PACKAGES_ROOT.exists():
            pytest.skip("packages/ not present (workspace not initialised)")
        hits = _grep(r"^\s*(import|from)\s+litellm\b", [PACKAGES_ROOT])
        hits = [p for p in hits if p.suffix == ".py"]
        assert not hits, (
            f"packages/ must not import litellm until PR4b; found: {[str(h.relative_to(REPO_ROOT)) for h in hits]}"
        )
