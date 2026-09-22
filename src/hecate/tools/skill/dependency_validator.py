"""Skill dependency validator: graph validation for ``requires`` declarations.

A skill may declare a list of dependencies via ``requires`` (typically read
from SKILL.md frontmatter or Agent Plugins 1.0 ``plugin.json`` namespace).
This module enforces the structural invariants at authoring time so the
bind-time closure walk in :mod:`hecate.tools.skill.dependency_resolver`
can rely on the graph being well-formed.

Invariants enforced:

- **Existence** — every transitive dependency resolves to a live skill row
  in the same workspace.
- **Acyclicity** — the transitive closure contains no cycle.
- **Cross-source compatibility** — ``requires`` edges never cross between
  ``user`` / ``project`` / ``bundled` / ``plugin`` provider classes.
- **Shape** — every entry in ``requires`` is ``{"name": str[, "provider": str]}``
  with a kebab-case name and an optional provider from the assignable set.

The output is a list of :class:`DependencyError` records, each carrying a
machine-readable ``error_code`` and the dependency path that triggered it.
Callers (the skill CRUD API and the plugin ingestion pipeline) translate
the errors into HTTP 422 responses with structured payloads.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from hecate.tools.skill.provider_registry import (
    ASSIGNABLE_PROVIDERS,
    PROVIDER_BUNDLED,
    PROVIDER_PROJECT,
    PROVIDER_USER,
)

#: Kebab-case grammar shared with skill naming (``skill-provider-registry``).
_KEBAB_RE = re.compile(r"^[a-z][a-z0-9-]*$")

#: error_code enumeration — kept narrow so API clients can branch on it.
ERROR_INVALID_SYNTAX = "invalid_requires_syntax"
ERROR_SELF_REFERENCE = "self_reference"
ERROR_DEPENDENCY_MISSING = "dependency_missing"
ERROR_DEPENDENCY_CYCLE = "dependency_cycle"
ERROR_CROSS_SOURCE_DENIED = "cross_source_denied"
ERROR_DEPTH_EXCEEDED = "dependency_depth_exceeded"

#: Maximum transitive depth the validator will walk before refusing to
#: recurse further. Mirrors the resolver's depth limit so validation and
#: resolution agree on the upper bound.
MAX_DEPENDENCY_DEPTH = 32

#: Mapping from each skill source to the set of *provider* values its
#: ``requires`` edges may target. Read as: "a skill with source S may
#: declare ``requires`` pointing at a target whose provider is in this
#: set". Plugin-sourced skills (``source="plugin"``, ``provider=None``)
#: are excluded entirely — the spec forbids any non-plugin source from
#: requiring a plugin-source skill, and plugin-source skills themselves
#: declare their requires in ``plugin.json`` namespace, not frontmatter.
#: ``None`` source is reserved for malformed rows and never permits any
#: require.
SOURCE_ALLOWED_REQUIRE_TARGETS: dict[str | None, frozenset[str]] = {
    "user": frozenset({PROVIDER_USER, PROVIDER_BUNDLED}),
    "project": frozenset({PROVIDER_PROJECT, PROVIDER_USER, PROVIDER_BUNDLED}),
    "system": frozenset({PROVIDER_BUNDLED}),
    "plugin": frozenset({PROVIDER_BUNDLED}),
    "learned": frozenset({PROVIDER_USER, PROVIDER_PROJECT, PROVIDER_BUNDLED}),
    None: frozenset(),
}


@dataclass(frozen=True)
class DependencyError:
    """A single validation failure with a structured error code and path.

    Attributes:
        error_code: One of the ``ERROR_*`` constants — stable for API clients.
        dependency_path: Ordered list of skill names from the current skill
            outward through the chain that triggered the error. The first
            element is the skill being validated; the last is the node
            that failed. For self-reference the path has one element.
        message: Human-readable explanation suitable for surfacing in API
            error messages.
    """

    error_code: str
    dependency_path: list[str] = field(default_factory=list)
    message: str = ""


class SkillLookup(Protocol):
    """Protocol for the storage layer to query skill rows during validation.

    The validator does not depend on a concrete database; any object that
    resolves a (workspace_id, name, provider?) triple to a row exposing
    ``name``, ``provider``, ``requires`` (as a list of dicts) is sufficient.
    A missing target is signaled by returning ``None``.
    """

    def find_skill(
        self,
        workspace_id: Any,
        name: str,
        provider: str | None = ...,
    ) -> Any | None: ...


def _provider_of_source(source: str | None) -> str | None:
    """Map a skill's ``source`` field to its provider classification.

    Delegates to the provider registry's mapping (``system`` → ``bundled``,
    etc.). Plugin and learned rows return ``None`` and stay out of rank
    competition — but plugin rows do participate in cross-source checks.
    """
    if source is None:
        return None
    # Local import to avoid a circular dependency with skill model imports
    # when this module is used from CLI / agent-versioning contexts.
    from hecate.tools.skill.provider_registry import SOURCE_TO_PROVIDER

    return SOURCE_TO_PROVIDER.get(source)


def _coerce_entry(entry: Any) -> tuple[str, str | None] | None:
    """Normalize a ``requires`` entry to ``(name, provider)`` or reject it.

    Returns ``None`` when the entry is malformed (missing ``name``, wrong
    type, bad kebab-case, unknown provider). Callers translate ``None``
    into a structured error so the API layer can return 422.
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    provider = entry.get("provider")
    if not isinstance(name, str) or not _KEBAB_RE.match(name) or name == "":
        return None
    if provider is not None and (not isinstance(provider, str) or provider not in ASSIGNABLE_PROVIDERS):
        return None
    return name, provider


