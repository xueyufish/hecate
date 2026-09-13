"""API tests for publish evaluation gate + named dataset versions (7.3a + 7.3b).

Covers the version endpoints (create/list/get/delete/checkout/diff) and
the publish endpoint gate contract (off = unchanged, warn = 200 with
report, require = 409 EVALUATION_GATE_BLOCKED, force bypass = 200 with
bypassed marker).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

VALID_DSL = {
    "version": "1.0",
    "name": "test",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
    "edges": [],
    "entry": "A",
}


async def _create_dataset(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/evaluation/datasets",
        json={"name": f"ds-{uuid.uuid4()}"},
    )
    assert resp.status_code == 201
    return resp.json()


async def _seed_items(client: AsyncClient, dataset_id: str, *, count: int = 3) -> None:
    items = [{"query": f"q{i}", "expected_answer": f"a{i}"} for i in range(count)]
    resp = await client.post(f"/api/evaluation/datasets/{dataset_id}/items", json=items)
    assert resp.status_code in (200, 201)


async def _create_workflow(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/workflows",
        json={
            "name": f"wf-{uuid.uuid4()}",
            "graph_dsl": VALID_DSL,
            "execution_mode": "task",
        },
    )
    assert resp.status_code == 201
    return resp.json()


class TestDatasetVersionEndpoints:
    @pytest.mark.asyncio
    async def test_create_then_get_then_list(self, client: AsyncClient) -> None:
        ds = await _create_dataset(client)
        await _seed_items(client, ds["id"], count=2)

        create = await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["name"] == "v1"
        assert body["dataset_id"] == ds["id"]
        assert len(body["items"]) == 2
        assert len(body["content_hash"]) == 64

        get = await client.get(f"/api/evaluation/datasets/{ds['id']}/versions/{body['id']}")
        assert get.status_code == 200
        assert get.json()["id"] == body["id"]

        listing = await client.get(f"/api/evaluation/datasets/{ds['id']}/versions")
        assert listing.status_code == 200
        listing_body = listing.json()
        assert listing_body["total"] == 1
        # List endpoint strips items to keep responses compact.
        assert "items" not in listing_body["items"][0]

    @pytest.mark.asyncio
    async def test_duplicate_name_409(self, client: AsyncClient) -> None:
        ds = await _create_dataset(client)
        await _seed_items(client, ds["id"], count=1)
        first = await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})
        assert first.status_code == 201
        second = await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})
        assert second.status_code == 409
        assert second.json()["detail"]["error"]["code"] == "NAME_CONFLICT"

    @pytest.mark.asyncio
    async def test_unknown_dataset_404(self, client: AsyncClient) -> None:
        resp = await client.post(f"/api/evaluation/datasets/{uuid.uuid4()}/versions", json={"name": "v1"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_soft_deleted_version_404_and_name_reserved(self, client: AsyncClient) -> None:
        ds = await _create_dataset(client)
        await _seed_items(client, ds["id"], count=1)
        v = await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})
        assert v.status_code == 201
        version_id = v.json()["id"]

        delete = await client.delete(f"/api/evaluation/datasets/{ds['id']}/versions/{version_id}")
        assert delete.status_code == 204

        get = await client.get(f"/api/evaluation/datasets/{ds['id']}/versions/{version_id}")
        assert get.status_code == 404

        retry = await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})
        assert retry.status_code == 409

    @pytest.mark.asyncio
    async def test_checkout_replaces_live_items(self, client: AsyncClient) -> None:
        ds = await _create_dataset(client)
        await _seed_items(client, ds["id"], count=2)
        version = (await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})).json()

        # Mutate live then checkout — original 2 should return.
        listing = (await client.get(f"/api/evaluation/datasets/{ds['id']}/items")).json()
        target_id = listing["items"][0]["id"]
        await client.delete(f"/api/evaluation/datasets/{ds['id']}/items/{target_id}")

        checkout = await client.post(f"/api/evaluation/datasets/{ds['id']}/versions/{version['id']}/checkout")
        assert checkout.status_code == 200
        body = checkout.json()
        assert body["live_items_after"] == 2

        after = (await client.get(f"/api/evaluation/datasets/{ds['id']}/items")).json()
        assert {item["query"] for item in after["items"]} == {"q0", "q1"}

    @pytest.mark.asyncio
    async def test_diff_against_live(self, client: AsyncClient) -> None:
        ds = await _create_dataset(client)
        await _seed_items(client, ds["id"], count=2)
        version = (await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})).json()

        diff = await client.get(f"/api/evaluation/datasets/{ds['id']}/versions/{version['id']}/diff?against=live")
        assert diff.status_code == 200
        body = diff.json()
        assert body["base"]["name"] == "v1"
        assert body["target"]["kind"] == "live"

    @pytest.mark.asyncio
    async def test_diff_against_invalid_against_422(self, client: AsyncClient) -> None:
        ds = await _create_dataset(client)
        await _seed_items(client, ds["id"], count=1)
        version = (await client.post(f"/api/evaluation/datasets/{ds['id']}/versions", json={"name": "v1"})).json()
        resp = await client.get(f"/api/evaluation/datasets/{ds['id']}/versions/{version['id']}/diff?against=garbage")
        assert resp.status_code == 422


class TestPublishEvaluationGateEndpoint:
    @pytest.mark.asyncio
    async def test_off_mode_preserves_existing_publish_contract(self, client: AsyncClient) -> None:
        wf = await _create_workflow(client)
        publish = await client.post(f"/api/workflows/{wf['id']}/publish/1")
        assert publish.status_code == 200
        body = publish.json()
        # No gate configured → no gate block in the report.
        report = body.get("evaluation_report") or {}
        assert "gate" not in report

    @pytest.mark.asyncio
    async def test_warn_mode_publishes_and_includes_gate(self, client: AsyncClient, db_session) -> None:

        from hecate.models.evaluation import EvaluationRunModel, RunStatus

        wf = await _create_workflow(client)
        patch = await client.put(
            f"/api/workflows/{wf['id']}",
            json={"evaluation_gate": {"mode": "warn", "min_pass_rate": 0.9}},
        )
        assert patch.status_code == 200

        # Seed a completed run so the publish-time report actually computes the gate.
        run = EvaluationRunModel(
            dataset_id=uuid.uuid4(),
            workflow_id=uuid.UUID(wf["id"]),
            workflow_version=1,
            status=RunStatus.COMPLETED.value,
            summary={"threshold": 0.5},
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        )
        db_session.add(run)
        await db_session.flush()

        publish = await client.post(f"/api/workflows/{wf['id']}/publish/1")
        assert publish.status_code == 200
        report = publish.json().get("evaluation_report") or {}
        assert "gate" in report
        assert report["gate"]["mode"] == "warn"
        assert report["gate"]["bypassed_by_force"] is False

    @pytest.mark.asyncio
    async def test_require_mode_no_signal_rejected_at_config_time(self, client: AsyncClient) -> None:
        wf = await _create_workflow(client)
        patch = await client.put(
            f"/api/workflows/{wf['id']}",
            json={"evaluation_gate": {"mode": "require"}},
        )
        assert patch.status_code == 422

    @pytest.mark.asyncio
    async def test_require_mode_min_pass_rate_out_of_range(self, client: AsyncClient) -> None:
        wf = await _create_workflow(client)
        patch = await client.put(
            f"/api/workflows/{wf['id']}",
            json={"evaluation_gate": {"mode": "require", "min_pass_rate": 1.5}},
        )
        assert patch.status_code == 422

    @pytest.mark.asyncio
    async def test_require_mode_publish_with_no_completed_run_returns_409(
        self, client: AsyncClient, db_session
    ) -> None:
        wf = await _create_workflow(client)
        patch = await client.put(
            f"/api/workflows/{wf['id']}",
            json={"evaluation_gate": {"mode": "require", "require_run": True}},
        )
        assert patch.status_code == 200
        publish = await client.post(f"/api/workflows/{wf['id']}/publish/1")
        assert publish.status_code == 409
        err = publish.json()["detail"]["error"]
        assert err["code"] == "EVALUATION_GATE_BLOCKED"
        assert err["details"]["evaluation_report"] is not None

    @pytest.mark.asyncio
    async def test_require_mode_force_bypass_succeeds(self, client: AsyncClient) -> None:
        wf = await _create_workflow(client)
        patch = await client.put(
            f"/api/workflows/{wf['id']}",
            json={"evaluation_gate": {"mode": "require", "require_run": True}},
        )
        assert patch.status_code == 200
        # With no completed run and force=True, publish succeeds and the
        # report marks the gate as bypassed. The report contains
        # evaluation_status=no_run_for_version when no run exists; the
        # bypass marker travels on the gate verdict when one is computed.
        publish = await client.post(f"/api/workflows/{wf['id']}/publish/1", json={"force": True})
        assert publish.status_code == 200
        report = publish.json().get("evaluation_report") or {}
        assert report.get("gate", {}).get("bypassed_by_force") is True

    @pytest.mark.asyncio
    async def test_publish_clears_gate_when_field_null(self, client: AsyncClient) -> None:
        wf = await _create_workflow(client)
        await client.put(
            f"/api/workflows/{wf['id']}",
            json={"evaluation_gate": {"mode": "warn", "min_pass_rate": 0.9}},
        )
        # Explicit null clears the gate; subsequent reads confirm it.
        patch = await client.put(f"/api/workflows/{wf['id']}", json={"evaluation_gate": None})
        assert patch.status_code == 200
        body = patch.json()
        assert body.get("evaluation_gate") is None
