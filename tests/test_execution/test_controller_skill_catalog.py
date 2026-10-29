"""Frozen skill catalog for explicitly enabled controller tasks.

The full loop runs through the REAL skill machinery: frozen AgentVersion
bindings, the strict-pin SkillLoader, the rendered catalog committed
before the first model call, and the actual builtin ``load_skill``
execution serving L2 content into the next decision. Only the model and
the governance layer are scripted; skill resolution, freezing and tool
execution are never mocked.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from hecate_runtime.ports import StubRuntimePort
from sqlalchemy import select, update

from hecate.contracts.execution.durable import TaskLifecycleState
from hecate.execution.task_control import TaskControlService, task_ref_of
from hecate.models.agent_version import AgentVersionModel
from hecate.models.evolution import SkillUsageEventModel  # noqa: F401 — table in metadata
from hecate.models.managed_delivery import ManagedDeliveryModel  # noqa: F401
from hecate.models.skill import ControllerSkillCatalogModel, SkillModel
from hecate.models.skill_version import SkillVersionModel
from hecate.models.standalone_enrollment import StandaloneEnrollmentModel  # noqa: F401
from hecate.models.workspace import WorkspaceModel
from tests.test_execution import test_controller_loop_dispatch as controller_fixtures
from tests.test_execution.test_controller_loop_dispatch import (
    ORG,
    OWNER,
    WS,
)

controller_harness = controller_fixtures.harness

OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000043")

SKILL_NAME = "inventory-playbook"
SKILL_INSTRUCTIONS = "PLAYBOOK-BODY: reconcile the ledger before counting."


class ScriptedSkillPort(StubRuntimePort):
    """Scripted structured model; tool calls route into a REAL builtin
    executor backed by the strict-pin SkillLoader."""

    def __init__(self, *, workspace_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        super().__init__()
        self.registry = None
        self._workspace_id = workspace_id
        self._agent_id = agent_id
        self.final_text = "playbook applied"
        self.model_calls = 0
        self.messages: list[dict] = []
        self.tool_calls: list[tuple[str, dict]] = []

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.model_calls += 1
        self.messages = messages
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if tool_messages:
            yield {"content": self.final_text, "tool_calls": None}
            return
        yield {
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "load_skill", "arguments": json.dumps({"skill_name": SKILL_NAME})},
                }
            ],
        }

    async def tool_execute(self, name, args, context=None):
        self.tool_calls.append((name, dict(args)))
        assert self.registry is not None
        return await self.registry.execute(name, args, context)


class _RealSkillAssembly:
    """Patch only the model and governance seams of the controller assembly."""

    def __init__(self, port: ScriptedSkillPort) -> None:
        self._port = port
        self._saved: dict[str, Any] = {}

    def patch(self) -> None:
        import hecate.core.composition.entry_assembly as entry_assembly
        import hecate.core.composition.guardrail_platform as guardrail_platform
        import hecate.core.composition.runtime_port_adapter as port_adapter

        self._saved = {
            "load_agent_tools": entry_assembly.load_agent_tools,
            "guardrails": guardrail_platform.assemble_guardrails,
            "create_runtime_port": port_adapter.create_runtime_port,
        }

        async def _no_tools(db, tool_names, *, workspace_id=None):
            return getattr(self._port, "definitions", [])

        async def _no_guardrails(*args: Any, **kwargs: Any):
            return getattr(self._port, "bundle", None)

        def _real_port(db, llm_service, *, tool_registry=None, workspace_id=None, agent_id=None, identity=None):
            self._port.registry = tool_registry
            return self._port

        entry_assembly.load_agent_tools = _no_tools
        guardrail_platform.assemble_guardrails = _no_guardrails
        port_adapter.create_runtime_port = _real_port

    def unpatch(self) -> None:
        import hecate.core.composition.entry_assembly as entry_assembly
        import hecate.core.composition.guardrail_platform as guardrail_platform
        import hecate.core.composition.runtime_port_adapter as port_adapter

        entry_assembly.load_agent_tools = self._saved["load_agent_tools"]
        guardrail_platform.assemble_guardrails = self._saved["guardrails"]
        port_adapter.create_runtime_port = self._saved["create_runtime_port"]


@pytest.fixture
async def harness(controller_harness, tmp_path):
    """Reuse the existing durable platform integration fixture."""
    state = controller_harness
    async with state["session_factory"]() as db:
        db.add(WorkspaceModel(id=OTHER_WS, org_id=ORG, name="other", slug=f"ws-{OTHER_WS.hex}"))
        skill = SkillModel(
            workspace_id=WS,
            name=SKILL_NAME,
            source="user",
            description="Reconcile inventory ledgers safely.",
            instructions=SKILL_INSTRUCTIONS,
            model_invocable=True,
            provider="user",
            max_tokens=500,
        )
        db.add(skill)
        await db.flush()
        version = (
            await db.execute(select(AgentVersionModel).where(AgentVersionModel.agent_id == state["agent_id"]))
        ).scalar_one()
        version.config_snapshot = {**version.config_snapshot, "tools": [], "skills": [SKILL_NAME]}
        version.ref_manifest = [
            {"resource_type": "skill", "resource_id": SKILL_NAME, "skill_id": str(skill.id), "version": 1}
        ]
        from hecate.core.canonical_hash import canonical_hash
        from hecate.tools.skill.versioning import snapshot_skill

        content = snapshot_skill(skill)
        db.add(
            SkillVersionModel(
                workspace_id=WS,
                skill_id=skill.id,
                version=1,
                config_snapshot=content,
                content_hash=canonical_hash(
                    {key: content[key] for key in ("name", "instructions", "allowed_tools", "scripts", "references")}
                ),
            )
        )
        await db.commit()
        state["skill_id"] = skill.id
    state["workspace_root"] = str(tmp_path / "workspace")
    return state


async def _dispatch(
    harness, *, skill_catalog: bool = True, port=None, targets=None, options=None, input_overrides=None
):
    """Submit and drive one controller task to a terminal durable state."""
    from hecate_durable.worker import DurableWorker

    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    port = port or ScriptedSkillPort(
        workspace_id=WS,
        agent_id=harness["agent_id"],
    )
    assembly = _RealSkillAssembly(port)
    assembly.patch()
    harness["cleanups"].append(assembly.unpatch)
    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    worker = DurableWorker(harness["store"], dispatcher, leases=harness["store"].leases)
    controller: dict[str, Any] = {"max_turns": 6, **(options or {})}
    if skill_catalog:
        controller["skill_catalog"] = True
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["suite"].store,
            recorder=harness["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            dispatcher=None,
            worker=worker,
        )
        result = await service.submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="use the frozen skill catalog",
            agent_id=harness["agent_id"],
            input={
                "messages": [{"role": "user", "content": "run the playbook"}],
                "controller_loop": True,
                "controller": controller,
                **(input_overrides or {}),
            },
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = task_ref_of(task_uuid)
    deadline = asyncio.get_running_loop().time() + 30.0
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
        if record is not None and record.lifecycle_state.value in (
            targets
            or {
                "succeeded",
                "failed",
                "cancelled",
                "reconciliation_required",
            }
        ):
            return record, t_ref, task_uuid, port, worker
        await worker.reconcile()
        await asyncio.sleep(0.05)
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    last = record.lifecycle_state.value if record else None
    raise AssertionError(f"task never reached a terminal state; last={last}")


async def _catalog_row(harness, task_uuid: uuid.UUID) -> Any:
    async with harness["session_factory"]() as db:
        result = await db.execute(
            select(ControllerSkillCatalogModel).where(ControllerSkillCatalogModel.task_id == task_uuid)
        )
        return result.scalar_one_or_none()


async def test_frozen_catalog_loads_real_l2_and_binds_the_surface(harness) -> None:
    """Opt-in: frozen catalog in the harness, real load_skill, L2 consumed."""
    record, _t_ref, task_uuid, port, _worker = await _dispatch(harness)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra

    row = await _catalog_row(harness, task_uuid)
    assert row is not None
    assert SKILL_NAME in (row.advertised_names or [])
    assert row.pins[SKILL_NAME]["version"] == 1
    assert row.content_sha256

    catalog_text = row.catalog_rendered
    assert f'<skill name="{SKILL_NAME}"' in catalog_text
    assert SKILL_INSTRUCTIONS not in catalog_text  # L1 only; body not pre-injected

    harness_messages = [m for m in port.messages if m.get("role") == "system" and "skills" in str(m.get("content"))]
    assert harness_messages and SKILL_NAME in str(harness_messages[0]["content"])
    assert SKILL_INSTRUCTIONS not in str(port.messages[0].get("content", ""))

    tool_tail = [m for m in port.messages if m.get("role") == "tool"][-1]
    assert SKILL_INSTRUCTIONS in tool_tail["content"]  # the REAL L2 body
    assert port.tool_calls == [("load_skill", {"skill_name": SKILL_NAME})]
    assert port.model_calls == 2


async def test_catalog_outside_and_governed_skills_cannot_load(harness) -> None:
    """A foreign-workspace skill and a non-invocable skill are not served."""
    async with harness["session_factory"]() as db:
        db.add(
            SkillModel(
                workspace_id=OTHER_WS,
                name="foreign-playbook",
                source="workspace",
                description="other workspace",
                instructions="FOREIGN-BODY",
                model_invocable=True,
                provider="user",
                max_tokens=500,
            )
        )
        governed = await db.get(SkillModel, harness["skill_id"])
        governed.model_invocable = False
        await db.commit()

    record, _t_ref, _task_uuid, port, _worker = await _dispatch(harness)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    # The scripted model keeps re-asking load_skill; the builtin refuses
    # (not advertised) and the loop classifies the action failure — no
    # foreign content ever reaches the conversation.
    assert result.get("error_category") == "controller_action_failed"
    leaked = [m for m in port.messages if m.get("role") == "tool" and "FOREIGN-BODY" in str(m.get("content"))]
    assert leaked == []


async def test_strict_pin_resolution_failure_fails_before_model_usage(harness) -> None:
    """A pinned version that cannot resolve refuses the task pre-model."""
    async with harness["session_factory"]() as db:
        await db.execute(
            update(SkillVersionModel).where(SkillVersionModel.skill_id == harness["skill_id"]).values(deleted=True)
        )
        await db.commit()

    record, _t_ref, _task_uuid, port, _worker = await _dispatch(harness)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    assert result.get("error_category") == "skill_catalog_freeze_failed"
    assert port.model_calls == 0  # refused before any model usage


async def test_frozen_surface_survives_live_edits_and_new_versions(harness) -> None:
    """Live edits and new versions never rewrite a frozen task's surface."""
    record, _t_ref, task_uuid, _port, _worker = await _dispatch(harness)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    frozen_before = await _catalog_row(harness, task_uuid)

    async with harness["session_factory"]() as db:
        skill = await db.get(SkillModel, harness["skill_id"])
        skill.instructions = "TAMPERED-LIVE-BODY"
        db.add(
            SkillVersionModel(
                skill_id=harness["skill_id"],
                version=2,
                config_snapshot={"name": SKILL_NAME, "description": "v2", "instructions": "V2-BODY"},
                content_hash="e" * 64,
            )
        )
        await db.commit()

    frozen_after = await _catalog_row(harness, task_uuid)
    assert frozen_after.catalog_rendered == frozen_before.catalog_rendered
    assert frozen_after.content_sha256 == frozen_before.content_sha256

    # A NEW task freezes its own surface; the frozen agent-version pin
    # still names version 1, so it resolves the SAME frozen content.
    record2, _t2, task2, port2, _w2 = await _dispatch(harness)
    assert record2.lifecycle_state is TaskLifecycleState.SUCCEEDED
    row2 = await _catalog_row(harness, task2)
    assert row2.pins[SKILL_NAME]["version"] == 1
    assert "TAMPERED-LIVE-BODY" not in row2.catalog_rendered
    tool_tail = [m for m in port2.messages if m.get("role") == "tool"][-1]
    assert SKILL_INSTRUCTIONS in tool_tail["content"]
    assert "TAMPERED-LIVE-BODY" not in tool_tail["content"]