def check_shape(requires: Any) -> list[DependencyError]:
    """Validate the shape of a ``requires`` value without touching storage.

    Returns a list of ``DependencyError`` with ``error_code =
    invalid_requires_syntax`` for any malformed entry. Empty list on
    success. This is the only check that does not require a
    :class:`SkillLookup`.
    """
    errors: list[DependencyError] = []
    if requires is None:
        return errors
    if not isinstance(requires, list):
        errors.append(
            DependencyError(
                error_code=ERROR_INVALID_SYNTAX,
                dependency_path=[],
                message="requires must be a list of {name, provider?} entries",
            )
        )
        return errors
    for i, entry in enumerate(requires):
        if _coerce_entry(entry) is None:
            errors.append(
                DependencyError(
                    error_code=ERROR_INVALID_SYNTAX,
                    dependency_path=[f"<index {i}>"],
                    message=f"entry {i!r} is not a valid requires shape",
                )
            )
    return errors


def check_cross_source(
    requires: Sequence[dict[str, Any]],
    source: str | None,
) -> list[DependencyError]:
    """Validate that no ``requires`` edge crosses a forbidden source boundary.

    The matrix is :data:`SOURCE_ALLOWED_REQUIRE_TARGETS`. Plugin-sourced
    skills may only require bundled skills; user skills may require
    bundled or same-source user; etc. A bare-name ``requires`` entry
    (no explicit provider) is allowed — the resolver narrows it to a
    concrete provider at bind time under the current skill's allowed
    set. A require edge that names a non-bundled provider when the
    declaring skill is plugin-sourced is rejected here.
    """
    errors: list[DependencyError] = []
    allowed = SOURCE_ALLOWED_REQUIRE_TARGETS.get(source, frozenset())
    for entry in requires:
        coerced = _coerce_entry(entry)
        if coerced is None:
            continue  # shape error reported by check_shape
        name, provider = coerced
        if provider is not None and provider not in allowed:
            errors.append(
                DependencyError(
                    error_code=ERROR_CROSS_SOURCE_DENIED,
                    dependency_path=[name],
                    message=(f"skill with source {source!r} cannot require target provider {provider!r}"),
                )
            )
    return errors


