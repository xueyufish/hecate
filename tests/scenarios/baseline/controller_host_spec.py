"""Shared scenario contract for the Controller two-host real-model baseline.

One dataset, one tool contract, one prompt pair, one output schema and one
scoring rubric are consumed by both host drivers (platform dispatch and the
standalone runner engine). Real provider selection stays behind a minimal
provider interface: the collector never scripts action choice — the provider
(calls litellm in real mode, a deterministic stand-in in hermetic mode)
returns content/tool_calls/usage and the hosts govern and execute them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from jsonschema import Draft202012Validator

RUBRIC_VERSION = "controller-host-rubric-2"
SAMPLING = {"temperature": 0, "max_tokens": 2048, "timeout_seconds": 25, "num_retries": 0}
LATENCY_BUDGET_MS = 120_000.0
TOKEN_BUDGET = 40_000
COST_BUDGET_USD = 1.0
REQUEST_LIMIT = 8
QUALITY_CASES = ("normal", "approval_resume")

# The synthetic business surface is identical on both hosts: the same tool
# names, JSON schemas and data. The platform serves them from a local
# synthetic registry behind the real governance facade; the runner reaches
# them through its business-API dispatch mapping.
READ_TOOL = "query_inventory"
WRITE_TOOL = "submit_inventory_update"
RECEIPT_ID = "UPD-BASELINE"
EXPECTED_READ = {"domain": "domain_a", "sku": "SKU-A1"}
EXPECTED_WRITE = {"domain": "domain_a", "sku": "SKU-A1", "quantity": 3}

MATERIAL = {
    "status": "ok",
    "stock": "confirmed",
    "reorder": True,
    "quantity": 3,
    "update_body": "SKU-A1 confirmed by warehouse-A",
}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    READ_TOOL: {
        "type": "object",
        "required": ["domain", "sku"],
        "properties": {"domain": {"type": "string", "minLength": 1}, "sku": {"type": "string", "minLength": 1}},
    },
    WRITE_TOOL: {
        "type": "object",
        "required": ["domain", "sku", "quantity"],
        "properties": {
            "domain": {"type": "string", "minLength": 1},
            "sku": {"type": "string", "minLength": 1},
            "quantity": {"type": "integer", "minimum": 1},
        },
    },
}


def tool_definitions() -> list[dict[str, Any]]:
    """OpenAI function-calling definitions shared verbatim by both hosts."""

    return [
        {"type": "function", "function": {"name": name, "parameters": TOOL_SCHEMAS[name]}}
        for name in (READ_TOOL, WRITE_TOOL)
    ]


SYSTEM_PROMPT = (
    "Read stock first. If reorder is true, submit exactly one inventory update "
    "using the returned quantity. Return final output as JSON "
    '{"answer":"..."} including the resulting update_id.'
)
USER_PROMPT = "Check stock for SKU-A1 in domain_a and submit the reorder update."

OUTPUT_SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}

CASES = ("normal", "approval_resume", "cancelled", "budget_exhausted", "unknown_outcome")
HOSTS = ("platform", "runner")

# Pre-registered stop expectations per host. The two hosts classify stops
# with their own real mechanisms; the rubric asserts each host's actual
# classification instead of forcing one shared vocabulary.
EXPECTED_STOPS: dict[str, dict[str, dict[str, Any]]] = {
    "platform": {
        "normal": {"task_status": "succeeded", "error_category": None},
        "approval_resume": {"task_status": "succeeded", "error_category": None},
        "cancelled": {"task_status": "cancelled", "error_category": None},
        # Declared harness context exceeds the pinned token budget: the
        # context chain rejects the turn before any model call.
        "budget_exhausted": {"task_status": "failed", "error_category": "controller_token_capped"},
        # The read's outcome is indeterminate: the attempt stays unreconciled.
        "unknown_outcome": {
            "task_status": "reconciliation_required",
            "error_category": None,
            "reconciliation_reason": "action outcome is not established",
        },
    },
    "runner": {
        "normal": {"task_status": "succeeded", "error_category": None},
        "approval_resume": {"task_status": "succeeded", "error_category": None},
        "cancelled": {"task_status": "cancelled", "error_category": None},
        # The durable model-call budget ledger refuses the reservation.
        "budget_exhausted": {"task_status": "failed", "error_contains": "budget"},
        "unknown_outcome": {"task_status": "reconciliation_required", "error_category": None},
    },
}

REPORT_SCHEMA = {
    "type": "object",
    "required": ["_meta", "runs"],
    "properties": {
        "_meta": {
            "type": "object",
            "required": [
                "status",
                "gate",
                "source",
                "cost_threshold_conclusion",
                "rubric_version",
                "model",
                "sampling",
                "source_revision",
                "output_schema_digest",
                "cases",
                "limits",
                "thresholds",
                "hosts",
                "collection",
                "unverified",
            ],
            "properties": {
                "sampling": {"const": SAMPLING},
                "rubric_version": {"const": RUBRIC_VERSION},
                "limits": {"const": {"requests_per_case": REQUEST_LIMIT, "seconds_per_case": 120}},
                "status": {"enum": ["recorded", "skipped"]},
                "gate": {"const": False},
                "source": {"enum": ["real_model", "deterministic_injection"]},
                "cost_threshold_conclusion": {"const": "unverified"},
                "thresholds": {
                    "const": {"latency_ms": LATENCY_BUDGET_MS, "tokens": TOKEN_BUDGET, "cost_usd": COST_BUDGET_USD}
                },
            },
        },
        "runs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["host", "case", "task_id", "run_id", "source", "rounds", "summary", "usage", "stop"],
                "properties": {
                    "host": {"enum": list(HOSTS)},
                    "case": {"enum": list(CASES)},
                    "source": {"enum": ["real_model", "deterministic_injection"]},
                },
            },
        },
    },
}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def provider_credentials_available(model: str) -> bool:
    """Check the selected provider without exposing credential values."""

    key = "OPENAI_API_KEY"
    if model.startswith(("zai/", "zhipu/")):
        key = "ZAI_API_KEY"
    elif model.startswith("anthropic/"):
        key = "ANTHROPIC_API_KEY"
    return bool(os.environ.get(key))


def _structured(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None


def _stop_matches(host: str, case: str, observation: dict[str, Any]) -> bool:
    expected = EXPECTED_STOPS[host][case]
    if observation.get("task_status") != expected["task_status"]:
        return False
    if "error_category" in expected and observation.get("error_category") != expected["error_category"]:
        return False
    if (
        "reconciliation_reason" in expected
        and observation.get("reconciliation_reason") != expected["reconciliation_reason"]
    ):
        return False
    if "error_contains" in expected:
        error = observation.get("error") or ""
        if expected["error_contains"] not in error:
            return False
    return True


def _artifact_correct(content: str | None) -> bool:
    document = _structured(content)
    if document is None or not isinstance(document.get("answer"), str):
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9_-]){RECEIPT_ID}(?![A-Za-z0-9_-])", document["answer"]))


def has_read_receipt(messages: list[dict[str, Any]]) -> bool:
    """Recognize the recorded read payload inside either host's result envelope."""
    for message in messages:
        if message.get("role") != "tool":
            continue
        payload = _structured(message.get("content"))
        if payload == MATERIAL or (payload is not None and payload.get("result") == MATERIAL):
            return True
    return False


