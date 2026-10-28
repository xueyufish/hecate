"""Convert one structured model call into a graph-independent decision proposal.

Hosts bind prepared inputs and version references before invoking this adapter.
DecisionStep owns acceptance and recovery; RuntimePort owns model accounting.
This component neither dispatches tools nor retries model calls. A response
without tool calls becomes a final-answer proposal when its aggregated text
is nonblank; tool calls that appear in the stream but vanish from the final
aggregation are an error, never a downgraded answer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import aclosing
from typing import Any

from hecate_runtime.accepted_decision import (
    HARNESS_INPUT_SNAPSHOT_VERSION,
    AcceptedAction,
    DecisionValidationError,
    RunRef,
    canonical_json,
)
from hecate_runtime.decision_step import KIND_ACTIONS, KIND_FINAL_ANSWER, DecisionProposal, DecisionResponder
from hecate_runtime.harness_turn import HarnessTurnPipeline, ModelCallRejectedError
from hecate_runtime.ports import RuntimePort
from hecate_runtime.tool_side_effects import classify

__all__ = ["PortDecisionResponder"]


class PortDecisionResponder(DecisionResponder):
    """Produce proposals using immutable, host-prepared invocation snapshots.

    Args:
        port: Model capability with existing cost/budget accounting.
        messages: Prepared JSON conversation; copied before use by the port.
        config: JSON model configuration. If tools are present, they must
            match the tool snapshot; otherwise the snapshot supplies them.
        tool_definitions: Name-to-definition mapping of complete function
            tool definitions (type/function), as consumed by RuntimePort.
            Tool visibility does not grant execution authorization.
        expected_refs: Host-defined input/config references for these inputs.
        on_usage: Optional synchronous observer of the final reported usage
            on a valid response. It must not perform a second settlement.
            Invalid/interrupted attempts remain accounted for by the port.
        pipeline: Optional per-turn harness pipeline — pre-call guards
            (which may reject), context projection, provider shaping and
            output governance — applied inside :meth:`respond`. The frozen
            snapshot this responder validates stays the controller-handed
            messages and references; the pipeline only shapes what the
            port is invoked with and what survives governance.
    """

    def __init__(
        self,
        port: RuntimePort,
        *,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        tool_definitions: dict[str, Any],
        expected_refs: dict[str, Any],
        on_usage: Callable[[dict[str, Any]], None] | None = None,
        pipeline: HarnessTurnPipeline | None = None,
    ) -> None:
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise DecisionValidationError("messages must be a list of JSON objects")
        if not all(isinstance(value, dict) for value in (config, tool_definitions, expected_refs)):
            raise DecisionValidationError("config, tool_definitions and expected_refs must be JSON objects")
        self._messages_json = canonical_json(messages)
        self._expected_refs_json = canonical_json(expected_refs)
        tools = json.loads(canonical_json(list(tool_definitions.values())))
        tool_map = self._tool_map(tools)
        if set(tool_map) != set(tool_definitions) or any(tool_map[name] != tool_definitions[name] for name in tool_map):
            raise DecisionValidationError("tool snapshot keys must match their function names")
        frozen_config = json.loads(canonical_json(config))
        if "tools" in frozen_config:
            if self._tool_map(frozen_config["tools"]) != tool_map:
                raise DecisionValidationError("config tools do not match the tool definition snapshot")
        else:
            frozen_config["tools"] = tools
        self._config_json = canonical_json(frozen_config)
        self._tool_names = frozenset(tool_map)
        self._port = port
        self._on_usage = on_usage
        self._pipeline = pipeline

    @classmethod
    def for_turn_messages(
        cls,
        port: RuntimePort,
        *,
        messages: list[dict[str, Any]],
        config: dict[str, Any],
        tool_definitions: dict[str, Any],
        expected_refs: dict[str, Any],
        on_usage: Callable[[dict[str, Any]], None] | None = None,
        pipeline: HarnessTurnPipeline | None = None,
    ) -> PortDecisionResponder:
        """Build a responder whose frozen snapshot continues a projected turn.

        Explicit continuation entry for messages projected from a
        completed action turn (see ``turn_projection``): the caller owns
        composing any host-side inputs into ``messages`` before
        constructing. Freezing, reference validation and the tool
        snapshot checks are identical to ``__init__``; no loop,
        continuation storage or wake-up is introduced.
        """
        return cls(
            port,
            messages=messages,
            config=config,
            tool_definitions=tool_definitions,
            expected_refs=expected_refs,
            on_usage=on_usage,
            pipeline=pipeline,
        )

    async def respond(self, run_ref: RunRef, decision_step_id: str, input_refs: dict[str, Any]) -> DecisionProposal:
        """Convert one complete response; invalid or interrupted streams fail."""
        if not isinstance(run_ref, RunRef):
            raise DecisionValidationError("run_ref must be a RunRef")
        if not isinstance(decision_step_id, str) or not decision_step_id.strip() or len(decision_step_id) > 256:
            raise DecisionValidationError("decision_step_id must be nonblank and at most 256 characters")
        if not isinstance(input_refs, dict) or canonical_json(input_refs) != self._expected_refs_json:
            raise DecisionValidationError("input refs do not match the frozen invocation references")

        text_parts: list[str] = []
        final_tool_calls: list[dict[str, Any]] | None = None
        saw_tool_calls = False
        usage: dict[str, Any] | None = None
        messages = json.loads(self._messages_json)
        config = json.loads(self._config_json)
        if self._pipeline is not None:
            prepared = await self._pipeline.prepare(messages, config.get("tools") or [])
            messages = prepared.messages
            if prepared.stop_reason is not None:
                raise ModelCallRejectedError("context", prepared.stop_reason)
            visible_tools = self._tool_map(prepared.tools)
            original_tools = self._tool_map(config.get("tools") or [])
            if any(original_tools.get(name) != definition for name, definition in visible_tools.items()):
                raise DecisionValidationError("pipeline tools must be a subset of the frozen snapshot")
            if prepared.tools != config.get("tools"):
                # Shaping may reorder or filter the provider-visible tool
                # list; acceptance still validates against the frozen
                # snapshot, and dispatch authorization is unchanged.
                config = {**config, "tools": prepared.tools}
        invocation_digest = (
            hashlib.sha256(canonical_json({"messages": messages, "config": config}).encode("utf-8")).hexdigest()
            if self._pipeline is not None
            else None
        )
        # The verifiable input snapshot: the same governed, projected and
        # provider-shaped messages/config, detached before the port can
        # mutate its arguments. The boundary is the runtime model port —
        # this is not a claim about the vendor's final wire request.
        input_snapshot = (
            {
                "snapshot_version": HARNESS_INPUT_SNAPSHOT_VERSION,
                "messages": json.loads(canonical_json(messages)),
                "config": json.loads(canonical_json(config)),
                "sha256": invocation_digest,
            }
            if invocation_digest is not None
            else None
        )
        # Fresh copies keep provider mutations from changing future proposals.
        async with aclosing(self._port.llm_invoke_structured(messages=messages, config=config)) as stream:
            async for chunk in stream:
                if not isinstance(chunk, dict):
                    raise DecisionValidationError("structured response chunks must be objects")
                content = chunk.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise DecisionValidationError("structured response content must be text or null")
                    text_parts.append(content)
                chunk_usage = chunk.get("usage")
                if isinstance(chunk_usage, dict):
                    usage = json.loads(canonical_json(chunk_usage))
                chunk_tool_calls = chunk.get("tool_calls")
                if chunk_tool_calls is not None:
                    # Snapshot before advancing the stream: ports may reuse buffers.
                    final_tool_calls = self._tool_call_snapshot(chunk_tool_calls)
                    saw_tool_calls = saw_tool_calls or bool(final_tool_calls)

        if saw_tool_calls and not final_tool_calls:
            raise DecisionValidationError("tool calls appeared in the stream but vanished from the final aggregation")
        if self._pipeline is not None:
            # Hooks consume the same provider-shaped response as the graph
            # path; revalidate every governed call before forming actions.
            calls = (
                [{"type": "function", "function": dict(entry)} for entry in final_tool_calls]
                if final_tool_calls is not None
                else None
            )
            text, governed_calls = await self._pipeline.govern("".join(text_parts), calls, messages)
            final_tool_calls = self._tool_call_snapshot(governed_calls) if governed_calls is not None else None
            text_parts = [text]

        accepted_output: dict[str, Any] = {"text": "".join(text_parts)}
        if invocation_digest is not None:
            accepted_output["harness_input_sha256"] = invocation_digest
            accepted_output["harness_input"] = input_snapshot
        if final_tool_calls:
            actions = tuple(
                self._action(run_ref, decision_step_id, position, entry)
                for position, entry in enumerate(final_tool_calls)
            )
            proposal = DecisionProposal(accepted_output=accepted_output, actions=actions, kind=KIND_ACTIONS)
        else:
            text = "".join(text_parts)
            if not text.strip():
                raise DecisionValidationError("structured response carried neither tool calls nor nonblank text")
            proposal = DecisionProposal(accepted_output=accepted_output, actions=(), kind=KIND_FINAL_ANSWER)
        if usage is not None and self._on_usage is not None:
            self._on_usage(usage)
        return proposal

    @staticmethod
    def _tool_map(tools: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(tools, list):
            raise DecisionValidationError("tool definitions must form a list")
        result: dict[str, dict[str, Any]] = {}
        for definition in tools:
            if not isinstance(definition, dict) or definition.get("type") != "function":
                raise DecisionValidationError("tool definitions must describe function tools")
            function = definition.get("function")
            if not isinstance(function, dict):
                raise DecisionValidationError("tool definitions must carry a function object")
            name = function.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 256 or name in result:
                raise DecisionValidationError("tool names must be nonblank, unique and at most 256 characters")
            result[name] = definition
        return result

    @staticmethod
    def _tool_call_snapshot(calls: Any) -> list[dict[str, Any]]:
        if not isinstance(calls, list):
            raise DecisionValidationError("tool_calls must be a list")
        selected = []
        for entry in calls:
            if not isinstance(entry, dict) or entry.get("type", "function") != "function":
                raise DecisionValidationError("tool call entries must describe functions")
            function = entry.get("function")
            if not isinstance(function, dict):
                raise DecisionValidationError("tool call entries must carry a function object")
            # Omit provider metadata, including hidden reasoning and temporary IDs.
            selected.append({"name": function.get("name"), "arguments": function.get("arguments")})
        return json.loads(canonical_json(selected))

    def _action(self, run_ref: RunRef, decision_step_id: str, position: int, entry: dict[str, Any]) -> AcceptedAction:
        tool = entry["name"]
        if not isinstance(tool, str) or tool not in self._tool_names:
            raise DecisionValidationError("tool call names a tool outside the frozen snapshot")
        return AcceptedAction(
            action_key=self._action_key(run_ref, decision_step_id, position),
            tool=tool,
            arguments=self._arguments(entry["arguments"]),
            side_effect_class=classify(tool).value,
        )

    @staticmethod
    def _arguments(raw: Any) -> dict[str, Any]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise DecisionValidationError("tool call arguments are not parsable JSON") from exc
        if not isinstance(raw, dict):
            raise DecisionValidationError("tool call arguments must be a JSON object")
        return json.loads(canonical_json(raw))

    @staticmethod
    def _action_key(run_ref: RunRef, decision_step_id: str, position: int) -> str:
        identity = canonical_json({"run": run_ref.to_dict(), "step": decision_step_id})
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]
        return f"decision:{digest}:{position}"
