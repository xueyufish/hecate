"""Recorded Controller two-host baseline: platform dispatch vs runner host.

Run manually with ``python -m tests.scenarios.baseline.run_controller_host_baseline``
(hermetic deterministic injection: ``--hermetic``). Both hosts run the same
dataset, tool contract, prompt pair, output schema, sampling parameters and
scoring rubric. Real collection requires ``HECATE_RUN_CONTROLLER_HOST_BASELINE=1``
plus the provider credential; anything less produces an explicit skipped
record, never a quality or production certificate. Deterministic fault
injection (cancel, budget, unknown outcome) is reported separately from any
real-model quality result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tests.scenarios.baseline import controller_host_platform, controller_host_runner
from tests.scenarios.baseline import controller_host_spec as spec

BASELINE_DIR = Path(__file__).resolve().parent.parent / "baselines"
REAL_MODE_GATE_ENV = "HECATE_RUN_CONTROLLER_HOST_BASELINE"
# Pre-registered real-mode sampling; identical for both hosts.
SAMPLING = dict(spec.SAMPLING)


class LiteLLMProvider:
    """Real provider adapter: litellm acCompletion → the shared reply shape.

    The adapter converts the provider response only; it never selects or
    edits tool calls. Usage and the provider-reported model version are
    recorded verbatim; a missing version stays ``unverified``.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.name = model

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        from litellm import acompletion

        provider_kwargs: dict[str, Any] = {}
        if not self.model.startswith(("zai/", "zhipu/", "anthropic/")):
            provider_kwargs["api_key"] = os.environ["OPENAI_API_KEY"]
        response = await acompletion(
            model=self.model,
            messages=messages,
            tools=tools or None,
            temperature=SAMPLING["temperature"],
            max_tokens=SAMPLING["max_tokens"],
            timeout=SAMPLING["timeout_seconds"],
            num_retries=SAMPLING["num_retries"],
            **provider_kwargs,
        )
        message = response.choices[0].message
        calls = [call.model_dump() if hasattr(call, "model_dump") else call for call in (message.tool_calls or [])]
        usage = getattr(response, "usage", None)
        usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else (dict(usage) if usage else None)
        return {
            "content": message.content,
            "tool_calls": calls,
            "usage": usage_dict,
            "provider_model": getattr(response, "model", None) or "unverified",
        }


