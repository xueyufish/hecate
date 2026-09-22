"""Unit tests for the skill dependency declaration / resolver modules.

Coverage of the 5.9e validator / resolver pipeline. Uses an in-memory
``SkillLookup`` adapter to keep the validator pure-Python and free of
database fixtures; integration with SQLAlchemy is exercised by the API
tests under ``tests/test_api``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from hecate.tools.skill.dependency_validator import (
    ERROR_CROSS_SOURCE_DENIED,
    ERROR_DEPENDENCY_CYCLE,
    ERROR_DEPENDENCY_MISSING,
    ERROR_INVALID_SYNTAX,
    ERROR_SELF_REFERENCE,
    MAX_DEPENDENCY_DEPTH,
    SOURCE_ALLOWED_REQUIRE_TARGETS,
    check_cross_source,
    check_cycles,
    check_missing,
    check_shape,
    validate_requires,
)


@dataclass
class FakeSkill:
    name: str
    provider: str | None
    source: str = "user"
    requires: list = field(default_factory=list)


class FakeLookup:
    def __init__(self, rows: list[FakeSkill]):
        self.rows = {r.name: r for r in rows}

    def find_skill(self, ws: Any, name: str, provider: str | None = None):
        return self.rows.get(name)


# --- shape ------------------------------------------------------------------


def test_check_shape_accepts_none() -> None:
    assert check_shape(None) == []


def test_check_shape_accepts_empty_list() -> None:
    assert check_shape([]) == []


def test_check_shape_accepts_valid_entry() -> None:
    errs = check_shape([{"name": "pdf-utils"}])
    assert errs == []


def test_check_shape_accepts_entry_with_provider() -> None:
    errs = check_shape([{"name": "pdf-utils", "provider": "bundled"}])
    assert errs == []


def test_check_shape_rejects_non_list() -> None:
    errs = check_shape("not a list")
    assert len(errs) == 1
    assert errs[0].error_code == ERROR_INVALID_SYNTAX


def test_check_shape_rejects_uppercase_name() -> None:
    errs = check_shape([{"name": "Pdf-Utils"}])
    assert len(errs) == 1
    assert errs[0].error_code == ERROR_INVALID_SYNTAX


def test_check_shape_rejects_unknown_provider() -> None:
    errs = check_shape([{"name": "pdf-utils", "provider": "external"}])
    assert len(errs) == 1
    assert errs[0].error_code == ERROR_INVALID_SYNTAX


# --- cross-source -----------------------------------------------------------


def test_cross_source_user_can_require_user() -> None:
    assert check_cross_source([{"name": "p", "provider": "user"}], "user") == []


def test_cross_source_user_can_require_bundled() -> None:
    assert check_cross_source([{"name": "p", "provider": "bundled"}], "user") == []


def test_cross_source_user_cannot_require_project() -> None:
    errs = check_cross_source([{"name": "p", "provider": "project"}], "user")
    assert len(errs) == 1
    assert errs[0].error_code == ERROR_CROSS_SOURCE_DENIED


def test_cross_source_plugin_only_requires_bundled() -> None:
    assert check_cross_source([{"name": "p", "provider": "bundled"}], "plugin") == []
    errs = check_cross_source([{"name": "p", "provider": "project"}], "plugin")
    assert errs and errs[0].error_code == ERROR_CROSS_SOURCE_DENIED


def test_cross_source_bundled_only_requires_bundled() -> None:
    assert check_cross_source([{"name": "p", "provider": "bundled"}], "system") == []
    errs = check_cross_source([{"name": "p", "provider": "user"}], "system")
    assert errs and errs[0].error_code == ERROR_CROSS_SOURCE_DENIED


def test_cross_source_allows_bare_name() -> None:
    """Bare-name requires have no explicit provider — defer to bind-time."""
    assert check_cross_source([{"name": "p"}], "user") == []


# --- cycles -----------------------------------------------------------------


def test_cycle_two_node() -> None:
    """a -> b -> a."""
    rows = [
        FakeSkill("a", "user", requires=[{"name": "b"}]),
        FakeSkill("b", "user", requires=[{"name": "a"}]),
    ]
    errs = check_cycles("a", [{"name": "b"}], "ws", FakeLookup(rows))
    assert any(e.error_code == ERROR_DEPENDENCY_CYCLE for e in errs)
    cycle = next(e for e in errs if e.error_code == ERROR_DEPENDENCY_CYCLE)
    assert cycle.dependency_path == ["a", "b", "a"]


def test_cycle_three_node() -> None:
    rows = [
        FakeSkill("a", "user", requires=[{"name": "b"}]),
        FakeSkill("b", "user", requires=[{"name": "c"}]),
        FakeSkill("c", "user", requires=[{"name": "a"}]),
    ]
    errs = check_cycles("a", [{"name": "b"}], "ws", FakeLookup(rows))
    cycle = next(e for e in errs if e.error_code == ERROR_DEPENDENCY_CYCLE)
    assert cycle.dependency_path == ["a", "b", "c", "a"]


def test_self_reference_is_self_reference_not_cycle() -> None:
    """A self-loop is reported as ``self_reference`` rather than a cycle."""
    rows = [FakeSkill("a", "user", requires=[{"name": "a"}])]
    errs = check_cycles("a", [{"name": "a"}], "ws", FakeLookup(rows))
    assert all(e.error_code == ERROR_SELF_REFERENCE for e in errs)


# --- missing ----------------------------------------------------------------


def test_missing_direct() -> None:
    errs = check_missing("a", [{"name": "missing"}], "ws", FakeLookup([]))
    assert len(errs) == 1
    assert errs[0].error_code == ERROR_DEPENDENCY_MISSING


def test_missing_transitive() -> None:
    rows = [
        FakeSkill("a", "user", requires=[{"name": "b"}]),
        FakeSkill("b", "user", requires=[{"name": "missing"}]),
    ]
    errs = check_missing("a", [{"name": "b"}], "ws", FakeLookup(rows))
    miss = next(e for e in errs if e.error_code == ERROR_DEPENDENCY_MISSING)
    assert "missing" in miss.dependency_path


# --- validate_requires (unified entry) --------------------------------------


def test_validate_happy_path() -> None:
    rows = [
        FakeSkill("a", "user", requires=[{"name": "b"}]),
        FakeSkill("b", "user", requires=[]),
    ]
    errs = validate_requires("a", "user", [{"name": "b"}], "ws", FakeLookup(rows))
    assert errs == []


def test_validate_aggregates_errors() -> None:
    """Cross-source + missing surface together when shape is clean."""
    rows = [FakeSkill("a", "user", requires=[{"name": "p", "provider": "project"}])]
    errs = validate_requires(
        "a",
        "user",
        [
            {"name": "p", "provider": "project"},  # cross-source
            {"name": "missing-skill"},  # missing
        ],
        "ws",
        FakeLookup(rows),
    )
    codes = {e.error_code for e in errs}
    assert ERROR_CROSS_SOURCE_DENIED in codes
    assert ERROR_DEPENDENCY_MISSING in codes


def test_validate_short_circuits_on_shape_error() -> None:
    """Shape errors block storage-touching checks (per validate_requires design)."""
    errs = validate_requires(
        "a",
        "user",
        [{"name": "BAD!"}, {"name": "p", "provider": "project"}],
        "ws",
        FakeLookup([]),
    )
    codes = {e.error_code for e in errs}
    # Only the shape error is reported; cross-source is intentionally
    # deferred until the shape is fixed (user can't reason about graph
    # state while structural errors remain).
    assert ERROR_INVALID_SYNTAX in codes
    assert ERROR_CROSS_SOURCE_DENIED not in codes


def test_validate_dedupes_self_reference() -> None:
    """Self-ref fires once even though cycle + missing DFS could re-detect."""
    rows = [FakeSkill("a", "user", requires=[{"name": "a"}])]
    errs = validate_requires("a", "user", [{"name": "a"}], "ws", FakeLookup(rows))
    assert [e.error_code for e in errs] == [ERROR_SELF_REFERENCE]


def test_max_depth_constant_matches_spec() -> None:
    assert MAX_DEPENDENCY_DEPTH == 32


# --- source matrix coverage ------------------------------------------------


@pytest.mark.parametrize(
    "source,allowed",
    [
        ("user", {"user", "bundled"}),
        ("project", {"user", "bundled", "project"}),
        ("system", {"bundled"}),
        ("plugin", {"bundled"}),
    ],
)
def test_source_allowed_require_targets_matrix(source: str, allowed: set[str]) -> None:
    assert SOURCE_ALLOWED_REQUIRE_TARGETS[source] == allowed
