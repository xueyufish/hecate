"""Checkpoint identity and session isolation regressions."""

import uuid

from hecate_durable.storage import SqlCheckpointStore, SqlDurableStore


def test_checkpoint_id_cannot_read_another_session(tmp_path):
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'checkpoints.db'}")
    store.create_schema()
    checkpoints = SqlCheckpointStore(store.session_factory)
    first, second = uuid.uuid4(), uuid.uuid4()
    try:
        checkpoint = checkpoints.save_sync(first, 1, None, {"secret": "first session"})
        loaded = checkpoints.load_sync(first, checkpoint)
        assert loaded is not None
        assert loaded["channel_state"]["secret"] == "first session"
        assert checkpoints.load_sync(second, checkpoint) is None
    finally:
        store.dispose()
