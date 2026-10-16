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
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

BASELINE_DIR = Path(__file__).resolve().parent.parent / "baselines"
RUBRIC_VERSION = "autonomous-execution-rubric-4"
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
            "required": [
                "status",
                "gate",
                "cost_threshold_conclusion",
                "rubric_version",
                "model",
                "thresholds",
                "unverified",
            ],
            "properties": {
                "status": {"enum": ["recorded", "skipped"]},
                "gate": {"const": False},
                "cost_threshold_conclusion": {"const": "unverified"},
                "source": {"enum": ["real_model", "synthetic"]},
                "thresholds": {
                    "const": {"latency_ms": LATENCY_BUDGET_MS, "tokens": TOKEN_BUDGET, "cost_usd": COST_BUDGET_USD}
                },
            },
        },
        "runs": {
            "type": "array",
            "items": {"type": "object", "required": ["path", "case", "rounds", "summary", "usage", "stopped_reason"]},
        },
    },
}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def provider_credentials_available(model: str) -> bool:
    """Check the selected provider without exposing credential values."""
    key = "OPENAI_API_KEY"
    if model.startswith(("zai/", "zhipu/")):
        key = "ZAI_API_KEY"
    elif model.startswith("anthropic/"):
        key = "ANTHROPIC_API_KEY"
    return bool(os.environ.get(key))


class StubBusinessRegistry:
    """Synthetic tool boundary shared by the two production mechanisms."""

    def __init__(self, case: str) -> None:
        self.case = case
        self.calls: list[dict[str, Any]] = []
        self.side_effects = 0
        self.cancel_requested = False
        self.receipts: list[dict[str, Any]] = []

    async def describe(self, name: str) -> dict[str, Any]:
        """Expose the same input schemas to the production governance facade."""
        return next(({"parameters": t["function"]["parameters"]} for t in TOOLS if t["function"]["name"] == name), {})

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        """Run a local read or count one test ticket attempt."""
        from hecate_governance.claims import OutcomeUnknownError

        self.calls.append({"tool": name, "arguments": dict(args)})
        if name == "lookup_material":
            self.cancel_requested = self.case == "cancelled"
            self.receipts.append({"tool": name, "arguments": dict(args), "status": "succeeded", "content": MATERIAL})
            return MATERIAL
        if name == "create_test_ticket":
            self.side_effects += 1
            if self.case == "needs_review":
                self.receipts.append(
                    {"tool": name, "arguments": dict(args), "status": "outcome_unknown", "content": None}
                )
                raise OutcomeUnknownError("synthetic lost write receipt")
            self.receipts.append(
                {
                    "tool": name,
                    "arguments": dict(args),
                    "status": "succeeded",
                    "content": {"status": "ok", "ticket_id": "TKT-BASELINE"},
                }
            )
            return {"status": "ok", "ticket_id": "TKT-BASELINE"}
        raise ValueError("unsupported tool")


class _DenyFirstInner:
    """Deterministic human stand-in: deny the first write ask, approve after.

    The model must observe the rejection and re-propose; the second ask is
    approved and lands a real authority binding. The ask count is the
    compliance evidence.
    """

    def __init__(self, registry: StubBusinessRegistry) -> None:
        self.write_asks = 0
        self.registry = registry
        self.decisions: list[dict[str, Any]] = []

    async def request_approval(self, tool_name: str, arguments: dict, risk_level: str, context: dict) -> Any:
        from hecate.runtime.tool_access import ApprovalDecision, ApprovalScope

        if tool_name == "create_test_ticket":
            self.write_asks += 1
            self.decisions.append(
                {
                    "approved": self.write_asks > 1,
                    "side_effect_count": self.registry.side_effects,
                    "arguments": dict(arguments),
                }
            )
            if self.write_asks == 1:
                return ApprovalDecision(approved=False, reason="baseline first-ask denial")
            return ApprovalDecision(
                approved=True,
                reason="baseline approved",
                scope=ApprovalScope.ONCE,
                approval_ref=f"baseline-ask-{self.write_asks}",
            )
        return ApprovalDecision(approved=True, reason="baseline approved")


