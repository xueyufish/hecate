"""Entry assembly behavior: one process-wide store pair shared by entries.

The default ``memory`` backends make store identity the consistency
boundary: two instances are two invisible event histories. These tests
pin the registration/getter contract of ``core.composition.entry_assembly``
and prove the HTTP DI fallback, the wiring registration and the MCP
wrapper all resolve the same instances.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import hecate.core.composition.entry_assembly as entry_assembly
from hecate.runtime.eventstore import InMemoryEventStore
from hecate.runtime.session_state import InMemorySessionStateStore


@pytest.fixture
def clean_slots(monkeypatch: pytest.MonkeyPatch):
    """Clear both shared slots so each test starts unregistered."""
    monkeypatch.setattr(entry_assembly, "_shared_event_store", None)
    monkeypatch.setattr(entry_assembly, "_shared_session_state_store", None)


def test_registered_store_is_the_accessor_result(clean_slots) -> None:
    store = InMemoryEventStore()
    entry_assembly.register_shared_event_store(store)
    assert entry_assembly.get_shared_event_store() is store


async def test_tool_definitions_and_dispatch_are_workspace_scoped(db_session):
    import uuid

    from hecate.models.tool import ToolModel
    from hecate.tools.tool.registry import ToolRegistry

    first, second = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            ToolModel(
                workspace_id=first, name="shared_name", description="first workspace", source="custom", parameters={}
            ),
            ToolModel(
                workspace_id=second, name="shared_name", description="second workspace", source="custom", parameters={}
            ),
            ToolModel(workspace_id=second, name="foreign_only", description="private", source="custom", parameters={}),
        ]
    )
    await db_session.flush()
    tools = await entry_assembly.load_agent_tools(db_session, ["shared_name", "foreign_only"], workspace_id=first)
    assert len(tools) == 1 and tools[0]["function"]["description"] == "first workspace"
    registry = ToolRegistry(db_session, builtin_executor=None, workspace_id=first)
    with pytest.raises(ValueError, match="not found"):
        await registry.execute("foreign_only", {})
    with pytest.raises(NotImplementedError, match="Custom tool"):
        await registry.execute("shared_name", {})


def test_latest_registration_wins(clean_slots) -> None:
    first, second = InMemoryEventStore(), InMemoryEventStore()
    entry_assembly.register_shared_event_store(first)
    entry_assembly.register_shared_event_store(second)
    assert entry_assembly.get_shared_event_store() is second


def test_session_state_store_registration(clean_slots) -> None:
    store = InMemorySessionStateStore()
    entry_assembly.register_shared_session_state_store(store)
    assert entry_assembly.get_shared_session_state_store() is store


def test_lazy_fallback_builds_from_settings(clean_slots) -> None:
    store = entry_assembly.get_shared_event_store()
    assert store is not None
    # Second call resolves the lazily-built instance, not a new one.
    assert entry_assembly.get_shared_event_store() is store


def test_http_di_fallback_resolves_the_shared_instance(clean_slots) -> None:
    """Without lifespan state, the HTTP dependency returns the shared store."""
    from hecate.core.deps_event_store import get_event_store
    from hecate.core.deps_state_store import get_session_state_store

    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    store = InMemoryEventStore()
    state_store = InMemorySessionStateStore()
    entry_assembly.register_shared_event_store(store)
    entry_assembly.register_shared_session_state_store(state_store)

    assert get_event_store(request) is store
    assert get_session_state_store(request) is state_store


def test_http_app_state_takes_precedence_over_shared_slot(clean_slots) -> None:
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(event_store=InMemoryEventStore())))
    from hecate.core.deps_event_store import get_event_store

    entry_assembly.register_shared_event_store(InMemoryEventStore())
    assert get_event_store(request) is request.app.state.event_store


def test_wiring_registers_lifespan_instances(clean_slots) -> None:
    """attach_state_stores publishes its instances to the shared slots."""
    from fastapi import FastAPI

    from hecate.core.composition.wiring import attach_state_stores

    app = FastAPI()
    attach_state_stores(app)

    assert entry_assembly.get_shared_event_store() is app.state.event_store
    assert entry_assembly.get_shared_session_state_store() is app.state.session_state_store


def test_cross_entry_resolutions_observe_one_store(clean_slots) -> None:
    """The MCP wrapper and the direct accessor are the same instance."""
    from hecate.tools.mcp.server import _get_shared_event_store

    store = InMemoryEventStore()
    entry_assembly.register_shared_event_store(store)

    assert _get_shared_event_store() is store
    assert entry_assembly.get_shared_event_store() is store
