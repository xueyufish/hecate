"""Recorded platform feedback baseline: correction, no-reorder, unknown.

Run manually with
``python -m tests.scenarios.baseline.run_controller_feedback_baseline``.
Three paired cases over the same synthetic inventory data run on the
real platform dispatch: a rejected update whose corrected quantity the
model must consume, a no-reorder read that must end without writing,
and a landed write whose receipt is lost into the outcome-unknown path.
The default run is the deterministic-injection matrix. Real model
collection reuses the dual-host entry conditions (the explicit gate
plus a matching credential) and applies only to the two quality cases;
the unknown-write case stays the deterministic fault injection and is
labeled as such. A real-mode failure never falls back to the
deterministic provider. The module registers the ``platform_feedback``
scenario adapter with the experiment layer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_feedback_platform as driver
from tests.scenarios.baseline import controller_host_feedback_spec as fspec
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import run_controller_host_baseline as collector
from tests.scenarios.baseline.run_controller_host_baseline import LiteLLMProvider, _BoundedProvider

BASELINE_DIR = collector.BASELINE_DIR

TRIAL_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["_meta", "runs"],
    "properties": {
        "_meta": {
            "type": "object",
            "required": [
                "kind",
                "status",
                "gate",
                "source",
                "rubric_version",
                "scenario",
                "sampling",
                "limits",
                "thresholds",
                "source_revision",
                "cases",
                "hosts",
                "collection",
                "unverified",
            ],
            "properties": {
                "kind": {"const": "controller_host_feedback"},
                "status": {"enum": ["recorded", "skipped"]},
                "gate": {"const": False},
                "source": {"enum": ["deterministic_injection", "real_model"]},
                "rubric_version": {"const": fspec.RUBRIC_VERSION},
                "scenario": {"const": fspec.SCENARIO_ID},
                "sampling": {"const": fspec.SAMPLING},
                "limits": {"const": fspec.LIMITS},
                "thresholds": {"const": fspec.THRESHOLDS},
            },
        },
        "runs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["host", "case", "source", "summary", "feedback", "usage", "stop"],
                "properties": {
                    "host": {"const": "platform"},
                    "case": {"enum": list(fspec.FEEDBACK_CASES)},
                    "source": {"enum": ["deterministic_injection", "real_model"]},
                },
            },
        },
    },
}


async def _run_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    # Model provenance follows the provider driving the loop; the fault
    # source is labeled separately for the unknown-write injection.
    source = "real_model" if isinstance(provider, LiteLLMProvider) else "deterministic_injection"
    fault_source = "deterministic_injection" if case not in fspec.QUALITY_CASES else None
    provider = _BoundedProvider(provider)
    try:
        observation = await driver.run_feedback_case(case, provider, root)
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = {
            "task_id": None,
            "run_id": None,
            "run_count": None,
            "rounds": [],
            "tool_receipts": [],
            "tool_calls": [],
            "business_calls": [],
            "ledger_actions": [],
            "final_content": None,
            "model_calls": None,
            "write_attempts": None,
            "side_effect_count": None,
            "approval": None,
            "rebuild": {"performed": False},
            "stop": {"task_status": None, "error_category": None, "error": None, "reconciliation_reason": None},
            "latency_ms_total": (time.monotonic() - started) * 1000,
            "collection_error": f"{type(exc).__name__}: {exc}",
            "feedback": {
                "rejections": [],
                "business_write_count": None,
                "business_attempts": None,
                "responder": "deterministic_injection",
            },
            "usage": {
                "source": "unverified",
                "token_total": None,
                "cost_amount": None,
                "currency": "USD",
                "price_version": "unverified",
            },
        }
    summary = fspec.score_feedback_run(case, observation)
    summary["within_latency_threshold"] = (
        observation.get("latency_ms_total") is not None
        and observation["latency_ms_total"] <= fspec.THRESHOLDS["latency_ms"]
    )
    summary["within_token_threshold"] = (
        observation["usage"]["token_total"] <= fspec.THRESHOLDS["tokens"]
        if observation["usage"]["token_total"] is not None
        else None
    )
    summary["within_cost_threshold"] = None
    return {
        "host": "platform",
        "case": case,
        "task_id": observation.get("task_id"),
        "run_id": observation.get("run_id"),
        "run_count": observation.get("run_count"),
        "source": source,
        "fault_source": fault_source,
        "rounds": observation.get("rounds") or [],
        "final_content": observation.get("final_content"),
        "tool_receipts": observation.get("tool_receipts") or [],
        "tool_calls": observation.get("tool_calls") or [],
        "business_calls": observation.get("business_calls") or [],
        "ledger_actions": observation.get("ledger_actions") or [],
        "approval": None,
        "rebuild": {"performed": False},
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
        "feedback": observation.get("feedback") or {},
    }


async def collect(
    *,
    provider: Any | None = None,
    model: str | None = None,
    hosts: tuple[str, ...] = fspec.HOSTS,
    cases: tuple[str, ...] = fspec.FEEDBACK_CASES,
    root: Path | None = None,
    case_pacing_seconds: float = 0.0,
) -> dict[str, Any]:
    """Collect the feedback matrix and return the validated report."""

    if not hosts or tuple(hosts) != fspec.HOSTS:
        raise ValueError("the feedback baseline runs on the platform host only")
    if not cases or len(set(cases)) != len(cases) or any(case not in fspec.FEEDBACK_CASES for case in cases):
        raise ValueError("cases must be a nonempty unique subset of the registered feedback cases")
    real_provider = isinstance(provider, LiteLLMProvider)
    hermetic = provider is not None and not real_provider
    if real_provider:
        if model is not None and model != provider.model:
            raise ValueError("model must match the injected real provider")
        resolved_model = provider.model
    else:
        resolved_model = model or (
            provider.name if provider is not None else os.environ.get("BASELINE_MODEL", "gpt-4o")
        )
    source_identity = collector._source_identity()
    if "collector_files" in source_identity:
        source_identity["collector_files"]["tests/scenarios/baseline/run_controller_feedback_baseline.py"] = (
            spec.digest(Path(__file__).read_text(encoding="utf-8"))
        )
    report: dict[str, Any] = {
        "_meta": {
            "kind": "controller_host_feedback",
            "status": "recorded",
            "gate": False,
            "business_value": "unverified",
            "recorded_at": datetime.now(UTC).isoformat(),
            "rubric_version": fspec.RUBRIC_VERSION,
            "scenario": fspec.SCENARIO_ID,
            "source_revision": source_identity,
            "output_schema_digest": spec.digest(fspec.OUTPUT_SCHEMA),
            "scenario_digest": fspec.scenario_digest(),
            "cases": list(cases),
            "source": "deterministic_injection" if hermetic else "real_model",
            "sampling": dict(fspec.SAMPLING),
            "limits": dict(fspec.LIMITS),
            "thresholds": dict(fspec.THRESHOLDS),
            "cost_threshold_conclusion": "unverified",
            "model": "deterministic-injection" if hermetic else resolved_model,
            "model_version": "unverified",
            "tool_versions": {fspec.READ_TOOL: "synthetic-1", fspec.WRITE_TOOL: "synthetic-1"},
            "dataset_digest": spec.digest(spec.MATERIAL),
            # Three separately labeled sources: the model above, the
            # business data here, and the per-run fault_source plus the
            # responder inside each run's feedback block.
            "dataset_source": "synthetic_inventory",
            "prompt_digest": spec.digest([fspec.SYSTEM_PROMPT, fspec.USER_PROMPT]),
            "tool_digest": spec.digest(fspec.tool_definitions()),
            "hosts": list(hosts),
            "collection": {
                "cases_attempted": 0,
                "cases_recorded": 0,
                "cases_with_collection_error": 0,
            },
            "unverified": [
                "real-model behavior when no real trials are recorded below",
                "real human wait time (the injected responder answers immediately; no human waited)",
                "provider price and final billing",
                "real business samples (the synthetic inventory fixture is not production work)",
                "business value",
            ],
        },
        "runs": [],
    }
    if not hermetic:
        # Supplied real providers obey the same entry conditions as CLI models.
        gate_open = os.environ.get(collector.REAL_MODE_GATE_ENV) == "1"
        credentials = spec.provider_credentials_available(resolved_model)
        if not gate_open or not credentials:
            report["_meta"]["status"] = "skipped"
            report["_meta"]["reason"] = (
                f"{collector.REAL_MODE_GATE_ENV} must be 1 to enable real provider calls"
                if not gate_open
                else "credentials for the selected model provider are unavailable"
            )
            report["_meta"]["collection"] = {
                "cases_attempted": 0,
                "cases_recorded": 0,
                "cases_with_collection_error": 0,
            }
            Draft202012Validator(TRIAL_REPORT_SCHEMA).validate(report)
            return report
    work_root = root or Path(tempfile.mkdtemp(prefix="controller-feedback-"))
    work_root.mkdir(parents=True, exist_ok=True)
    for case in cases:
        # Free-tier rate limits need real pacing between real cases; the
        # pause is harness robustness, not part of any measurement.
        if not hermetic and case_pacing_seconds > 0:
            await asyncio.sleep(case_pacing_seconds)
        if hermetic or case not in fspec.QUALITY_CASES:
            # The unknown-write case is always the deterministic fault
            # injection, in every mode.
            case_provider = provider if hermetic else fspec.DeterministicFeedbackProvider()
        else:
            # A fresh real provider per quality case; _run_case applies
            # the per-case bounds. A real-mode failure never falls back.
            case_provider = provider if real_provider else LiteLLMProvider(resolved_model)
        report["runs"].append(await _run_case(case, case_provider, work_root))
    report["_meta"]["collection"] = {
        "cases_attempted": len(report["runs"]),
        "cases_recorded": len([run for run in report["runs"] if run["collection_error"] is None]),
        "cases_with_collection_error": len([run for run in report["runs"] if run["collection_error"] is not None]),
    }
    Draft202012Validator(TRIAL_REPORT_SCHEMA).validate(report)
    return report


def build_definition(
    repetitions: int,
    *,
    cases: tuple[str, ...] = fspec.FEEDBACK_CASES,
    source_mode: str = "deterministic_injection",
    model: str | None = None,
) -> dict[str, Any]:
    return fspec.build_feedback_definition(repetitions=repetitions, cases=cases, source_mode=source_mode, model=model)


def _planned_source(definition: dict[str, Any], case: str) -> str:
    """The quality cases follow the definition's mode; the unknown-write
    case is always the deterministic fault injection."""

    if case not in fspec.QUALITY_CASES:
        return "deterministic_injection"
    return "real_model" if definition["source_mode"] == "real_model" else "deterministic_injection"


def _validate_definition(definition: dict[str, Any]) -> None:
    if definition["source_mode"] == "real_model" and not (definition["model"] and str(definition["model"]).strip()):
        raise ValueError("real_model feedback definitions must pin a concrete model")
    if definition["source_mode"] == "deterministic_injection" and definition["model"]:
        raise ValueError("deterministic_injection feedback definitions must not pin a provider model")


def _register() -> None:
    """Register the platform_feedback scenario adapter (idempotent import)."""

    try:
        experiment.register_scenario(
            experiment.ScenarioAdapter(
                name=fspec.SCENARIO_ID,
                collect=collect,
                scenario_digest=fspec.scenario_digest,
                hosts=tuple(fspec.HOSTS),
                cases=tuple(fspec.FEEDBACK_CASES),
                definition_schema=fspec.FEEDBACK_DEFINITION_SCHEMA,
                report_schema=experiment.experiment_report_schema_for(fspec.FEEDBACK_DEFINITION_SCHEMA),
                planned_source=_planned_source,
                validate_definition=_validate_definition,
                default_provider=fspec.DeterministicFeedbackProvider,
                unverified=(
                    "provider price and final billing",
                    "real-model behavior when no real trials are recorded below",
                    "real human wait time (the injected responder answers immediately; no human waited)",
                    "real business samples (the synthetic inventory fixture is not production work)",
                    "business value",
                ),
                extra_run_fields=("feedback", "fault_source", "run_count"),
                security_rules=fspec.SECURITY_RULES,
            )
        )
    except ValueError:
        if experiment.resolve_scenario(fspec.SCENARIO_ID) is not None:  # pragma: no cover - reimport guard
            return
        raise


_register()


def main(argv: list[str] | None = None) -> Path | None:
    """CLI entry: run the versioned repeated feedback experiment."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=None, help="paired repetitions (default: 3)")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--definition",
        default=None,
        help="path to an experiment definition JSON (default: the built-in deterministic definition)",
    )
    selection.add_argument(
        "--model",
        default=None,
        help="select real-model collection with this provider model (LiteLLM name); the run stays gated",
    )
    parser.add_argument(
        "--print-default-definition",
        action="store_true",
        help="print the default experiment definition JSON and exit",
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help="directory for the trials' transient host work dirs (default: a fresh temp directory)",
    )
    parser.add_argument(
        "--trial-report-dir",
        default=None,
        help="directory for the committed trial records (default: tests/scenarios/baselines)",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="directory for the committed aggregate record (default: tests/scenarios/baselines)",
    )
    parser.add_argument(
        "--pacing-seconds",
        type=float,
        default=25.0,
        help="pause between real cases (free-tier rate limits; never applied to hermetic trials)",
    )
    args = parser.parse_args(argv)
    if args.definition is not None and args.repetitions is not None:
        parser.error("--repetitions cannot override a pinned --definition")
    repetitions = args.repetitions if args.repetitions is not None else 3
    if args.print_default_definition:
        print(json.dumps(build_definition(repetitions), indent=2, ensure_ascii=False))
        return None

    if args.definition is not None:
        definition = experiment.load_definition(Path(args.definition), scenario=fspec.SCENARIO_ID)
    elif args.model is not None:
        definition = build_definition(repetitions, source_mode="real_model", model=args.model)
    else:
        definition = build_definition(repetitions)
    state_root = Path(args.state_root) if args.state_root else Path(tempfile.mkdtemp(prefix="controller-feedback-exp-"))
    report = asyncio.run(
        experiment.run_experiment(
            definition=definition,
            state_root=state_root,
            trial_report_dir=Path(args.trial_report_dir) if args.trial_report_dir else BASELINE_DIR,
            case_pacing_seconds=args.pacing_seconds,
        )
    )
    out = experiment.write_experiment_report(report, Path(args.report_dir) if args.report_dir else BASELINE_DIR)
    counts = report["aggregate"]["denominators"]
    print(
        f"{report['_meta']['status']}: trials recorded/skipped/failed = "
        f"{counts['trials_recorded']}/{counts['trials_skipped']}/{counts['trials_failed']}"
    )
    print(f"real-model quality conclusion: {report['_meta']['conclusion']['real_model_quality']}")
    print(f"feedback experiment aggregate written to {out}")
    return out


if __name__ == "__main__":
    main()
