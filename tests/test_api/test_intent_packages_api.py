"""Intent package API contract tests (6.49)."""

from __future__ import annotations

import json


async def _create_package(client, name: str = "pkg") -> dict:
    response = await client.post("/api/intent-packages", json={"name": name})
    assert response.status_code == 201
    return response.json()


async def _add_category(client, package_id: str, name: str = "billing") -> dict:
    response = await client.post(
        f"/api/intent-packages/{package_id}/categories",
        json={"name": name, "description": "Billing questions"},
    )
    assert response.status_code == 201
    return response.json()


async def _add_sample(client, package_id: str, category_id: str, utterance: str) -> dict:
    response = await client.post(
        f"/api/intent-packages/{package_id}/categories/{category_id}/samples",
        json={"utterance": utterance},
    )
    assert response.status_code == 201
    return response.json()


async def test_package_crud_round_trip(client):
    created = await _create_package(client, "billing-intents")
    package_id = created["id"]

    listed = await client.get("/api/intent-packages")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    updated = await client.put(f"/api/intent-packages/{package_id}", json={"description": "updated"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "updated"

    fetched = await client.get(f"/api/intent-packages/{package_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "billing-intents"

    deleted = await client.delete(f"/api/intent-packages/{package_id}")
    assert deleted.status_code == 204
    missing = await client.get(f"/api/intent-packages/{package_id}")
    assert missing.status_code == 404


async def test_duplicate_name_conflict_409(client):
    await _create_package(client, "pkg")
    response = await client.post("/api/intent-packages", json={"name": "pkg"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "CONFLICT"


async def test_category_and_samples_flow(client):
    package = await _create_package(client)
    category = await _add_category(client, package["id"])
    sample = await _add_sample(client, package["id"], category["id"], "refund my order")

    assert category["policy_gated"] is False
    assert sample["provenance"] == {}

    categories = await client.get(f"/api/intent-packages/{package['id']}/categories")
    assert categories.status_code == 200
    assert [c["name"] for c in categories.json()["items"]] == ["billing"]


async def test_freeze_and_publish_gate_409_then_force(client):
    package = await _create_package(client)
    category = await _add_category(client, package["id"])
    await _add_sample(client, package["id"], category["id"], "refund my order")

    frozen = await client.post(f"/api/intent-packages/{package['id']}/versions", json={"name": "v1"})
    assert frozen.status_code == 201
    version_id = frozen.json()["id"]
    assert frozen.json()["content_hash"]
    assert frozen.json()["published_at"] is None

    blocked = await client.post(
        f"/api/intent-packages/{package['id']}/versions/{version_id}/publish",
        json={"gate": {"mode": "require", "min_samples_per_category": 5}},
    )
    assert blocked.status_code == 409
    body = blocked.json()["detail"]["error"]
    assert body["code"] == "INTENT_PACKAGE_GATE_BLOCKED"
    assert body["details"]["gate"]["signals"][0]["name"] == "min_samples_per_category"

    # The rejection must not have published the version.
    fetched = await client.get(f"/api/intent-packages/{package['id']}/versions/{version_id}")
    assert fetched.json()["published_at"] is None

    forced = await client.post(
        f"/api/intent-packages/{package['id']}/versions/{version_id}/publish",
        json={"gate": {"mode": "require", "min_samples_per_category": 5}, "force": True},
    )
    assert forced.status_code == 200
    assert forced.json()["gate_report"]["bypassed_by_force"] is True
    assert forced.json()["published_at"] is not None

    published_only = await client.get(f"/api/intent-packages/{package['id']}/versions?published_only=true")
    assert published_only.json()["total"] == 1


async def test_correction_endpoint_records_provenance(client):
    package = await _create_package(client)
    await _add_category(client, package["id"])

    response = await client.post(
        f"/api/intent-packages/{package['id']}/corrections",
        json={
            "utterance": "cancel my subscription",
            "category_name": "billing",
            "reason": "routed to default",
        },
    )
    assert response.status_code == 201
    provenance = response.json()["provenance"]
    assert provenance["source_type"] == "correction"
    assert provenance["reason"] == "routed to default"
    assert provenance["created_by"] is not None

    unknown = await client.post(
        f"/api/intent-packages/{package['id']}/corrections",
        json={"utterance": "x", "category_name": "nope", "reason": "r"},
    )
    assert unknown.status_code == 404


async def test_import_error_report_and_export_round_trip(client):
    package = await _create_package(client)

    bad = await client.post(
        f"/api/intent-packages/{package['id']}/import",
        json={
            "format": "json",
            "mode": "merge",
            "payload": json.dumps({"categories": [{"name": "", "samples": ["x"]}]}),
        },
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["error"]["code"] == "IMPORT_VALIDATION_ERROR"
    assert bad.json()["detail"]["error"]["details"]["errors"]

    good = await client.post(
        f"/api/intent-packages/{package['id']}/import",
        json={
            "format": "json",
            "mode": "merge",
            "payload": json.dumps({"categories": [{"name": "billing", "samples": ["refund"]}]}),
        },
    )
    assert good.status_code == 200
    assert good.json()["categories_created"] == 1

    exported = await client.get(f"/api/intent-packages/{package['id']}/export?format=json")
    assert exported.status_code == 200
    payload = json.loads(exported.json()["payload"])
    assert payload["categories"][0]["samples"] == ["refund"]


async def test_version_name_conflict_409(client):
    package = await _create_package(client)
    category = await _add_category(client, package["id"])
    await _add_sample(client, package["id"], category["id"], "x")

    first = await client.post(f"/api/intent-packages/{package['id']}/versions", json={"name": "v1"})
    assert first.status_code == 201
    await client.delete(f"/api/intent-packages/{package['id']}/versions/{first.json()['id']}")

    reuse = await client.post(f"/api/intent-packages/{package['id']}/versions", json={"name": "v1"})
    assert reuse.status_code == 409  # deleted names stay reserved
