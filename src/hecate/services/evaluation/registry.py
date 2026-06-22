"""Evaluator registry with decorator-based auto-registration.

Provides a central registry for all evaluators. Evaluators are registered
via the ``@register_evaluator`` decorator at import time. The ``evaluators/``
package ``__init__.py`` imports all evaluator modules to trigger registration.

Usage::

    from hecate.services.evaluation.registry import register_evaluator

    @register_evaluator("my_eval")
    class MyEvaluator(Evaluator):
        ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hecate.services.evaluation.evaluator import Evaluator

_REGISTRY: dict[str, type[Evaluator]] = {}


def register_evaluator(name: str) -> Callable[[type[Evaluator]], type[Evaluator]]:
    """Decorator that registers an evaluator class under the given name.

    Args:
        name: Unique identifier for the evaluator (e.g. ``"toxicity"``).

    Returns:
        Class decorator that registers and returns the class unchanged.
    """

    def decorator(cls: type[Evaluator]) -> type[Evaluator]:
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_evaluator(name: str) -> type[Evaluator] | None:
    """Look up a registered evaluator class by name.

    Args:
        name: Evaluator identifier.

    Returns:
        The evaluator class, or ``None`` if not found.
    """
    return _REGISTRY.get(name)


def list_evaluators(category: str | None = None) -> dict[str, type[Evaluator]]:
    """Return registered evaluators, optionally filtered by category.

    Args:
        category: If provided, only return evaluators whose ``category``
            attribute matches. If ``None``, return all.

    Returns:
        Dict mapping evaluator name to class.
    """
    if category is None:
        return dict(_REGISTRY)
    return {name: cls for name, cls in _REGISTRY.items() if getattr(cls, "category", "generic") == category}


def list_evaluator_names(category: str | None = None) -> list[str]:
    """Return sorted list of registered evaluator names.

    Args:
        category: Optional category filter.

    Returns:
        Alphabetically sorted list of evaluator names.
    """
    return sorted(list_evaluators(category).keys())