class _BoundedProvider:
    """Bound fresh provider requests independently of replayed Controller turns."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.calls = 0
        self.tokens = 0

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        if self.calls >= spec.REQUEST_LIMIT or self.tokens >= spec.TOKEN_BUDGET:
            raise RuntimeError("baseline provider request/token limit reached")
        self.calls += 1
        reply = await self.provider.complete(messages=messages, tools=tools)
        tokens = (reply.get("usage") or {}).get("total_tokens")
        if type(tokens) is int and tokens >= 0:
            self.tokens += tokens
        return reply


async def _run_case(host: str, case: str, provider: Any, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    source = "real_model" if isinstance(provider, LiteLLMProvider) else "deterministic_injection"
    provider = _BoundedProvider(provider)
    try:
        if host == "platform":
            observation = await controller_host_platform.run_platform_case(case, provider, root)
        else:
            observation = await controller_host_runner.run_runner_case(case, provider, root)
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = {
            "task_status": None,
            "final_content": None,
            "error_category": None,
            "error": None,
            "reconciliation_reason": None,
            "model_calls": None,
            "rounds": [],
            "tool_calls": [],
            "tool_receipts": [],
            "ledger_actions": [],
            "write_attempts": None,
            "side_effect_count": None,
            "approval": None,
            "rebuild": {"performed": False},
            "post_resume_model_input_has_receipt": None,
            "latency_ms_total": (time.monotonic() - started) * 1000,
            "collection_error": f"{type(exc).__name__}: {exc}",
            "usage": {
                "source": "unverified",
                "token_total": None,
                "cost_amount": None,
                "currency": "USD",
                "price_version": "unverified",
            },
        }
    summary = spec.score_run(case, host, observation)
    summary.update(
        {
            "within_latency_threshold": (
                observation.get("latency_ms_total") is not None
                and observation["latency_ms_total"] <= spec.LATENCY_BUDGET_MS
            ),
            "within_token_threshold": (
                observation["usage"]["token_total"] <= spec.TOKEN_BUDGET
                if observation["usage"]["token_total"] is not None
                else None
            ),
            "within_cost_threshold": None,
        }
    )
    return {
        "host": host,
        "case": case,
        "task_id": observation.get("task_id"),
        "run_id": observation.get("run_id"),
        "source": source,
        "mechanisms": _mechanisms(host, case),
        "rounds": observation.get("rounds") or [],
        "final_content": observation.get("final_content"),
        "tool_receipts": observation.get("tool_receipts") or [],
        "tool_calls": observation.get("tool_calls") or [],
        "ledger_actions": observation.get("ledger_actions") or [],
        "approval": observation.get("approval"),
        "rebuild": observation.get("rebuild") or {"performed": False},
        "post_resume_model_input_has_receipt": observation.get("post_resume_model_input_has_receipt"),
        "post_resume_model_input_has_read_receipt": observation.get("post_resume_model_input_has_read_receipt"),
        "collection_error": observation.get("collection_error"),
        "stop": {
            "task_status": observation.get("task_status"),
            "error_category": observation.get("error_category"),
            "error": observation.get("error"),
            "reconciliation_reason": observation.get("reconciliation_reason"),
        },
        "summary": summary,
        "latency_ms_total": observation.get("latency_ms_total"),
        "model_calls": observation.get("model_calls"),
        "write_attempts": observation.get("write_attempts"),
        "side_effect_count": observation.get("side_effect_count"),
        "usage": observation.get("usage"),
    }


def _mechanisms(host: str, case: str) -> dict[str, str]:
    """Honest per-case mechanism labels (what was real, what was injected)."""

    mechanisms: dict[str, str] = {
        "approval": (
            "real guardrail policy row (ASK) + ApprovalWaitSignal park + "
            + (
                "ApprovalDecisionService resume after host reassembly"
                if host == "platform"
                else "apply_wake grant + engine.resume after host rebuild"
            )
            if case == "approval_resume"
            else "real policy path; no approval needed"
        ),
        "tools": "local synthetic business data behind the host's real governance path",
        "storage": (
            "sqlite (WAL) durable store; in-process host reassembly — not a "
            "PostgreSQL or cross-process recovery certification"
            if host == "platform"
            else "sqlite durable store; engine/store reassembly over the same "
            "persisted state — not a PostgreSQL or cross-process recovery certification"
        ),
    }
    if case == "cancelled":
        mechanisms["cancel"] = (
            "real CANCEL command applied at the approval park (the platform "
            "has no cooperative abort for a running loop)"
            if host == "platform"
            else "real cooperative request_cancel mid-loop"
        )
    elif case == "budget_exhausted":
        mechanisms["budget"] = (
            "declared harness context exceeds the pinned token budget (context-chain stop before any model call)"
            if host == "platform"
            else "durable model-call budget ledger refuses the reservation before any model call"
        )
    elif case == "unknown_outcome":
        mechanisms["unknown"] = "synthetic lost tool receipt surfaces the host's real outcome-unknown path"
    return mechanisms


def _source_identity() -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[3]

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    try:
        return {
            "head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
            "collector_files": {
                str(path.relative_to(repo)): spec.digest(path.read_text(encoding="utf-8"))
                for path in sorted({Path(__file__), *Path(__file__).parent.glob("controller_host_*.py")})
            },
        }
    except (OSError, subprocess.CalledProcessError):
        return {"head": "unverified", "dirty": None}


def _collection_block(runs: list[dict[str, Any]], source: str, model: str) -> dict[str, Any]:
    """Separate collector completion, real-collection status and quality."""

    real_runs = [run for run in runs if run["source"] == "real_model"]
    deterministic_runs = [run for run in runs if run["source"] == "deterministic_injection"]
    quality = {"real_model_cases": 0, "delivery_compliance": None, "approval_resume_compliance": None}
    if real_runs:
        quality = {
            "real_model_cases": len(real_runs),
            "delivery_compliance": [
                run["summary"].get("delivery_compliance") for run in real_runs if run["case"] == "normal"
            ],
            "approval_resume_compliance": [
                run["summary"].get("approval_resume_compliance")
                for run in real_runs
                if run["case"] == "approval_resume"
            ],
        }
    return {
        "collector": {
            "cases_attempted": len(runs),
            "cases_recorded": len([run for run in runs if run["collection_error"] is None]),
            "cases_with_collection_error": len([run for run in runs if run["collection_error"] is not None]),
        },
        "real_model": {
            "enabled": source == "real_model",
            "quality_conclusion": "recorded_below" if source == "real_model" else "not_available",
            **({"quality": quality} if real_runs else {}),
        },
        "deterministic_injection": {
            "cases": len(deterministic_runs),
            "stop_compliance_passed": len(
                [run for run in deterministic_runs if run["summary"].get("stop_compliance") is True]
            ),
            "note": "harness and stop-classification evidence; never a real-model quality result",
        },
        "model": model,
    }


async def collect(
    *,
    provider: Any | None = None,
    model: str | None = None,
    hosts: tuple[str, ...] = spec.HOSTS,
    cases: tuple[str, ...] = spec.CASES,
    root: Path | None = None,
    case_pacing_seconds: float = 0.0,
) -> dict[str, Any]:
    """Collect the two-host matrix and return the validated report."""

    hermetic = provider is not None
    if not hosts or len(set(hosts)) != len(hosts) or any(host not in spec.HOSTS for host in hosts):
        raise ValueError("hosts must be a nonempty unique subset of platform,runner")
    if not cases or len(set(cases)) != len(cases) or any(case not in spec.CASES for case in cases):
        raise ValueError("cases must be a nonempty unique subset of the registered cases")
    resolved_model = model or (provider.name if provider is not None else os.environ.get("BASELINE_MODEL", "gpt-4o"))
    report: dict[str, Any] = {
        "_meta": {
            "kind": "controller_host_baseline",
            "status": "recorded",
            "gate": False,
            "business_value": "unverified",
            "recorded_at": datetime.now(UTC).isoformat(),
            "rubric_version": spec.RUBRIC_VERSION,
            "source_revision": _source_identity(),
            "output_schema_digest": spec.digest(spec.OUTPUT_SCHEMA),
            "cases": list(cases),
            "source": "deterministic_injection" if hermetic else "real_model",
            "sampling": dict(SAMPLING),
            "limits": {"requests_per_case": spec.REQUEST_LIMIT, "seconds_per_case": 120},
            "model": resolved_model,
            "model_version": "unverified",
            "tool_versions": {spec.READ_TOOL: "synthetic-1", spec.WRITE_TOOL: "synthetic-1"},
            "dataset_digest": spec.digest(spec.MATERIAL),
            "prompt_digest": spec.digest([spec.SYSTEM_PROMPT, spec.USER_PROMPT]),
            "tool_digest": spec.digest(spec.tool_definitions()),
            "thresholds": {
                "latency_ms": spec.LATENCY_BUDGET_MS,
                "tokens": spec.TOKEN_BUDGET,
                "cost_usd": spec.COST_BUDGET_USD,
            },
            "cost_threshold_conclusion": "unverified",
            "hosts": list(hosts),
            "unverified": [
                "provider price and final billing",
                "model quality below (deterministic-injection runs measure the harness, not the model)",
                "cross-process and PostgreSQL recovery (this record rebuilds hosts in-process over sqlite)",
                "managed deployment certification",
                "business value",
            ],
        },
        "runs": [],
    }
    if not hermetic:
        gate_open = os.environ.get(REAL_MODE_GATE_ENV) == "1"
        credentials = spec.provider_credentials_available(resolved_model)
        if not gate_open or not credentials:
            report["_meta"]["status"] = "skipped"
            report["_meta"]["reason"] = (
                f"{REAL_MODE_GATE_ENV} must be 1 to enable real provider calls"
                if not gate_open
                else "credentials for the selected model provider are unavailable"
            )
            report["_meta"]["collection"] = _collection_block([], "skipped", resolved_model)
            Draft202012Validator(spec.REPORT_SCHEMA).validate(report)
            return report
        provider = LiteLLMProvider(resolved_model)
    work_root = root or Path(tempfile.mkdtemp(prefix="controller-host-baseline-"))
    work_root.mkdir(parents=True, exist_ok=True)
    for host in hosts:
        for case in cases:
            # Free-tier rate limits need real pacing between real cases; the
            # pause is harness robustness, not part of any measurement, and
            # hermetic runs skip it entirely.
            if not hermetic and case_pacing_seconds > 0:
                await asyncio.sleep(case_pacing_seconds)
            case_provider = provider if hermetic or case in spec.QUALITY_CASES else spec.DeterministicProvider()
            report["runs"].append(await _run_case(host, case, case_provider, work_root))
    report["_meta"]["collection"] = _collection_block(report["runs"], report["_meta"]["source"], resolved_model)
    Draft202012Validator(spec.REPORT_SCHEMA).validate(report)
    return report


def write_report(report: dict[str, Any]) -> Path:
    """Write a new immutable record; previous records are never overwritten."""

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    out = BASELINE_DIR / f"controller_host_baseline_{spec.RUBRIC_VERSION}_{stamp}.json"
    with out.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return out


def main(argv: list[str] | None = None) -> Path:
    """CLI entry: hermetic deterministic injection or gated real collection."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermetic", action="store_true", help="run the deterministic-injection matrix (no network)")
    parser.add_argument("--model", default=None, help="provider model for real collection (default gpt-4o)")
    parser.add_argument("--hosts", default="platform,runner", help="comma-separated hosts to collect")
    parser.add_argument(
        "--pacing-seconds", type=float, default=25.0, help="pause between real cases (free-tier rate limits)"
    )
    args = parser.parse_args(argv)
    if args.hermetic:
        report = asyncio.run(
            collect(
                provider=spec.DeterministicProvider(),
                hosts=tuple(host.strip() for host in args.hosts.split(",") if host.strip()),
            )
        )
    else:
        report = asyncio.run(
            collect(
                model=args.model,
                hosts=tuple(host.strip() for host in args.hosts.split(",") if host.strip()),
                case_pacing_seconds=args.pacing_seconds,
            )
        )
    out = write_report(report)
    meta = report["_meta"]
    print(meta["status"] + ": " + meta.get("reason", "controller host comparison recorded"))
    print(f"baseline written to {out}")
    return out


if __name__ == "__main__":
    main()
