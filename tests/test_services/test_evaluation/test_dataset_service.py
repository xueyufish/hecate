"""Tests for EvaluationDatasetService — CRUD, items, import/export."""

from __future__ import annotations

import uuid

import pytest

from hecate.services.evaluation.dataset_service import EvaluationDatasetService


@pytest.fixture
def svc(db_session: object) -> EvaluationDatasetService:
    """Create a dataset service with the test database session."""
    return EvaluationDatasetService(db_session)  # type: ignore[arg-type]


class TestCreateDataset:
    async def test_create_minimal(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="test-ds")
        assert ds.name == "test-ds"
        assert ds.id is not None
        assert ds.description is None
        assert ds.metadata_ == {}

    async def test_create_full(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(
            name="full-ds",
            description="A test dataset",
            metadata={"version": 1},
        )
        assert ds.description == "A test dataset"
        assert ds.metadata_ == {"version": 1}


class TestGetDataset:
    async def test_get_existing(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="get-test")
        fetched = await svc.get_dataset(ds.id)
        assert fetched.id == ds.id
        assert fetched.name == "get-test"

    async def test_get_nonexistent(self, svc: EvaluationDatasetService) -> None:
        with pytest.raises(ValueError, match="not found"):
            await svc.get_dataset(uuid.uuid4())


class TestListDatasets:
    async def test_list_empty(self, svc: EvaluationDatasetService) -> None:
        items, total = await svc.list_datasets()
        assert total == 0
        assert items == []

    async def test_list_with_data(self, svc: EvaluationDatasetService) -> None:
        await svc.create_dataset(name="ds1")
        await svc.create_dataset(name="ds2")
        items, total = await svc.list_datasets()
        assert total == 2
        assert len(items) == 2

    async def test_list_pagination(self, svc: EvaluationDatasetService) -> None:
        for i in range(5):
            await svc.create_dataset(name=f"ds-{i}")
        items, total = await svc.list_datasets(page=1, page_size=2)
        assert total == 5
        assert len(items) == 2


class TestUpdateDataset:
    async def test_update_name(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="original")
        updated = await svc.update_dataset(ds.id, name="renamed")
        assert updated.name == "renamed"

    async def test_update_description(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="test")
        updated = await svc.update_dataset(ds.id, description="new desc")
        assert updated.description == "new desc"


class TestDeleteDataset:
    async def test_delete_dataset(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="to-delete")
        await svc.delete_dataset(ds.id)
        with pytest.raises(ValueError, match="not found"):
            await svc.get_dataset(ds.id)


class TestAddItems:
    async def test_add_valid_items(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="items-test")
        count = await svc.add_items(
            ds.id,
            [
                {"query": "What is Python?"},
                {"query": "What is Java?", "expected_answer": "A programming language"},
            ],
        )
        assert count == 2

    async def test_skip_empty_query(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="skip-test")
        count = await svc.add_items(
            ds.id,
            [
                {"query": "valid"},
                {"query": ""},
                {"query": "   "},
            ],
        )
        assert count == 1


class TestListItems:
    async def test_list_items(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="list-items")
        await svc.add_items(
            ds.id,
            [
                {"query": "q1"},
                {"query": "q2"},
            ],
        )
        items, total = await svc.list_items(ds.id)
        assert total == 2
        assert len(items) == 2

    async def test_list_items_pagination(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="pag-items")
        for i in range(5):
            await svc.add_items(ds.id, [{"query": f"q{i}"}])
        items, total = await svc.list_items(ds.id, page=1, page_size=2)
        assert total == 5
        assert len(items) == 2


class TestDeleteItem:
    async def test_delete_item(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="del-item")
        await svc.add_items(ds.id, [{"query": "to-delete"}])
        items, _ = await svc.list_items(ds.id)
        item_id = items[0].id
        await svc.delete_item(item_id)
        items_after, _ = await svc.list_items(ds.id)
        assert len(items_after) == 0


class TestImportExport:
    async def test_import_json(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="import-test")
        result = await svc.import_json(
            ds.id,
            [
                {"query": "q1", "expected_answer": "a1"},
                {"query": "", "expected_answer": "skip"},
                {"query": "q2", "context": ["c1"]},
            ],
        )
        assert result["total"] == 3
        assert result["valid"] == 2
        assert result["skipped"] == 1

    async def test_export_json(self, svc: EvaluationDatasetService) -> None:
        ds = await svc.create_dataset(name="export-test")
        await svc.add_items(
            ds.id,
            [
                {"query": "q1", "expected_answer": "a1"},
                {"query": "q2", "context": ["c1", "c2"]},
            ],
        )
        exported = await svc.export_json(ds.id)
        assert len(exported) == 2
        assert exported[0]["query"] == "q1"
        assert exported[1]["context"] == ["c1", "c2"]
