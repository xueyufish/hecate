"""Unit tests for PostgresCheckpointStore in the services layer."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from hecate.services.checkpoint_store import PostgresCheckpointStore

from ..conftest import test_session_factory


@pytest.fixture()
def session_factory() -> async_sessionmaker:
    """Provide the shared test session factory."""
    return test_session_factory


@pytest.mark.asyncio
async def test_save_checkpoint(session_factory: async_sessionmaker) -> None:
    """Test saving a checkpoint to PostgreSQL."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    cp_id = await store.save(
        session_id=session_id,
        superstep=1,
        node_id="node_a",
        channel_state={"messages": ["hello"]},
    )

    assert cp_id is not None


@pytest.mark.asyncio
async def test_save_checkpoint_with_metadata(session_factory: async_sessionmaker) -> None:
    """Test saving a checkpoint with metadata."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    cp_id = await store.save(
        session_id=session_id,
        superstep=1,
        node_id="node_a",
        channel_state={"messages": ["hello"]},
        metadata={"interrupted": True, "interrupt_value": "ask_user"},
    )

    assert cp_id is not None


@pytest.mark.asyncio
async def test_load_latest_checkpoint(session_factory: async_sessionmaker) -> None:
    """Test loading the latest checkpoint."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    await store.save(session_id, 1, "node_a", {"step": 1})
    await store.save(session_id, 2, "node_b", {"step": 2})
    await store.save(session_id, 3, "node_c", {"step": 3})

    checkpoint = await store.load(session_id)

    assert checkpoint is not None
    assert checkpoint["superstep"] == 3
    assert checkpoint["channel_state"] == {"step": 3}


@pytest.mark.asyncio
async def test_load_specific_checkpoint(session_factory: async_sessionmaker) -> None:
    """Test loading a specific checkpoint by ID."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    cp_id_1 = await store.save(session_id, 1, "node_a", {"step": 1})
    await store.save(session_id, 2, "node_b", {"step": 2})

    checkpoint = await store.load(session_id, checkpoint_id=cp_id_1)

    assert checkpoint is not None
    assert checkpoint["superstep"] == 1
    assert checkpoint["id"] == cp_id_1


@pytest.mark.asyncio
async def test_load_nonexistent_checkpoint(session_factory: async_sessionmaker) -> None:
    """Test loading a non-existent checkpoint returns None."""
    store = PostgresCheckpointStore(session_factory)

    checkpoint = await store.load(uuid.uuid4())

    assert checkpoint is None


@pytest.mark.asyncio
async def test_list_checkpoints(session_factory: async_sessionmaker) -> None:
    """Test listing checkpoints for a session."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    await store.save(session_id, 1, "node_a", {"step": 1})
    await store.save(session_id, 2, "node_b", {"step": 2})
    await store.save(session_id, 3, "node_c", {"step": 3})

    checkpoints = await store.list_checkpoints(session_id)

    assert len(checkpoints) == 3
    assert checkpoints[0]["superstep"] == 3
    assert checkpoints[1]["superstep"] == 2
    assert checkpoints[2]["superstep"] == 1


@pytest.mark.asyncio
async def test_list_checkpoints_with_limit(session_factory: async_sessionmaker) -> None:
    """Test listing checkpoints with limit."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    await store.save(session_id, 1, "node_a", {"step": 1})
    await store.save(session_id, 2, "node_b", {"step": 2})
    await store.save(session_id, 3, "node_c", {"step": 3})

    checkpoints = await store.list_checkpoints(session_id, limit=2)

    assert len(checkpoints) == 2
    assert checkpoints[0]["superstep"] == 3
    assert checkpoints[1]["superstep"] == 2


@pytest.mark.asyncio
async def test_cache_hit_on_load(session_factory: async_sessionmaker) -> None:
    """Test that cache is used for subsequent loads."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    await store.save(session_id, 1, "node_a", {"step": 1})

    # First load - from DB (and cached)
    checkpoint1 = await store.load(session_id)
    assert checkpoint1 is not None

    # Second load - should hit cache
    checkpoint2 = await store.load(session_id)
    assert checkpoint2 is not None
    assert checkpoint2["superstep"] == 1


@pytest.mark.asyncio
async def test_cache_invalidation_on_save(session_factory: async_sessionmaker) -> None:
    """Test that cache is updated on save."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    await store.save(session_id, 1, "node_a", {"step": 1})
    await store.load(session_id)  # Populate cache

    await store.save(session_id, 2, "node_b", {"step": 2})

    # Cache should be updated
    checkpoint = await store.load(session_id)
    assert checkpoint is not None
    assert checkpoint["superstep"] == 2


@pytest.mark.asyncio
async def test_cache_eviction(session_factory: async_sessionmaker) -> None:
    """Test that cache evicts old entries when full."""
    store = PostgresCheckpointStore(session_factory, cache_size=2)

    session_1 = uuid.uuid4()
    session_2 = uuid.uuid4()
    session_3 = uuid.uuid4()

    await store.save(session_1, 1, "node_a", {"step": 1})
    await store.save(session_2, 1, "node_a", {"step": 1})
    await store.save(session_3, 1, "node_a", {"step": 1})

    # Cache should have evicted session_1
    assert session_1 not in store._cache
    assert session_2 in store._cache
    assert session_3 in store._cache


@pytest.mark.asyncio
async def test_checkpoint_to_dict(session_factory: async_sessionmaker) -> None:
    """Test checkpoint to dict conversion."""
    store = PostgresCheckpointStore(session_factory)
    session_id = uuid.uuid4()

    cp_id = await store.save(
        session_id=session_id,
        superstep=1,
        node_id="node_a",
        channel_state={"messages": ["hello"]},
        pending_writes=[{"key": "value"}],
        metadata={"test": True},
    )

    checkpoint = await store.load(session_id, checkpoint_id=cp_id)

    assert checkpoint is not None
    assert checkpoint["id"] == cp_id
    assert checkpoint["session_id"] == session_id
    assert checkpoint["superstep"] == 1
    assert checkpoint["node_id"] == "node_a"
    assert checkpoint["channel_state"] == {"messages": ["hello"]}
    assert checkpoint["pending_writes"] == [{"key": "value"}]
    assert checkpoint["metadata"] == {"test": True}