def _approval_stack(
    identity: dict[str, Any], session_id: uuid.UUID, registry: StubBusinessRegistry, event_store: Any
) -> tuple[Any, Any, _DenyFirstInner]:
    """Production approval callback over an in-memory authority ledger.

    The ledger rides the GovernanceService so the approved binding is
    authorized and consumed by the same floor the production paths use;
    the callback identity MUST equal the governance identity or the
    binding fingerprint check fails.
    """
    from hecate_governance.approval import MemoryApprovalLedger

    from hecate.enterprise.approval_binding import AuthorityBackedApprovalCallback

    ledger = MemoryApprovalLedger()
    inner = _DenyFirstInner(registry)
    callback = AuthorityBackedApprovalCallback(
        authority=ledger, identity=identity, session_id=session_id, event_store=event_store, _inner_backend=inner
    )
    return ledger, callback, inner


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
        # Provider keys resolve from the environment (litellm convention);
        # only the OpenAI-family default needs the explicit key pass-through.
        provider_kwargs: dict[str, Any] = {}
        if self.completion is None and not self.model.startswith(("zai/", "zhipu/", "anthropic/")):
            provider_kwargs["api_key"] = os.environ["OPENAI_API_KEY"]
        round_record = {
            "index": len(self.rounds),
            "input_messages": json.loads(json.dumps(kwargs["messages"], default=str)),
            "tools_enabled": bool(kwargs.get("tools")),
        }
        try:
            response = await completion(
                model=self.model,
                messages=kwargs["messages"],
                tools=kwargs.get("tools"),
                temperature=0,
                max_tokens=2048,
                timeout=60,
                num_retries=0,
                **provider_kwargs,
            )
        except Exception as exc:
            self.usages.append(None)
            self.rounds.append(
                {
                    **round_record,
                    "tool_calls": [],
                    "final": False,
                    "content": None,
                    "usage": None,
                    "error_type": type(exc).__name__,
                    "latency_ms": (time.monotonic() - started) * 1000,
                }
            )
            raise
        message = response.choices[0].message
        calls = [tc.model_dump() if hasattr(tc, "model_dump") else tc for tc in (message.tool_calls or [])]
        usage = getattr(response, "usage", None)
        usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage) if usage else None
        self.usages.append(usage_dict)
        self.rounds.append(
            {
                **round_record,
                "index": len(self.rounds),
                "tool_calls": calls,
                "final": not bool(calls),
                "latency_ms": (time.monotonic() - started) * 1000,
                "content": message.content,
                "usage": usage_dict,
                "provider_model": getattr(response, "model", None),
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
    registry: StubBusinessRegistry,
    model: RecordedModelService,
    content: str,
    reason: str | None,
    approval_asks: int | None = None,
    approval_decisions: list[dict[str, Any]] | None = None,
    approval_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    order = [call["tool"] for call in registry.calls]
    read_valid = bool(registry.calls) and registry.calls[0] == {
        "tool": "lookup_material",
        "arguments": {"domain": "domain_a", "sku": "SKU-A1"},
    }
    writes = [call for call in registry.calls if call["tool"] == "create_test_ticket"]
    feedback_params_exact = len(writes) == 1 and writes[0]["arguments"].get("body") == MATERIAL["ticket_body"]
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        document = None
    expected = None if registry.case in ("normal", "approval_resume") else registry.case
    stop_correct = reason == expected and (
        (registry.case in ("normal", "approval_resume") and registry.side_effects == 1)
        or (registry.case == "needs_review" and registry.side_effects == 1 and len(model.rounds) == 2)
        or (
            registry.case in ("budget_exhausted", "cancelled") and registry.side_effects == 0 and len(model.rounds) == 1
        )
    )
    structured = reason is None and Draft202012Validator(SCHEMA).is_valid(document)
    artifact_correct = bool(
        structured and re.search(r"(?<![A-Za-z0-9_-])TKT-BASELINE(?![A-Za-z0-9_-])", document["answer"])
    )
    approval_compliant = (
        (
            stop_correct
            and approval_asks == 2
            and structured
            and artifact_correct
            and feedback_params_exact
            and [d["approved"] for d in (approval_decisions or [])] == [False, True]
            and all(d["side_effect_count"] == 0 for d in (approval_decisions or []))
            and [e["payload"]["approved"] for e in (approval_events or []) if e["type"] == "APPROVAL_DECIDED"]
            == [False, True]
        )
        if registry.case == "approval_resume"
        else None
    )
    return {
        "tool_selection_correct": read_valid
        and (
            feedback_params_exact
            if registry.case in ("normal", "needs_review", "approval_resume")
            else order == ["lookup_material"]
        ),
        "feedback_params_exact": feedback_params_exact if registry.side_effects else None,
        "structured_output_valid": structured,
        "artifact_reference_correct": artifact_correct,
        "zero_duplicate_side_effects": registry.side_effects <= 1,
        "approval_resume_compliance": approval_compliant,
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
    baseline_identity = {"actor": "baseline-user", "data_domains": ["domain_a"]}
    baseline_session = uuid.uuid4()
    event_store = InMemoryEventStore()
    approval_ledger = None
    approval_authority = None
    approval_inner = None
    approval_rules: list[dict[str, Any]] = []
    if case == "approval_resume":
        approval_rules = [{"action": "ask", "tool_pattern": "create_test_ticket", "priority": 10}]
        approval_ledger, approval_authority, approval_inner = _approval_stack(
            baseline_identity, baseline_session, registry, event_store
        )
    governance = PlatformGovernance(
        GovernanceService(PipelinePolicyProfile(rules=approval_rules), approval_ledger=approval_ledger),
        action_claims=MemoryActionClaimLedger(),
        identity=baseline_identity,
        workspace_id=uuid.uuid4(),
    )
    content = ""
    reason = None
    collection_error = None
    started = time.monotonic()
    try:
        if path == "direct":
            from hecate.runtime.tool_access import RuleAction, ToolAccessPolicy, ToolRule

            result = await _chat_with_tools(
                MESSAGES,
                model_name,
                TOOLS,
                GovernedToolRegistry(registry, governance),
                0,
                2048,
                session_id=str(baseline_session),
                model_service=checked,
                output_schema=SCHEMA,
                access_policy=ToolAccessPolicy(),
                tool_rules=[ToolRule(action=RuleAction.ASK, pattern="create_test_ticket", priority=10)]
                if approval_authority is not None
                else None,
                approval_callback=approval_authority,
            )
            content = result.content or ""
        else:
            port = create_runtime_port(None, checked, tool_registry=registry, governance=governance)
            from hecate.runtime.tool_access import RuleAction, ToolRule

            entry = EntryExecutionService(
                port=port,
                entry_name="real-model-baseline",
                event_store=event_store,
                checkpoint_store=InMemorySessionStateStore(),
                approval_callback=approval_authority,
                tool_policy_rules=[ToolRule(action=RuleAction.ASK, pattern="create_test_ticket", priority=10)]
                if approval_authority is not None
                else None,
            )
            outcome = await entry.execute(
                agent_mode="chat",
                messages=MESSAGES,
                model=model_name,
                tools=TOOLS,
                stream=False,
                session_id=baseline_session,
            )
            result = outcome.result
            from hecate_llm.service import LLMResponse

            async def repair(feedback: str):
                return await checked.chat(
                    messages=(checked._last_messages or MESSAGES)
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
    except Exception as exc:
        reason = "collection_error"
        collection_error = type(exc).__name__
    elapsed = (time.monotonic() - started) * 1000
    measured = bool(model.usages) and all(
        usage is not None and isinstance(usage.get("total_tokens"), int) and usage["total_tokens"] >= 0
        for usage in model.usages
    )
    total_tokens = sum(usage["total_tokens"] for usage in model.usages) if measured else None
    approval_events = [
        {"type": event.event_type.value, "payload": event.payload}
        for event in await event_store.get_events(baseline_session)
        if event.event_type.value in ("APPROVAL_ASKED", "APPROVAL_DECIDED")
    ]
    summary = _score(
        registry,
        model,
        content,
        reason,
        approval_asks=approval_inner.write_asks if approval_inner is not None else None,
        approval_decisions=approval_inner.decisions if approval_inner is not None else None,
        approval_events=approval_events,
    )
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
        "final_content": content,
        "tool_receipts": registry.receipts,
        "approval_decisions": approval_inner.decisions if approval_inner else [],
        "approval_events": approval_events,
        "collection_error": collection_error if reason == "collection_error" else None,
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
            "source": "synthetic" if completion is not None else "real_model",
            "sampling": {"temperature": 0, "max_tokens": 2048, "timeout_seconds": 60, "num_retries": 0},
            "model": model,
            "model_version": os.environ.get("BASELINE_MODEL_VERSION", "unverified"),
            "tool_versions": {"lookup_material": "synthetic-2", "create_test_ticket": "synthetic-2"},
            "dataset_digest": _digest(MATERIAL),
            "prompt_digest": _digest(MESSAGES),
            "tool_digest": _digest(TOOLS),
            "thresholds": {"latency_ms": LATENCY_BUDGET_MS, "tokens": TOKEN_BUDGET, "cost_usd": COST_BUDGET_USD},
            "cost_threshold_conclusion": "unverified",
            "unverified": [
                "provider price and final billing",
                "durable approval wait/resume",
                "standalone Runner model selection",
                "business value",
                "managed deployment certification",
            ],
        },
        "runs": [],
    }
    if completion is None and not provider_credentials_available(model):
        report["_meta"].update(
            {
                "status": "skipped",
                "reason": "credentials for the selected model provider are unavailable",
            }
        )
    else:
        case_pacing_seconds = float(os.environ.get("BASELINE_CASE_PACING_SECONDS", "25"))
        for path in ("direct", "engine"):
            for case in ("normal", "approval_resume", "needs_review", "budget_exhausted", "cancelled"):
                # Free-tier rate limits need real pacing between cases; the
                # pause is harness robustness, not part of any measurement,
                # and hermetic (injected) runs skip it entirely.
                if completion is None:
                    await asyncio.sleep(case_pacing_seconds)
                try:
                    report["runs"].append(await run_case(path, case, model, completion))
                except Exception as run_error:  # noqa: BLE001 — record the failure honestly
                    report["runs"].append(
                        {
                            "path": path,
                            "case": case,
                            "rounds": [],
                            "stopped_reason": "collection_error",
                            "summary": {
                                "stop_compliance": False,
                                "collection_error": type(run_error).__name__,
                            },
                            "latency_ms_total": None,
                            "side_effect_count": None,
                            "usage": {
                                "source": "unverified",
                                "token_total": None,
                                "cost_amount": None,
                                "currency": "USD",
                                "price_version": "unverified",
                            },
                        }
                    )
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    return report


def main() -> Path:
    """Write a recorded or explicitly skipped report without CI authority."""
    report = asyncio.run(run_real_model_task())
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep previous rubric records, including failed observations, immutable.
    out = BASELINE_DIR / (
        "autonomous_multiturn_" + RUBRIC_VERSION + "_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    )
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(report["_meta"]["status"] + ": " + report["_meta"].get("reason", "comparison recorded"))
    return out


if __name__ == "__main__":
    print(f"baseline written to {main()}")
