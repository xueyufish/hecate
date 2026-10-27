"""Per-turn semantic pipeline around one controller model decision.

The graph-free controller path composes the same semantic order the
Pregel ``LLMWorker`` applies to a conversation node, without a graph or a
channel: a pre-call guard chain (which may reject the call before any
model usage), the context processor chain's non-destructive projection,
provider shaping via the port, and a post-response governance chain. The
pipeline is a per-invocation view: the frozen harness composition, the
accepted-decision chain and the input revisions stay the authority, and a
projection never deletes recovery facts.

Rejections are explicit, never silent downgrades: a guard BLOCK raises
:class:`ModelCallRejectedError` before a proposal exists, so the refused
turn commits no decision and the loop classifies a
``model_call_rejected`` stop instead of laundering a canned refusal into
an accepted model output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hecate_runtime.accepted_decision import DecisionValidationError, canonical_json
from hecate_runtime.context_policy import model_default_budget
from hecate_runtime.context_processors import resolve_budget
from hecate_runtime.middleware import Chain, Phase

__all__ = [
    "HarnessTurnPipeline",
    "ModelCallRejectedError",
    "PreparedModelCall",
]


class ModelCallRejectedError(Exception):
    """A guard stage refused this turn's model call; nothing was committed.

    ``stage`` names the refusing boundary (``pre_llm`` before the call,
    ``llm_response`` after a completed stream, ``context`` for a budget
    stop); ``reason`` carries the boundary's reason for the stop event.
    """

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


@dataclass(frozen=True)
class PreparedModelCall:
    """The governed, projected and shaped inputs for one model call."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    stop_reason: str | None


async def _identity_handler(data: Any) -> Any:
    """Terminal handler for guard chains: the data itself is the result."""
    return data


