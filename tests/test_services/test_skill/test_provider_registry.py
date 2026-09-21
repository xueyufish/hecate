"""Tests for the skill provider registry resolution primitives."""

from __future__ import annotations

from dataclasses import dataclass

from hecate.tools.skill.provider_registry import (
    ASSIGNABLE_PROVIDERS,
    PROVIDER_BUNDLED,
    PROVIDER_CUSTOM,
    PROVIDER_PROJECT,
    PROVIDER_USER,
    derive_provider,
    resolve_by_precedence,
    resolve_precedence_map,
)


@dataclass
class _Row:
    """Minimal stand-in exposing the attributes resolution reads."""

    name: str
    provider: str | None


class TestDeriveProvider:
    def test_system_maps_to_bundled(self) -> None:
        assert derive_provider("system") == PROVIDER_BUNDLED

    def test_user_and_project_map_identity(self) -> None:
        assert derive_provider("user") == PROVIDER_USER
        assert derive_provider("project") == PROVIDER_PROJECT

    def test_plugin_and_learned_derive_no_provider(self) -> None:
        assert derive_provider("plugin") is None
        assert derive_provider("learned") is None


class TestResolveByPrecedence:
    def test_empty_candidates_return_none(self) -> None:
        assert resolve_by_precedence([]) is None

    def test_project_beats_user_and_bundled(self) -> None:
        bundled = _Row("pdf-report", PROVIDER_BUNDLED)
        user = _Row("pdf-report", PROVIDER_USER)
        project = _Row("pdf-report", PROVIDER_PROJECT)
        assert resolve_by_precedence([bundled, user, project]) is project

    def test_user_beats_bundled(self) -> None:
        bundled = _Row("summarize", PROVIDER_BUNDLED)
        user = _Row("summarize", PROVIDER_USER)
        assert resolve_by_precedence([bundled, user]) is user

    def test_bundled_served_when_sole_candidate(self) -> None:
        bundled = _Row("translate", PROVIDER_BUNDLED)
        assert resolve_by_precedence([bundled]) is bundled

    def test_unranked_plugin_row_loses_to_any_ranked_row(self) -> None:
        plugin = _Row("deploy", None)
        bundled = _Row("deploy", PROVIDER_BUNDLED)
        assert resolve_by_precedence([plugin, bundled]) is bundled

    def test_unranked_plugin_row_served_when_sole_candidate(self) -> None:
        plugin = _Row("deploy", None)
        assert resolve_by_precedence([plugin]) is plugin

    def test_resolution_is_independent_of_input_order(self) -> None:
        bundled = _Row("audit", PROVIDER_BUNDLED)
        user = _Row("audit", PROVIDER_USER)
        project = _Row("audit", PROVIDER_PROJECT)
        candidates = [bundled, user, project]
        expected = resolve_by_precedence(candidates)
        for permutation in (
            [project, user, bundled],
            [user, bundled, project],
            [project, bundled, user],
            [bundled, project, user],
        ):
            assert resolve_by_precedence(permutation) is expected


class TestResolvePrecedenceMap:
    def test_names_resolve_independently(self) -> None:
        user_pdf = _Row("pdf-report", PROVIDER_USER)
        project_pdf = _Row("pdf-report", PROVIDER_PROJECT)
        bundled_translate = _Row("translate", PROVIDER_BUNDLED)
        winners = resolve_precedence_map([user_pdf, project_pdf, bundled_translate])
        assert winners["pdf-report"] is project_pdf
        assert winners["translate"] is bundled_translate


class TestProviderConstants:
    def test_custom_is_not_assignable(self) -> None:
        assert PROVIDER_CUSTOM not in ASSIGNABLE_PROVIDERS

    def test_rank_order_is_project_user_bundled(self) -> None:
        from hecate.tools.skill.provider_registry import RANKED_PROVIDERS

        assert RANKED_PROVIDERS == (PROVIDER_PROJECT, PROVIDER_USER, PROVIDER_BUNDLED)