def check_missing(
    skill_name: str,
    requires: Sequence[dict[str, Any]],
    workspace_id: Any,
    lookup: SkillLookup,
) -> list[DependencyError]:
    """Recursively check that every transitive dependency exists in this workspace.

    Walks the closure once, returning one :class:`DependencyError` per
    missing node. The traversal is depth-limited to
    :data:`MAX_DEPENDENCY_DEPTH`; over-depth yields a single
    ``dependency_depth_exceeded`` error rather than a stack overflow.
    """
    errors: list[DependencyError] = []
    seen: set[tuple[str, str | None]] = set()
    # Use a stack of (name, path_so_far) frames to avoid recursion overhead.
    stack: list[tuple[str, list[str]]] = [(skill_name, [skill_name])]
    while stack:
        current, path = stack.pop()
        if current == skill_name and len(path) > 1:
            continue  # root revisit — normal for shared closure nodes
        row = lookup.find_skill(workspace_id, current)
        if row is None:
            errors.append(
                DependencyError(
                    error_code=ERROR_DEPENDENCY_MISSING,
                    dependency_path=list(path),
                    message=f"skill {current!r} not found in workspace",
                )
            )
            continue
        nested_requires = _row_requires(row)
        for entry in nested_requires:
            coerced = _coerce_entry(entry)
            if coerced is None:
                continue
            nested_name, nested_provider = coerced
            visit_key = (nested_name, nested_provider)
            if visit_key in seen:
                continue
            seen.add(visit_key)
            if nested_name == skill_name:
                errors.append(
                    DependencyError(
                        error_code=ERROR_SELF_REFERENCE,
                        dependency_path=list(path) + [nested_name],
                        message="skill requires itself",
                    )
                )
                continue
            if len(path) >= MAX_DEPENDENCY_DEPTH:
                errors.append(
                    DependencyError(
                        error_code=ERROR_DEPTH_EXCEEDED,
                        dependency_path=list(path) + [nested_name],
                        message=(
                            f"dependency depth exceeds {MAX_DEPENDENCY_DEPTH}; "
                            f"refactor the requires graph to be shallower"
                        ),
                    )
                )
                continue
            stack.append((nested_name, path + [nested_name]))
    return errors


def check_cycles(
    skill_name: str,
    requires: Sequence[dict[str, Any]],
    workspace_id: Any,
    lookup: SkillLookup,
) -> list[DependencyError]:
    """Detect cycles in the transitive closure reachable from ``skill_name``.

    Uses iterative DFS with three-color marking (WHITE / GRAY / BLACK) so
    the detection runs in O(V + E). A cycle is reported once per cycle
    found, with the path that closes it.
    """
    # Three-color DFS marks. Lowercase ``white/gray/black`` keeps ruff
    # N806 happy; the values are sentinels only, never used as data.
    white, gray, black = 0, 1, 2
    color: dict[str, int] = {}
    path_stack: list[str] = []
    path_set: set[str] = set()
    errors: list[DependencyError] = []
    reported_cycles: set[tuple[str, ...]] = set()

    # Initialize colors from the lookup so cross-workspace rows that the
    # caller has not pre-loaded still resolve correctly. Rows that fail
    # lookup are treated as WHITE — they will be caught by check_missing
    # rather than misreported here as a cycle edge.
    def _colorize(name: str) -> None:
        row = lookup.find_skill(workspace_id, name)
        if row is None:
            color[name] = white
            return
        color.setdefault(name, white)
        for entry in _row_requires(row):
            coerced = _coerce_entry(entry)
            if coerced is None:
                continue
            nested_name, _ = coerced
            if nested_name not in color:
                _colorize(nested_name)

    _colorize(skill_name)

    def _dfs(name: str) -> None:
        if color.get(name) == black:
            return
        if color.get(name) == gray:
            # Find the start of the cycle in path_stack and report it.
            idx = path_stack.index(name)
            cycle_path = tuple(path_stack[idx:] + [name])
            if cycle_path not in reported_cycles:
                reported_cycles.add(cycle_path)
                # A self-loop (length-2 cycle with the same name twice) is
                # the same defect as a self-reference; report that error
                # code instead so clients only need to handle one of them.
                error_code = ERROR_SELF_REFERENCE if len(cycle_path) == 2 else ERROR_DEPENDENCY_CYCLE
                errors.append(
                    DependencyError(
                        error_code=error_code,
                        dependency_path=list(cycle_path),
                        message=f"cycle detected: {' -> '.join(cycle_path)}",
                    )
                )
            return
        color[name] = gray
        path_stack.append(name)
        path_set.add(name)
        row = lookup.find_skill(workspace_id, name)
        if row is not None:
            for entry in _row_requires(row):
                coerced = _coerce_entry(entry)
                if coerced is None:
                    continue
                nested_name, _ = coerced
                _dfs(nested_name)
        path_stack.pop()
        path_set.discard(name)
        color[name] = black

    _dfs(skill_name)
    return errors