def score_run(case: str, host: str, observation: dict[str, Any]) -> dict[str, Any]:
    """Score one case observation under the shared rubric.

    The rubric never compares action-sequence text; it checks tool
    selection, feedback-derived parameters, structured delivery, artifact
    reference, side-effect counts, and each host's pre-registered stop
    classification.
    """

    calls = observation.get("tool_calls") or []
    reads = [call for call in calls if call["tool"] == READ_TOOL]
    first_read = bool(calls) and calls[0]["tool"] == READ_TOOL and calls[0]["arguments"] == EXPECTED_READ
    read_once = len(reads) == 1
    writes = [call for call in calls if call["tool"] == WRITE_TOOL]
    feedback_params_exact = len(writes) == 1 and writes[0]["arguments"] == EXPECTED_WRITE
    structured = observation.get("error_category") is None and Draft202012Validator(OUTPUT_SCHEMA).is_valid(
        _structured(observation.get("final_content"))
    )
    stop_correct = _stop_matches(host, case, observation)
    side_effects = observation.get("side_effect_count")
    write_attempts = observation.get("write_attempts")

    summary: dict[str, Any] = {
        "tool_selection_correct": first_read,
        "feedback_params_exact": feedback_params_exact if writes or side_effects else None,
        "structured_output_valid": structured,
        "artifact_reference_correct": _artifact_correct(observation.get("final_content")),
        "zero_duplicate_side_effects": side_effects is not None and side_effects <= 1,
        "stop_compliance": stop_correct,
    }
    if case == "normal":
        summary["delivery_compliance"] = bool(
            stop_correct
            and first_read
            and read_once
            and feedback_params_exact
            and structured
            and summary["artifact_reference_correct"]
            and side_effects == 1
            and write_attempts == 1
        )
    elif case == "approval_resume":
        approval = observation.get("approval") or {}
        rebuild = observation.get("rebuild") or {}
        decisions = approval.get("decisions") or []
        summary["approval_resume_compliance"] = bool(
            stop_correct
            and first_read
            and read_once
            and feedback_params_exact
            and structured
            and summary["artifact_reference_correct"]
            and approval.get("parked") is True
            and approval.get("write_attempts_before_park") == 0
            and [decision.get("approved") for decision in decisions] == [True]
            and rebuild.get("performed") is True
            and rebuild.get("same_run") is True
            and observation.get("post_resume_model_input_has_receipt") is True
            and observation.get("post_resume_model_input_has_read_receipt") is True
            and (host != "runner" or approval.get("authority_consumed") is True)
            and side_effects == 1
            and write_attempts == 1
        )
    elif case == "cancelled":
        summary["cancel_compliance"] = bool(
            stop_correct and write_attempts == 0 and side_effects == 0 and first_read and read_once
        )
    elif case == "budget_exhausted":
        summary["budget_compliance"] = bool(
            stop_correct and write_attempts == 0 and side_effects == 0 and observation.get("model_calls") == 0
        )
    elif case == "unknown_outcome":
        summary["unknown_stop_compliance"] = bool(
            stop_correct and write_attempts == 0 and side_effects == 0 and observation.get("model_calls") == 1
        )
    return summary