async def test_lost_terminal_commit_replays_skills_without_loading_again(harness, monkeypatch):
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    original_finish = PlatformTaskDispatcher._finish
    calls = 0

    async def lose_first_finish(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("terminal commit lost after accepted Skill decisions")
        return await original_finish(self, *args, **kwargs)

    monkeypatch.setattr(PlatformTaskDispatcher, "_finish", lose_first_finish)
    record, t_ref, task_uuid, port, _worker = await _dispatch(harness)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra
    assert calls == 2
    assert port.model_calls == 2
    assert port.tool_calls == [("load_skill", {"skill_name": SKILL_NAME})]
    row = await _catalog_row(harness, task_uuid)
    payload = await asyncio.to_thread(harness["store"].get_task_input, t_ref)
    from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
    from hecate_runtime.accepted_decision import RunRef

    for step in ("agent-turn-000001", "agent-turn-000002"):
        decision = await SqlAcceptedDecisionStore(harness["store"]).lookup(
            RunRef("hecate", payload["platform_run_id"]), step
        )
        assert decision.input_refs["skill_catalog_sha256"] == row.content_sha256


async def test_separate_process_serves_same_frozen_builtin_content(harness):
    import subprocess
    import sys

    record, _t_ref, task_uuid, _port, _worker = await _dispatch(harness)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    async with harness["session_factory"]() as db:
        skill = await db.get(SkillModel, harness["skill_id"])
        skill.instructions = "LIVE CHANGED AFTER FREEZE"
        await db.commit()
    script = """
import asyncio, sys, uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from hecate.models.skill import ControllerSkillCatalogModel
from hecate.execution.skill_catalog import ControllerSkillLoader
from hecate.core.composition.entry_assembly import build_tool_registry
async def main():
    engine = create_async_engine(sys.argv[1])
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        row = (await db.execute(select(ControllerSkillCatalogModel).where(
            ControllerSkillCatalogModel.task_id == uuid.UUID(sys.argv[2])
        ))).scalar_one()
        registry = build_tool_registry(db, workspace_id=row.workspace_id, skill_loader=ControllerSkillLoader(db, row))
        result = await registry.execute("load_skill", {"skill_name": sys.argv[3]}, {
            "workspace_id": str(row.workspace_id), "agent_id": str(row.agent_id)
        })
        print(result)
    await engine.dispose()
asyncio.run(main())
"""
    process = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(harness["engine"].url), str(task_uuid), SKILL_NAME],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert process.returncode == 0, process.stderr
    assert SKILL_INSTRUCTIONS in process.stdout
    assert "LIVE CHANGED" not in process.stdout