def validate_requires(
    skill_name: str,
    source: str | None,
    requires: Any,
    workspace_id: Any,
    lookup: SkillLookup,
) -> list[DependencyError]:
    """Validate a ``requires`` declaration at authoring time.

    Aggregates the four checks in the canonical order: shape → cross-source
    → cycles → missing. Each check is independent; the function returns
    *all* failures it finds rather than stopping at the first, so API
    clients can render the full picture to the user in one round trip.

    Args:
        skill_name: The name of the skill being created or updated. Used
            as the cycle-detection root and the self-reference target.
        source: The skill's source (``"user"`` / ``"project"`` /
            ``"system"`` / ``"plugin"`` / ``"learned"``). Drives the
            cross-source matrix.
        requires: The untrusted ``requires`` value from the request body.
            May be ``None``, a list of dicts, or malformed.
        workspace_id: The owning workspace identifier; passed through to
            the storage layer for existence checks.
        lookup: A :class:`SkillLookup` that resolves names to skill rows.

    Returns:
        Empty list on success; a list of :class:`DependencyError`
        otherwise. Each error carries the chain that produced it.
    """
    errors: list[DependencyError] = []
    errors.extend(check_shape(requires))
    if errors:
        # Don't bother with storage-touching checks if the shape itself is
        # broken — the user has more pressing concerns than graph traversal.
        return errors

    # Coerce once for downstream checks; ignore malformed entries that
    # already produced a shape error above.
    coerced_requires = [c for c in (_coerce_entry(e) for e in requires) if c is not None]
    normalized: list[dict[str, Any]] = [
        {"name": name, **({"provider": provider} if provider else {})} for name, provider in coerced_requires
    ]

    errors.extend(check_cross_source(normalized, source))

    # Cycle and missing checks touch storage; both share a single traversal
    # for efficiency — the cycle DFS resolves each name once. We dedupe by
    # (error_code, dependency_path) so the same defect doesn't surface as
    # two errors from two passes (e.g. self-reference showing up once in
    # the local self-ref check, once in the cycle DFS, once in the
    # missing DFS).
    seen_keys: set[tuple[str, tuple[str, ...]]] = set()

    def _record(new_errors: Iterable[DependencyError]) -> None:
        for err in new_errors:
            key = (err.error_code, tuple(err.dependency_path))
            if key not in seen_keys:
                seen_keys.add(key)
                errors.append(err)

    _record(check_cycles(skill_name, normalized, workspace_id, lookup))
    _record(check_missing(skill_name, normalized, workspace_id, lookup))

    return errors


def _row_requires(row: Any) -> list[dict[str, Any]]:
    """Return the requires list from a skill row, treating ``None`` as ``[]``."""
    raw = getattr(row, "requires", None) or []
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def sources_for_provider(provider: str | None) -> frozenset[str | None]:
    """Return the set of source values that map to a given provider.

    Used by callers that need to enumerate which source values a
    particular provider setting covers (e.g. "all plugin-sourced skills
    that map to bundled"). Mirrors :data:`SOURCE_TO_PROVIDER`.
    """
    if provider is None:
        return frozenset({"plugin", "learned"})
    if provider == PROVIDER_BUNDLED:
        return frozenset({"system"})
    if provider == PROVIDER_USER:
        return frozenset({"user"})
    if provider == PROVIDER_PROJECT:
        return frozenset({"project"})
    return frozenset()


__all__ = [
    "DependencyError",
    "SkillLookup",
    "validate_requires",
    "check_shape",
    "check_cross_source",
    "check_missing",
    "check_cycles",
    "MAX_DEPENDENCY_DEPTH",
    "ERROR_INVALID_SYNTAX",
    "ERROR_SELF_REFERENCE",
    "ERROR_DEPENDENCY_MISSING",
    "ERROR_DEPENDENCY_CYCLE",
    "ERROR_CROSS_SOURCE_DENIED",
    "ERROR_DEPTH_EXCEEDED",
    "SOURCE_ALLOWED_REQUIRE_TARGETS",
    "sources_for_provider",
]