class HarnessTurnPipeline:
    """Compose guards, context projection and provider shaping per turn.

    Every stage is optional: a component the host does not supply is a
    pass-through, so wiring the pipeline with nothing leaves the model
    messages and tools unchanged. ``context_chain`` accepts a
    ``ContextChainFactory`` (policy resolution, cached chains) or an
    already-built ``ContextProcessorChain``; ``middleware_chains`` is the
    per-phase chain dict the guardrail assembly produces — only the
    ``AGENT_REQUEST`` (pre-call) and ``LLM_RESPONSE`` (output governance)
    phases are consumed here; tool phases stay with the action executor.
    """

    def __init__(
        self,
        *,
        context_chain: Any | None = None,
        middleware_chains: dict | None = None,
        port: Any | None = None,
        session_id: str = "",
        model: str = "",
        node_config: dict[str, Any] | None = None,
    ) -> None:
        self._context_chain = context_chain
        self._chains = middleware_chains or {}
        self._port = port
        self._session_id = session_id
        self._model = model
        self._node_config = json.loads(canonical_json(node_config or {}))
        self._binding_json = canonical_json(self._binding())

    def _binding(self) -> dict[str, Any]:
        chain = self._context_chain
        processors = []
        explicit = False
        if chain is not None:
            if hasattr(chain, "resolve"):
                policy = chain.resolve(self._node_config, model=self._model or None)
                processors = policy.processors
                explicit = policy.metadata_source() in ("agent", "node")
            else:
                processors = chain.processors
        for processor in processors:
            if getattr(processor, "backend", None) == "surface_replacement" or (
                explicit and processor.name == "offload"
            ):
                raise DecisionValidationError("controller harness does not support durable compaction or offload")
        return {
            "version": "harness-turn/1",
            "model": self._model,
            "context_budget": resolve_budget(
                self._node_config, {"context_budget_model_default": model_default_budget(self._model or None)}
            ),
            "node_config": self._node_config,
            "processors": [
                {"name": processor.name, "type": type(processor).__qualname__, "params": vars(processor)}
                for processor in processors
            ],
        }

    def frozen_refs(self) -> dict[str, Any]:
        """Versioned projection policy bound by every accepted decision.

        Guard verdicts remain live so a known denial overrides old input
        snapshots; this reference pins semantic projection and shaping.
        """
        return json.loads(self._binding_json)

    async def prepare(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> PreparedModelCall:
        """Guard, project and shape one turn's model inputs.

        A pre-call BLOCK raises before any model usage. SANITIZE replaces
        the messages the guard returned. The context chain projection is a
        view over the frozen inputs; ``stop_reason`` carries the chain's
        controlled-termination verdict so the responder stops before a
        new model call rather than treating a budget stop as completion.
        """
        if canonical_json(self._binding()) != self._binding_json:
            raise DecisionValidationError("harness pipeline policy changed after freezing")
        messages, tools = await self._guard_request(messages, tools)

        stop_reason: str | None = None
        if self._context_chain is not None:
            chain = self._context_chain
            if not hasattr(chain, "apply"):
                chain = chain.chain_for_node(self._node_config, model=self._model or None)
            execution_context: dict[str, Any] = {"session_id": self._session_id}
            budget = model_default_budget(self._model or None)
            if budget is not None:
                execution_context["context_budget_model_default"] = budget
            report = await chain.apply(messages, self._node_config, execution_context, "controller")
            messages = report.messages
            stop_reason = report.stop_reason

        shaped_tools = tools
        # Provider shaping is an optional port capability (the RuntimePort
        # default is pass-through); duck-typed host ports without it keep
        # the projected messages verbatim.
        if self._port is not None and hasattr(self._port, "context_assemble"):
            assembled = await self._port.context_assemble(
                messages=messages,
                tools=tools,
                session_id=self._session_id,
                model=self._model or "gpt-4o",
            )
            if not isinstance(assembled, dict):
                raise ModelCallRejectedError("pre_llm", "provider shaping returned a malformed request")
            messages = assembled.get("messages", messages)
            shaped_tools = assembled.get("tools", tools)
            if shaped_tools is None:
                shaped_tools = []
        self._validate_request(messages, shaped_tools)
        return PreparedModelCall(messages=list(messages), tools=shaped_tools, stop_reason=stop_reason)

    async def govern(
        self, text: str, tool_calls: list[dict[str, Any]] | None, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]] | None]:
        """Run output governance over one completed model response.

        A BLOCK raises — the consumed model usage stays accounted by the
        port, but the response can never become an accepted proposal.
        SANITIZE replaces the governed text and tool calls.
        """
        chain = self._chains.get(Phase.LLM_RESPONSE)
        if chain is None or not chain.stages:
            return text, tool_calls
        guarded = Chain(phase=chain.phase, stages=list(chain.stages), handler=_identity_handler)
        response: dict[str, Any] = {"content": text}
        if tool_calls is not None:
            response["tool_calls"] = tool_calls
        decision, result = await guarded.run({"response": response, "messages": messages})
        if decision.action.value == "block":
            raise ModelCallRejectedError("llm_response", decision.reason or "response blocked by policy")
        if not isinstance(result, dict) or not isinstance(result.get("response"), dict):
            raise ModelCallRejectedError("llm_response", "guard returned a malformed response")
        governed_response = result["response"]
        governed_text = governed_response.get("content")
        governed_calls = governed_response.get("tool_calls")
        if not isinstance(governed_text, str) or not isinstance(governed_calls, (list, type(None))):
            raise ModelCallRejectedError("llm_response", "guard returned a malformed response")
        canonical_json(governed_response)
        return governed_text, governed_calls

    async def _guard_request(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Run the pre-call guard chain; BLOCK refuses the call entirely."""
        chain = self._chains.get(Phase.AGENT_REQUEST)
        if chain is None or not chain.stages:
            return messages, tools
        guarded = Chain(phase=chain.phase, stages=list(chain.stages), handler=_identity_handler)
        decision, result = await guarded.run({"messages": messages, "model": self._model, "tools": tools})
        if decision.action.value == "block":
            raise ModelCallRejectedError("pre_llm", decision.reason or "request blocked by policy")
        if not isinstance(result, dict):
            raise ModelCallRejectedError("pre_llm", "guard returned a malformed request")
        messages = result.get("messages")
        tools = result.get("tools", tools)
        self._validate_request(messages, tools)
        return messages, tools

    @staticmethod
    def _validate_request(messages: Any, tools: Any) -> None:
        if (
            not isinstance(messages, list)
            or any(not isinstance(message, dict) for message in messages)
            or not isinstance(tools, list)
            or any(not isinstance(tool, dict) for tool in tools)
        ):
            raise ModelCallRejectedError("pre_llm", "pipeline returned a malformed request")
        canonical_json({"messages": messages, "tools": tools})
