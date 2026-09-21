"""Skill provider registry: origin classification and precedence resolution.

Classifies skills by *provider* — where they come from — and resolves
same-name candidates deterministically. The provider is orthogonal to the
ownership-mode ``source`` field (``system``/``user``/``project``/``plugin``)
and to the plugin-package provenance stored on ``SkillModel.origin``:

- ``bundled``  — platform-shipped skills (zero-UUID workspace).
- ``user``     — personal skills of a workspace member.
- ``project``  — skills shared across a workspace.
- ``custom``   — reserved for a future external-registry integration;
  never assignable today.

Plugin-sourced rows keep ``provider`` unset (``None``): they do not
compete in the rank game — ingestion rejects same-name collisions against
non-plugin skills, so a plugin row only ever resolves when it is the sole
candidate for its name.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

PROVIDER_BUNDLED = "bundled"
PROVIDER_USER = "user"
PROVIDER_PROJECT = "project"
PROVIDER_CUSTOM = "custom"

#: Providers that participate in rank precedence, ordered highest first.
RANKED_PROVIDERS: tuple[str, ...] = (PROVIDER_PROJECT, PROVIDER_USER, PROVIDER_BUNDLED)

#: Assignable providers at creation time; ``custom`` is reserved, plugin
#: rows are managed by the ingestion pipeline and never set provider.
ASSIGNABLE_PROVIDERS: frozenset[str] = frozenset({PROVIDER_BUNDLED, PROVIDER_USER, PROVIDER_PROJECT})

#: ``source`` value → ``provider`` classification. Sources absent from the
#: map (``plugin``, ``learned``) derive no provider.
SOURCE_TO_PROVIDER: dict[str, str] = {
    "system": PROVIDER_BUNDLED,
    "user": PROVIDER_USER,
    "project": PROVIDER_PROJECT,
}

#: Precedence rank per provider; lower wins. Unranked rows (plugin, drifted
#: data) sort after every ranked provider so they can only win by default.
_PROVIDER_RANK: dict[str | None, int] = {provider: rank for rank, provider in enumerate(RANKED_PROVIDERS)}
_UNRANKED_SENTINEL = len(RANKED_PROVIDERS)


def derive_provider(source: str) -> str | None:
    """Derive the provider classification from a skill's ``source`` value."""
    return SOURCE_TO_PROVIDER.get(source)


def resolve_by_precedence(rows: Iterable[Any]) -> Any | None:
    """Resolve same-name candidates to the highest-precedence row.

    Rows are ordered by provider rank (``project`` > ``user`` >
    ``bundled``); unranked rows (``provider=None``, i.e. plugin-sourced)
    sort last. The ordering is stable, so candidates of equal rank keep
    their input order and the winner never depends on query or storage
    order. Returns ``None`` for an empty candidate set.
    """
    ranked = sorted(
        rows,
        key=lambda row: _PROVIDER_RANK.get(getattr(row, "provider", None), _UNRANKED_SENTINEL),
    )
    return ranked[0] if ranked else None


def provider_inconsistencies(rows: Iterable[Any]) -> list[str]:
    """Return descriptions of rows whose provider/trust data violates the rules.

    Guards the persisted invariants: ``provider`` must equal the derivation
    from ``source`` (or be unset for unranked sources), plugin rows must not
    carry a provider, and bundled rows must be ``official``.
    """
    issues: list[str] = []
    for row in rows:
        source = getattr(row, "source", None)
        provider = getattr(row, "provider", None)
        expected = derive_provider(source) if source is not None else None
        if source in ("plugin", "learned"):
            if provider is not None:
                issues.append(
                    f"skill {getattr(row, 'id', '?')} (source={source}) must have provider unset, got {provider!r}"
                )
        elif provider != expected:
            issues.append(
                f"skill {getattr(row, 'id', '?')} (source={source}) provider {provider!r} != derived {expected!r}"
            )
        if provider == PROVIDER_BUNDLED and getattr(row, "trust_tier", None) != "official":
            issues.append(
                f"bundled skill {getattr(row, 'id', '?')} trust_tier {getattr(row, 'trust_tier', None)!r} != 'official'"
            )
    return issues


def resolve_precedence_map(rows: Sequence[Any]) -> dict[str, Any]:
    """Resolve a sequence of candidates per name into winners by name."""
    by_name: dict[str, list[Any]] = {}
    for row in rows:
        by_name.setdefault(row.name, []).append(row)
    return {name: resolve_by_precedence(candidates) for name, candidates in by_name.items()}
