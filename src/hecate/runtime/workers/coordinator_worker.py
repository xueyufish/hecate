"""CoordinatorWorker — the 1.3.18 dynamic orchestration engine-side worker.

A graph node of type `` COORDINATOR `` is dispatched to this worker. The
worker runs the Magentic double-loop:

  Outer loop (per iteration):
    1. Ask the planner model for a candidate ``TaskDAG`` revision.
    2. Run ``validate_task_requirements`` against the current roster.
       On failure, emit ``ORCHESTRATOR_DECISION`` with ``dag=None`` and
       ``reasoning="verification_failed"`` and continue to the next
       iteration (allow the planner to self-correct).
    3. Materialize the validated DAG into a sub-``GraphConfig`` via
       ``build_dynamic_orchestration_executor``.
    4. Run the sub-graph in an isolated child session: distinct
       ``session_id``, fresh ``ChannelManager``, fresh checkpoint store.
    5. Ask the evaluator model to produce an ``ORCHESTRATOR_EVALUATION``
       event with typed blocker payload.
    6. If the evaluator reports `` verdict="satisfied" ``, fold the
       sub-graph's final channel state into a synthesis channel and
       return; else advance to the next iteration.

The outer loop is bounded by ``OrchestrationBudgets.max_iterations`` and
the inner stall counter (Magentic "stall counter <= 2" semantics).

**Isolation contract (5 properties, see design.md §D6):**
  1. Sub-graph runs in a fresh ``ChannelManager`` whose registered
     channels are exactly those listed in the executor's
     ``channel_mapping.input``. Reading an undeclared parent channel
     raises ``KeyError``.
  2. Sub-graph's long-term memory writes are keyed by the child's
     session id, never the parent's thread id.
  3. After sub-graph completion, only channels listed in
     ``channel_mapping.output`` are written back; the sub-graph's
     ``messages`` channel is never copied to the parent.
  4. Failure of a sub-task is determined by ``WorkerResult.error``
     only — never by parsing the sub-agent's output text.
  5. The child session uses an in-memory checkpoint store by default;
     checkpoints are throwaway and never re-enter the parent's restore path.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.dynamic_types import (
    BENEFIT_BASED_DELEGATION_RUBRIC,
    OrchestrationBudgets,
    TaskDAG,
)
from hecate.runtime.eventstore import (
    CURRENT_LOG_SCHEMA_VERSION,
    Event,
    EventType,
)
from hecate.runtime.pregel import PregelRuntime
from hecate.runtime.types import StreamMode, WorkerResult
from hecate.runtime.worker import Worker

if TYPE_CHECKING:
    # Lazy-imported at runtime inside the methods that use them so engine
    # remains independent of services/ (Phase 0 拆包入场费 plan, PR0.3).
    from hecate.runtime.replay.orchestrator_validator import (
        RosterEntry,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Guidance string emitted with stop_reason events. Source-faithful to
#: deer-flow's "durable delegation ledger captures stop_reason and renders
#: model-facing guidance so the lead reuses a capped completion knowingly
#: instead of mistaking it for a clean one." tests pin this verbatim.
CAPPED_GUIDANCE: str = "reuse it, retry tighter, or raise budget"

#: Canonical verdict set for ORCHESTRATOR_EVALUATION events. The set is
#: additive over the eventual tool: legacy readers MUST fall back to
#: ``"stalled"`` for unknown strings (see ADR-030 unknown-EventType
#: fallback semantics; this is the verdict analogue).
EVAL_VERDICTS: frozenset[str] = frozenset(
    {
        "satisfied",
        "needs_user_input",
        "missing_evidence",
        "run_failed",
        "external_wait",
        "goal_not_met_yet",
        "stalled",
    }
)

#: Canonical stop_reason set, additive alongside the verdict. We
#: deliberately do NOT add a new WorkerResult.status / EventType value
#: because that would break v1 consumers (DeerFlow Phase 1→2 lesson).
STOP_REASONS: frozenset[str] = frozenset({"token_capped", "turn_capped", "loop_capped", "stall_capped"})


# ---------------------------------------------------------------------------
# LLM port (minimal protocol)
# ---------------------------------------------------------------------------


class PlannerResponseError(RuntimeError):
    """Planner returned an unparseable TaskDAG."""


class LLMInvoker:  # protocol shape; not a true Protocol — see coordinator_worker.py
    """Minimal protocol for the model invocations CoordinatorWorker needs.

    In production, ``RuntimePort.llm_invoke`` is the implementation; tests
    inject a stub that returns pre-baked TaskDAG JSON or evaluator text.
    """


def _coerce_planner_output(text: str) -> TaskDAG:
    """Parse a planner response into a TaskDAG, with one fallback for
    JSON-wrapped responses.

    The planner is expected to return JSON parsable as ``TaskDAG``. If
    parsing fails, we re-raise ``PlannerResponseError`` so the caller
    can emit a verification_failed event.
    """
    try:
        return TaskDAG.model_validate_json(text)
    except (PydanticValidationError, ValueError) as exc:
        raise PlannerResponseError(str(exc)) from exc


def _coerce_evaluator_output(text: str) -> tuple[str, str | None, str | None]:
    """Parse an evaluator response into (verdict, blocker, stop_reason).

    Unknown verdict values are silently demoted to ``"stalled"`` (the
    legacy-readers' fallback semantics from ADR-030 §1).
    """
    # Default response if parsing fails entirely.
    verdict = "stalled"
    blocker: str | None = text.strip() or None
    stop_reason: str | None = None

    lowered = text.strip().lower()
    for candidate in EVAL_VERDICTS:
        if candidate in lowered:
            verdict = candidate
            break
    for candidate in STOP_REASONS:
        if candidate in lowered:
            stop_reason = candidate
            break
    return verdict, blocker, stop_reason


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class CoordinatorWorker(Worker):
    """Implements the Magentic double-loop orchestration of a TaskDAG."""

    def __init__(
        self,
        llm_invoker: Any | None = None,
        sub_worker_factory: Any | None = None,
        event_store: Any = None,
    ) -> None:
        super().__init__(event_store=event_store)
        self._llm = llm_invoker
        self._sub_worker_factory = sub_worker_factory

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict[str, Any],
        execution_context: dict | None = None,
    ) -> WorkerResult:
        """Drive the orchestration loop for one COORDINATOR node dispatch.

        Returns a WorkerResult whose ``channel_updates`` carry the
        ``_synthesis_buffer`` (LLM-synthesized answer) and ``_plan``
        (the final TaskDAG used). ``_ledger`` accumulates per-task
        outputs across iterations for replan-with-carryover.
        """
        from hecate.runtime.replay.orchestrator_validator import (
            validate_budgets,
            validate_task_requirements,
        )

        ctx = execution_context or {}
        goal = node_config.get("goal") or channel_snapshot.get("goal") or node_id
        roster = self._resolve_roster(node_config, channel_snapshot)
        budgets: OrchestrationBudgets = OrchestrationBudgets.model_validate(node_config.get("budgets", {}))
        planner_model = node_config.get("planner_model", "default")
        evaluator_model = node_config.get("evaluator_model", "default")

        ledger: dict[str, dict[str, Any]] = {}
        stall_counter = 0
        last_synthesis: Any = None
        last_dag: TaskDAG | None = None
        session_id = ctx.get("session_id") or uuid.uuid4()

        for iteration in range(budgets.max_iterations):
            plan_revision = iteration + 1

            # 1. Planner LLM call.
            try:
                planner_text = await self._call_planner(
                    planner_model,
                    goal=goal,
                    roster=roster,
                    ledger=ledger,
                )
                candidate = _coerce_planner_output(planner_text)
            except PlannerResponseError as exc:
                await self._emit_event(
                    ctx,
                    session_id,
                    EventType.ORCHESTRATOR_DECISION,
                    node_id=node_id,
                    payload={
                        "plan_revision": plan_revision,
                        "dag": None,
                        "reasoning": f"verification_failed: {exc}",
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
                continue

            # 2. Budget pre-check + roster validation (fail-closed).
            budget_issues = validate_budgets(candidate)
            if budget_issues:
                await self._emit_event(
                    ctx,
                    session_id,
                    EventType.ORCHESTRATOR_DECISION,
                    node_id=node_id,
                    payload={
                        "plan_revision": plan_revision,
                        "dag": None,
                        "reasoning": ("verification_failed: " + "; ".join(i.message for i in budget_issues)),
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
                continue

            report = validate_task_requirements(candidate, roster)
            if not report.is_valid:
                await self._emit_event(
                    ctx,
                    session_id,
                    EventType.ORCHESTRATOR_DECISION,
                    node_id=node_id,
                    payload={
                        "plan_revision": plan_revision,
                        "dag": None,
                        "reasoning": ("verification_failed: " + "; ".join(i.message for i in report.issues)),
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
                continue

            # 3. Decision event with the validated DAG.
            await self._emit_event(
                ctx,
                session_id,
                EventType.ORCHESTRATOR_DECISION,
                node_id=node_id,
                payload={
                    "plan_revision": plan_revision,
                    "dag": candidate.model_dump(mode="json"),
                    "reasoning": "validated",
                    "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                },
            )
            last_dag = candidate

            # 4. Execute the sub-graph in an isolated child session.
            sub_session_id = uuid.uuid4()
            try:
                sub_state = await self._run_sub_graph(
                    candidate,
                    roster,
                    sub_session_id,
                    ledger,
                    channel_snapshot,
                    ctx,
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.exception("Sub-graph execution failed")
                await self._emit_event(
                    ctx,
                    session_id,
                    EventType.ORCHESTRATOR_EVALUATION,
                    node_id=node_id,
                    payload={
                        "verdict": "run_failed",
                        "blocker": str(exc),
                        "stop_reason": None,
                        "guidance_string": None,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                )
                continue

            # Record per-task outputs into the ledger so a later iteration
            # can carry them forward without re-running the producer.
            self._absorb_into_ledger(ledger, candidate, sub_state)

            # 5. Evaluator LLM call.
            eval_text = await self._call_evaluator(
                evaluator_model,
                goal=goal,
                ledger=ledger,
            )
            verdict, blocker, stop_reason = _coerce_evaluator_output(eval_text)
            guidance = CAPPED_GUIDANCE if stop_reason else None

            await self._emit_event(
                ctx,
                session_id,
                EventType.ORCHESTRATOR_EVALUATION,
                node_id=node_id,
                payload={
                    "verdict": verdict,
                    "blocker": blocker,
                    "stop_reason": stop_reason,
                    "guidance_string": guidance,
                    "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                },
            )

            if verdict == "satisfied":
                # Fold the sub-graph's final state into a synthesis buffer.
                last_synthesis = self._synthesize(candidate, ledger)
                break

            if verdict == "stalled":
                stall_counter += 1
                if stall_counter > budgets.stall_limit:
                    await self._emit_event(
                        ctx,
                        session_id,
                        EventType.ORCHESTRATOR_DECISION,
                        node_id=node_id,
                        payload={
                            "plan_revision": plan_revision,
                            "dag": None,
                            "reasoning": "stall_cap_exceeded",
                            "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                        },
                    )
                    last_synthesis = self._synthesize(candidate, ledger)
                    break
            else:
                # Any other non-satisfied verdict resets the stall counter
                # (planner is making progress, just not done yet).
                stall_counter = 0

        else:  # exhausted max_iterations without breaking out
            await self._emit_event(
                ctx,
                session_id,
                EventType.ORCHESTRATOR_DECISION,
                node_id=node_id,
                payload={
                    "plan_revision": budgets.max_iterations,
                    "dag": None,
                    "reasoning": "iteration_cap_exceeded",
                    "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                },
            )
            if last_dag is not None:
                last_synthesis = self._synthesize(last_dag, ledger)

        updates: dict[str, Any] = {
            "_plan": last_dag.model_dump(mode="json") if last_dag is not None else None,
            "_ledger": ledger,
            "_synthesis_buffer": last_synthesis,
            "_stall_counter": stall_counter,
        }
        return WorkerResult(node_id=node_id, channel_updates=updates)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_roster(
        self,
        node_config: dict,
        channel_snapshot: dict,
    ) -> list[RosterEntry]:
        """Resolve the agent roster from node_config or a roster channel."""
        from hecate.runtime.replay.orchestrator_validator import RosterEntry

        raw = node_config.get("roster")
        if raw is None:
            raw = channel_snapshot.get("agent_roster", [])
        return [RosterEntry.from_value(r) for r in (raw or [])]

    async def _call_planner(
        self,
        model: str,
        *,
        goal: str,
        roster: list[RosterEntry],
        ledger: dict,
    ) -> str:
        """Invoke the planner LLM. Default impl delegates to the port."""
        if self._llm is None:
            msg = "CoordinatorWorker has no llm_invoker configured"
            raise PlannerResponseError(msg)
        prompt = self._build_planner_prompt(goal=goal, roster=roster, ledger=ledger)
        return await self._llm.invoke(model=model, prompt=prompt)

    async def _call_evaluator(
        self,
        model: str,
        *,
        goal: str,
        ledger: dict,
    ) -> str:
        """Invoke the evaluator LLM."""
        if self._llm is None:
            return "stalled"
        prompt = self._build_evaluator_prompt(goal=goal, ledger=ledger)
        return await self._llm.invoke(model=model, prompt=prompt)

    def _build_planner_prompt(
        self,
        *,
        goal: str,
        roster: list[RosterEntry],
        ledger: dict,
    ) -> str:
        roster_lines = "\n".join(
            f"- agent_id={r.agent_id} capabilities={sorted(r.capabilities)} model={r.model}" for r in roster
        )
        ledger_lines = (
            "\n".join(f"- {task_id}: outputs={list(outputs.keys())}" for task_id, outputs in ledger.items())
            if ledger
            else "(empty)"
        )
        return (
            f"{BENEFIT_BASED_DELEGATION_RUBRIC}\n\n"
            f"Goal: {goal}\n\n"
            f"Roster:\n{roster_lines}\n\n"
            f"Completed task outputs available for carryover:\n{ledger_lines}\n\n"
            "Produce a JSON TaskDAG. Output JSON only."
        )

    def _build_evaluator_prompt(self, *, goal: str, ledger: dict) -> str:
        ledger_repr = "\n".join(f"{tid}: {list(outputs.keys())}" for tid, outputs in ledger.items())
        return (
            f"You are an evaluator. Given the goal and current per-task "
            f"outputs, emit ONE of the canonical verdicts on its own line:\n"
            f"{sorted(EVAL_VERDICTS)}\n\n"
            f"Optionally include a stop_reason from {sorted(STOP_REASONS)}.\n\n"
            f"Goal: {goal}\n\n"
            f"Per-task outputs:\n{ledger_repr or '(empty)'}\n\n"
            "Output exactly one verdict and optionally one stop_reason."
        )

    async def _run_sub_graph(
        self,
        dag: TaskDAG,
        roster: list[RosterEntry],
        sub_session_id: uuid.UUID,
        ledger: dict,
        parent_snapshot: dict,
        ctx: dict,
    ) -> dict[str, Any]:
        """Execute the materialised sub-graph in an isolated child session."""
        from hecate.services.workflow.templates import build_dynamic_orchestration_executor

        sub_graph = build_dynamic_orchestration_executor(
            dag=dag,
            roster=roster,
            ledger=ledger,
        )
        sub_worker = self._sub_worker_factory() if self._sub_worker_factory else None
        sub_checkpoint = InMemoryCheckpointStore()

        await self._emit_event(
            ctx,
            parent_session_id(ctx),
            EventType.SUBGRAPH_START,
            payload={
                "child_session_id": str(sub_session_id),
                "task_count": len(dag.tasks),
                "planner_model": ctx.get("model"),
                "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
            },
        )

        # Channel isolation: only declared channels from parent_snapshot
        # are visible to the child. ChannelManager.read() raises KeyError
        # on unregistered channels (the child runs in its own
        # ChannelManager constructed inside PregelRuntime). We project
        # the parent snapshot down to a minimal initial_input rather than
        # exposing the parent's ChannelManager instance.
        parent_input_keys = set(parent_snapshot.keys()) & {"messages", "context"}
        initial_input = {name: parent_snapshot[name] for name in parent_input_keys if name in parent_snapshot}
        # Carryover: explicit declared outputs from completed tasks.
        for task_id in dag.carryover_outputs:
            entry = ledger.get(task_id)
            if entry:
                for out_name, value in entry.items():
                    initial_input[f"{task_id}.{out_name}"] = value

        sub_runtime = PregelRuntime(
            graph=GraphCompiler().compile(sub_graph),
            worker=sub_worker,
            checkpoint_store=sub_checkpoint,
        )

        final_state: dict[str, Any] = {}
        sub_status = "ok"
        try:
            results: list[dict] = []
            async for event in sub_runtime.execute(
                session_id=sub_session_id,
                initial_input=initial_input,
                stream_mode=StreamMode.VALUES,
            ):
                results.append(event)
            if results:
                final_state = results[-1].get("state", {})
        except Exception as exc:  # noqa: BLE001 — surfaced as status
            logger.warning("Sub-graph raised: %s", exc)
            sub_status = "failed"

        await self._emit_event(
            ctx,
            parent_session_id(ctx),
            EventType.SUBGRAPH_END,
            payload={
                "child_session_id": str(sub_session_id),
                "status": sub_status,
                "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
            },
        )

        return final_state

    def _absorb_into_ledger(
        self,
        ledger: dict[str, dict[str, Any]],
        dag: TaskDAG,
        sub_state: dict[str, Any],
    ) -> None:
        """Take the sub-graph's final channel state and project per-task outputs."""
        for task in dag.tasks:
            output_key = f"{task.id}.{task.expected_output}"
            if output_key in sub_state:
                ledger.setdefault(task.id, {})[task.expected_output] = sub_state[output_key]

    def _synthesize(self, dag: TaskDAG, ledger: dict) -> Any:
        """Fold ledger outputs into a single synthesis payload.

        The synthesis step is deliberately deterministic: the LLM-based
        summarisation (if any) is the caller's responsibility via
        ``synthesis_prompt`` on the next iteration; this worker just
        exposes the folded payload.
        """
        folded: dict[str, Any] = {}
        for task in dag.tasks:
            entry = ledger.get(task.id)
            if entry:
                folded[task.id] = entry.get(task.expected_output)
        return folded

    async def _emit_event(
        self,
        ctx: dict,
        session_id: Any,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        if self._event_store is None:
            return
        store = ctx.get("event_store") or self._event_store
        try:
            await store.append(
                Event(
                    session_id=session_id,
                    superstep=int(ctx.get("superstep", 0)),
                    event_type=event_type,
                    node_id=node_id,
                    payload=payload or {},
                    trace_id=ctx.get("trace_id"),
                )
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception("Failed to append orchestration event")


def parent_session_id(ctx: dict) -> Any:
    """Tiny indirection so tests can monkeypatch session-id resolution."""
    return ctx.get("session_id") or uuid.uuid4()


__all__ = [
    "CAPPED_GUIDANCE",
    "CoordinatorWorker",
    "EVAL_VERDICTS",
    "LLMInvoker",
    "PlannerResponseError",
    "STOP_REASONS",
    "parent_session_id",
]
