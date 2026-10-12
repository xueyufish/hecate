"""Recorded real-model comparison of the two production chat mechanisms.

Run manually with ``python -m tests.scenarios.baseline.run_autonomous_baseline``.
Synthetic business tools are local; model selection is real. Missing credentials
produce an explicit skipped record, never a quality or production certificate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

BASELINE_DIR = Path(__file__).resolve().parent.parent / "baselines"
RUBRIC_VERSION = "autonomous-execution-rubric-2"
LATENCY_BUDGET_MS = 60_000.0
TOKEN_BUDGET = 20_000
COST_BUDGET_USD = 1.0
SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
MATERIAL = {"status": "ok", "stock": "confirmed", "reorder": True, "ticket_body": "SKU-A1 confirmed by warehouse-A"}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_material",
            "description": "Read stock and the required ticket body",
            "parameters": {
                "type": "object",
                "required": ["domain", "sku"],
                "properties": {"domain": {"type": "string"}, "sku": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_test_ticket",
            "description": "Create a test reorder ticket using the returned ticket_body verbatim",
            "parameters": {
                "type": "object",
                "required": ["title", "body"],
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            },
        },
    },
]
MESSAGES = [
    {
        "role": "system",
        "content": (
            "Read material first. If reorder is true, create one ticket using ticket_body verbatim. "
            'Return JSON {"answer":"..."} including the resulting ticket_id.'
        ),
    },
    {"role": "user", "content": "Check stock for SKU-A1 in domain_a and create the reorder ticket."},
]
REPORT_SCHEMA = {
    "type": "object",
    "required": ["_meta", "runs"],
    "properties": {
        "_meta": {
            "type": "object",
            "required": ["status", "gate", "rubric_version", "model", "thresholds", "unverified"],
            "properties": {"status": {"enum": ["recorded", "skipped"]}, "gate": {"const": False}},
        },
        "runs": {
            "type": "array",
            "items": {"type": "object", "required": ["path", "case", "rounds", "summary", "usage", "stopped_reason"]},
        },
    },
}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class StubBusinessRegistry:
    """Synthetic tool boundary shared by the two production mechanisms."""

    def __init__(self, case: str) -> None:
        self.case = case
        self.calls: list[dict[str, Any]] = []
        self.side_effects = 0
        self.cancel_requested = False

    async def describe(self, name: str) -> dict[str, Any]:
        """Expose the same input schemas to the production governance facade."""
        return next(({"parameters": t["function"]["parameters"]} for t in TOOLS if t["function"]["name"] == name), {})

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        """Run a local read or count one test ticket attempt."""
        from hecate_governance.claims import OutcomeUnknownError

        self.calls.append({"tool": name, "arguments": dict(args)})
        if name == "lookup_material":
            self.cancel_requested = self.case == "cancelled"
            return MATERIAL
        if name == "create_test_ticket":
            self.side_effects += 1
            if self.case == "needs_review":
                raise OutcomeUnknownError("synthetic lost write receipt")
            return {"status": "ok", "ticket_id": "TKT-BASELINE"}
        raise ValueError("unsupported tool")


class RecordedModelService:
    """Record complete model choices, usage and per-call latency."""

    def __init__(self, model: str, registry: StubBusinessRegistry, completion: Any = None) -> None:
        self.model = model
        self.registry = registry
        self.completion = completion
        self.rounds: list[dict[str, Any]] = []
        self.usages: list[dict[str, Any] | None] = []

    async def chat(self, **kwargs: Any) -> Any:
        """Call the real provider, or an injected provider used by report tests."""
        from hecate_llm.service import LLMResponse

        from hecate.core.composition.governance import BudgetRefusedError

        if self.registry.case == "budget_exhausted" and self.rounds:
            raise BudgetRefusedError("synthetic refusal before the next model call")
        completion = self.completion
        if completion is None:
            from litellm import acompletion

            completion = acompletion
        started = time.monotonic()
        response = await completion(
            model=self.model,
            messages=kwargs["messages"],
            tools=kwargs.get("tools"),
            temperature=0,
            max_tokens=2048,
            timeout=60,
            **({"api_key": os.environ["OPENAI_API_KEY"]} if self.completion is None else {}),
        )
        message = response.choices[0].message
        calls = [tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in (message.tool_calls or [])]
        usage = getattr(response, "usage", None)
        usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage) if usage else None
        self.usages.append(usage_dict)
        self.rounds.append(
            {
                "index": len(self.rounds),
                "tool_calls": calls,
                "final": not bool(calls),
                "latency_ms": (time.monotonic() - started) * 1000,
            }
        )
        return LLMResponse(
            content=message.content or "",
            model=self.model,
            tool_calls=calls,
            usage=usage_dict or {},
            finish_reason="tool_calls" if calls else "stop",
        )

    async def chat_stream(self, **kwargs: Any):
        """Expose complete provider choices as one streamed delta to the port."""
        from types import SimpleNamespace

        response = await self.chat(**kwargs)
        yield {
            "content": response.content,
            "tool_calls": [
                SimpleNamespace(index=i, id=call["id"], type="function", function=SimpleNamespace(**call["function"]))
                for i, call in enumerate(response.tool_calls or [])
            ],
        }
        yield {"content": "", "usage": response.usage}


def _score(
    registry: StubBusinessRegistry, model: RecordedModelService, content: str, reason: str | None
) -> dict[str, Any]:
    order = [call["tool"] for call in registry.calls]
    read_valid = bool(registry.calls) and registry.calls[0] == {
        "tool": "lookup_material",
        "arguments": {"domain": "domain_a", "sku": "SKU-A1"},
    }
    writes = [call for call in registry.calls if call["tool"] == "create_test_ticket"]
    derived = len(writes) == 1 and writes[0]["arguments"].get("body") == MATERIAL["ticket_body"]
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        document = None
    expected = None if registry.case == "normal" else registry.case
    stop_correct = reason == expected and (
        (registry.case == "normal" and registry.side_effects == 1)
        or (registry.case == "needs_review" and registry.side_effects == 1 and len(model.rounds) == 2)
        or (
            registry.case in ("budget_exhausted", "cancelled") and registry.side_effects == 0 and len(model.rounds) == 1
        )
    )
    return {
        "tool_selection_correct": read_valid
        and (derived if registry.case in ("normal", "needs_review") else order == ["lookup_material"]),
        "structured_output_valid": reason is None and Draft202012Validator(SCHEMA).is_valid(document),
        "artifact_reference_correct": reason is None and "TKT-BASELINE" in content,
        "unknown_stop_compliance": stop_correct if registry.case == "needs_review" else None,
        "budget_compliance": stop_correct if registry.case == "budget_exhausted" else None,
        "cancel_compliance": stop_correct if registry.case == "cancelled" else None,
        "stop_compliance": stop_correct,
    }


async def run_case(path: str, case: str, model_name: str, completion: Any = None) -> dict[str, Any]:
    """Compare the actual direct loop and Pregel entry with identical inputs."""
    from hecate_governance.claims import MemoryActionClaimLedger
    from hecate_governance.policy import GovernanceService
    from hecate_runtime.errors import ExecutionStoppedError, MaxSuperstepsError
    from hecate_runtime.eventstore import InMemoryEventStore
    from hecate_runtime.session_state import InMemorySessionStateStore

    from hecate.channel.api.v1.chat import (
        _chat_with_tools,
        _CheckedModelService,
        _enforce_output_schema,
        _schema_feedback,
    )
    from hecate.core.composition.governance import GovernedToolRegistry, PlatformGovernance
    from hecate.core.composition.runtime_port_adapter import create_runtime_port
    from hecate.execution.entry_service import EntryExecutionService
    from hecate.tools.policy.governance_adapter import PipelinePolicyProfile

    registry = StubBusinessRegistry(case)
    model = RecordedModelService(model_name, registry, completion)
    checked = _CheckedModelService(model, lambda: registry.cancel_requested)
    governance = PlatformGovernance(
        GovernanceService(PipelinePolicyProfile()),
        action_claims=MemoryActionClaimLedger(),
        identity={"actor": "baseline-user", "data_domains": ["domain_a"]},
        workspace_id=uuid.uuid4(),
    )
    content = ""
    reason = None
    started = time.monotonic()
    try:
        if path == "direct":
            result = await _chat_with_tools(
                MESSAGES,
                model_name,
                TOOLS,
                GovernedToolRegistry(registry, governance),
                0,
                2048,
                session_id=str(uuid.uuid4()),
                model_service=checked,
                output_schema=SCHEMA,
            )
            content = result.content or ""
        else:
            port = create_runtime_port(None, checked, tool_registry=registry, governance=governance)
            entry = EntryExecutionService(
                port=port,
                entry_name="real-model-baseline",
                event_store=InMemoryEventStore(),
                checkpoint_store=InMemorySessionStateStore(),
            )
            outcome = await entry.execute(
                agent_mode="chat", messages=MESSAGES, model=model_name, tools=TOOLS, stream=False
            )
            result = outcome.result
            from hecate_llm.service import LLMResponse

            async def repair(feedback: str):
                return await checked.chat(
                    messages=MESSAGES
                    + [
                        {"role": "assistant", "content": result["content"]},
                        {"role": "user", "content": _schema_feedback(feedback)},
                    ],
                    tools=None,
                )

            final = await _enforce_output_schema(LLMResponse(content=result.get("content", "")), SCHEMA, repair)
            content = final.content or ""
    except ExecutionStoppedError as exc:
        reason = exc.reason_category
    except MaxSuperstepsError:
        reason = "iteration_limit"
    elapsed = (time.monotonic() - started) * 1000
    measured = all(usage is not None and usage.get("total_tokens") is not None for usage in model.usages)
    total_tokens = sum(usage["total_tokens"] for usage in model.usages) if measured else None
    summary = _score(registry, model, content, reason)
    summary.update(
        {
            "within_latency_threshold": elapsed <= LATENCY_BUDGET_MS,
            "within_token_threshold": total_tokens <= TOKEN_BUDGET if total_tokens is not None else None,
            "within_cost_threshold": None,
        }
    )
    return {
        "path": path,
        "case": case,
        "rounds": model.rounds,
        "stopped_reason": reason,
        "summary": summary,
        "latency_ms_total": elapsed,
        "side_effect_count": registry.side_effects,
        "usage": {
            "source": "reported" if measured else "unverified",
            "token_total": total_tokens,
            "cost_amount": None,
            "currency": "USD",
            "price_version": "unverified",
        },
    }


async def run_real_model_task(completion: Any = None) -> dict[str, Any]:
    """Record the same normal and fault-injection cases for both mechanisms."""
    model = os.environ.get("BASELINE_MODEL", "gpt-4o")
    report = {
        "_meta": {
            "kind": "record",
            "status": "recorded",
            "gate": False,
            "business_value": "unverified",
            "recorded_at": datetime.now(UTC).isoformat(),
            "rubric_version": RUBRIC_VERSION,
            "model": model,
            "model_version": os.environ.get("BASELINE_MODEL_VERSION", "unverified"),
            "tool_versions": {"lookup_material": "synthetic-2", "create_test_ticket": "synthetic-2"},
            "dataset_digest": _digest(MATERIAL),
            "prompt_digest": _digest(MESSAGES),
            "tool_digest": _digest(TOOLS),
            "thresholds": {"latency_ms": LATENCY_BUDGET_MS, "tokens": TOKEN_BUDGET, "cost_usd": COST_BUDGET_USD},
            "unverified": [
                "provider price and final billing",
                "durable approval/resume with a real model",
                "standalone Runner model selection",
                "business value",
                "managed deployment certification",
            ],
        },
        "runs": [],
    }
    if completion is None and not os.environ.get("OPENAI_API_KEY"):
        report["_meta"].update(
            {"status": "skipped", "reason": "OPENAI_API_KEY is not set; no real-model quality measured"}
        )
    else:
        for path in ("direct", "engine"):
            for case in ("normal", "needs_review", "budget_exhausted", "cancelled"):
                report["runs"].append(await run_case(path, case, model, completion))
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    return report


def main() -> Path:
    """Write a recorded or explicitly skipped report without CI authority."""
    report = asyncio.run(run_real_model_task())
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    out = BASELINE_DIR / "autonomous_multiturn_baseline.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(report["_meta"]["status"] + ": " + report["_meta"].get("reason", "comparison recorded"))
    return out


if __name__ == "__main__":
    print(f"baseline written to {main()}")