class InputAfterSkillPort(ScriptedSkillPort):
    """Real Skill loads combined with a scripted input-requesting business tool."""

    definitions = [controller_fixtures.TOOL_DEFINITION]

    async def tool_execute(self, name, args, context=None):
        if name == "load_skill":
            return await super().tool_execute(name, args, context)
        self.tool_calls.append((name, dict(args)))
        if "region" not in args:
            return {"status": "input_required", "contract": {"fields": ["region"]}}
        return {"quantity": 3, "region": args["region"]}

    async def llm_invoke_structured(self, messages, config):
        self.model_calls += 1
        self.messages = messages
        results = [m for m in messages if m.get("role") == "tool"]
        if len(results) == 4:
            yield {"content": "frozen playbook and provided region applied", "tool_calls": None}
            return
        if len(results) in (0, 2):
            name, args = "load_skill", {"skill_name": SKILL_NAME}
        else:
            name, args = "query_inventory", {"sku": "S-9"}
            if len(results) == 3:
                args["region"] = "EU"
                assert any("provided_input" in str(m.get("content")) for m in messages)
        yield {
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{len(results)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        }


@pytest.mark.parametrize("intervention", ["input", "cancel"])
async def test_skill_catalog_survives_input_wake_or_cancel(harness, intervention):
    port = InputAfterSkillPort(workspace_id=WS, agent_id=harness["agent_id"])
    record, t_ref, task_uuid, _port, _worker = await _dispatch(harness, port=port, targets={"waiting_input"})
    row = await _catalog_row(harness, task_uuid)
    digest = row.content_sha256
    assert port.model_calls == 2
    async with harness["session_factory"]() as db:
        skill = await db.get(SkillModel, harness["skill_id"])
        skill.instructions = "EDITED WHILE WAITING"
        await db.commit()
    worker = controller_fixtures._worker_for(harness)
    if intervention == "input":
        wait = record.extra["wait"]
        issue = await controller_fixtures._issue_input_wake(
            harness,
            task_uuid,
            wait["wait_token"],
            {"region": "EU"},
            worker=worker,
        )
        assert issue.record.state.value == "applied"
        record = await controller_fixtures._dispatch_to_terminal(harness, t_ref, worker=worker)
        assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra
        assert port.model_calls == 5
        assert sum(name == "load_skill" for name, _ in port.tool_calls) == 2
        assert "EDITED WHILE WAITING" not in str(port.messages)
        assert SKILL_INSTRUCTIONS in str(port.messages)
    else:
        from hecate.contracts.execution.durable import ControlCommandKind

        async with harness["session_factory"]() as db:
            service = TaskControlService(
                db,
                store=harness["store"],
                recorder=harness["store"],
                backend="postgres",
                ledger_source="core",
                session_factory=harness["session_factory"],
                worker=worker,
            )
            issue = await service.issue_command(
                workspace_id=WS, task_id=task_uuid, kind=ControlCommandKind.CANCEL, issuer="reviewer"
            )
            assert issue.record.state.value == "applied"
        record = await controller_fixtures._dispatch_to_state(harness, t_ref, worker, "cancelled")
        await worker.reconcile()
        assert port.model_calls == 2
    assert (await _catalog_row(harness, task_uuid)).content_sha256 == digest


@pytest.mark.parametrize("value", ["true", 1, None])
async def test_invalid_skill_opt_in_is_rejected_at_submission(harness, value):
    from hecate.execution.task_control import TaskControlValidationError

    with pytest.raises(TaskControlValidationError, match="boolean"):
        await controller_fixtures._submit_rows(harness, {"controller": {"skill_catalog": value}})
    assert harness["store"].list_tasks() == []


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_skill_content_and_original_run_survive_governed_approval(harness, decision):
    from types import SimpleNamespace

    from hecate_runtime.eventstore import InMemoryEventStore
    from hecate_runtime.security.approval import FailingClosedApprovalCallback
    from hecate_runtime.tool_access import AccessDecision

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger
    from hecate.execution.approval_decision import ApprovalDecisionService

    class Policy:
        def evaluate(self, tool_meta, *args, **kwargs):
            return AccessDecision.REQUIRE_APPROVAL if tool_meta["name"] == "query_inventory" else AccessDecision.EXECUTE

    class Port(ScriptedSkillPort):
        definitions = [controller_fixtures.TOOL_DEFINITION]

        async def llm_invoke_structured(self, messages, config):
            self.model_calls += 1
            self.messages = messages
            count = len([m for m in messages if m.get("role") == "tool"])
            if count == 2:
                yield {"content": "approved business result and frozen skill consumed", "tool_calls": None}
                return
            name, args = (
                ("load_skill", {"skill_name": SKILL_NAME}) if count == 0 else ("query_inventory", {"sku": "S-9"})
            )
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{count}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            }

        async def tool_execute(self, name, args, context=None):
            if name == "load_skill":
                return await super().tool_execute(name, args, context)
            self.tool_calls.append((name, dict(args)))
            return {"quantity": 3}

    port = Port(workspace_id=WS, agent_id=harness["agent_id"])
    port.bundle = SimpleNamespace(
        access_policy=Policy(),
        rules=[],
        denial_tracker=None,
        middleware_chains={},
        approval_callback=FailingClosedApprovalCallback(
            event_store=InMemoryEventStore(), session_id=uuid.uuid4(), workspace_id=WS, agent_id=harness["agent_id"]
        ),
    )
    record, t_ref, task_uuid, _port, _old_worker = await _dispatch(
        harness,
        port=port,
        targets={"waiting_approval"},
        input_overrides={"approval_wait": True},
    )
    row = await _catalog_row(harness, task_uuid)
    digest = row.content_sha256
    wait = record.extra["wait"]
    worker = controller_fixtures._worker_for(harness)
    async with harness["session_factory"]() as db:
        skill = await db.get(SkillModel, harness["skill_id"])
        skill.instructions = "CHANGED DURING APPROVAL"
        await db.commit()
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            worker=worker,
        )
        result = await ApprovalDecisionService.of(service).submit_decision(
            WS,
            task_uuid,
            operation_id=f"skill-{decision}",
            approval_ref=wait["contract_ref"]["approval_ref"],
            decision=decision,
            reason="verified",
            wait_token=wait["wait_token"],
            expected_revision=record.revision,
            decided_by=str(uuid.uuid4()),
        )
        assert result["resume"]["state"] == "applied"
    record = await controller_fixtures._dispatch_to_terminal(harness, t_ref, worker=worker)
    assert record.lifecycle_state.value == ("succeeded" if decision == "approve" else "failed"), record.extra
    assert len([name for name, _ in port.tool_calls if name == "query_inventory"]) == (decision == "approve")
    assert len([name for name, _ in port.tool_calls if name == "load_skill"]) == 1
    assert "CHANGED DURING APPROVAL" not in str(port.messages)
    assert (await _catalog_row(harness, task_uuid)).content_sha256 == digest
    async with harness["session_factory"]() as db:
        authority = await SqlApprovalLedger(db).get(wait["contract_ref"]["approval_ref"])
        assert bool(authority.consumed_by_invocation_id) == (decision == "approve")
