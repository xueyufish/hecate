"""Tests for the evaluators registry surface.

``GET /api/evaluation/evaluators`` and its scope metadata table are part
of the retained evaluation API. These tests were relocated here when the
dataset-synthesis module was retired (M1-T1).
"""

from __future__ import annotations


class TestEvaluatorsListEndpoint:
    async def test_list_evaluators_returns_16(self, client: object) -> None:
        """Spec invariant: GET /api/evaluation/evaluators returns 16."""
        response = await client.get("/api/evaluation/evaluators")  # type: ignore[union-attr]
        assert response.status_code == 200
        data = response.json()
        # 16 - 4 ragas optional = 12 if ragas missing; but in this env
        # ragas is installed so should be 16
        assert data["total"] == 16
        names = {it["name"] for it in data["items"]}
        for expected in (
            "correctness",
            "contains",
            "refusal",
            "harmfulness",
            "pii_leakage",
            "tool_call_accuracy",
        ):
            assert expected in names

    async def test_filter_by_scope(self, client: object) -> None:
        response = await client.get(  # type: ignore[union-attr]
            "/api/evaluation/evaluators?scope=safety"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        for item in data["items"]:
            assert item["scope"] == "safety"
        names = {it["name"] for it in data["items"]}
        assert names == {"refusal", "harmfulness", "pii_leakage"}


class TestEvaluatorsListMetadata:
    """Spec invariant: list_evaluators returns 16 (12 always + 4 ragas-optional)."""

    def test_metadata_table_has_16_entries(self) -> None:
        from hecate.ops.api.evaluation import _EVALUATOR_SCOPE_META

        assert len(_EVALUATOR_SCOPE_META) == 16

        by_scope: dict[str, int] = {}
        for _name, (scope, _source) in _EVALUATOR_SCOPE_META.items():
            by_scope[scope] = by_scope.get(scope, 0) + 1
        # Spec taxonomy: result=7, process=2, rag=4, safety=3 → 16
        assert by_scope["result"] == 7
        assert by_scope["process"] == 2
        assert by_scope["rag"] == 4
        assert by_scope["safety"] == 3