class DeterministicProvider:
    """Hermetic provider stand-in: reads results, then acts on them.

    This is the injected provider used by report tests and ``--hermetic``
    collection. It emulates a compliant model — including deriving the
    write parameters from the read receipt — so the harness is validated
    end to end without any network call. It is never a quality result.
    """

    name = "deterministic-injection"

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        tool_messages = [message for message in messages if isinstance(message, dict) and message.get("role") == "tool"]
        if not tool_messages:
            return self._reply([], READ_TOOL, dict(EXPECTED_READ))
        receipt_seen = any(RECEIPT_ID in str(message.get("content")) for message in tool_messages)
        if receipt_seen:
            quantity = EXPECTED_WRITE["quantity"]
            answer = (
                f"Stock confirmed for SKU-A1 in domain_a; reorder update {RECEIPT_ID} "
                f"submitted with quantity {quantity}."
            )
            return {
                "content": json.dumps({"answer": answer}),
                "tool_calls": [],
                "usage": None,
                "provider_model": self.name,
            }
        quantity = MATERIAL["quantity"]
        for message in reversed(tool_messages):
            try:
                payload = json.loads(str(message.get("content")))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("quantity"), int):
                quantity = payload["quantity"]
                break
        return self._reply([], WRITE_TOOL, {**EXPECTED_WRITE, "quantity": quantity})

    @staticmethod
    def _reply(usage: list[Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "",
            "tool_calls": [
                {"id": "call-deterministic", "type": "function", "function": {"name": tool, "arguments": arguments}}
            ],
            "usage": None,
            "provider_model": "deterministic-injection",
        }


class FailAfterProvider:
    """Wrapper that records a provider outage after the given call count."""

    def __init__(self, inner: Any, fail_after: int) -> None:
        self.inner = inner
        self.fail_after = fail_after
        self.calls = 0
        self.name = getattr(inner, "name", "wrapped")

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        self.calls += 1
        if self.calls > self.fail_after:
            raise ConnectionError("synthetic provider outage")
        return await self.inner.complete(messages=messages, tools=tools)
