"""Recorded platform clarification baseline: paired clarify-vs-direct cases.

Run manually with
``python -m tests.scenarios.baseline.run_controller_clarification_baseline``.
Both paired cases run the same dataset, tool contract, prompt pair and
output schema on the real platform dispatch; they differ only in whether
the read receipt already carries the required procurement order
reference. The default run is the deterministic-injection matrix. Real
model collection reuses the dual-host entry conditions — the explicit
gate (``HECATE_RUN_CONTROLLER_HOST_BASELINE=1``) plus a credential
matching the selected model — and never falls back to the deterministic
provider: a real-mode failure is recorded as such. The human responder
stays the fixed simulated ``provide_input`` answer and is labeled
separately from the model source in every report. The module registers
the ``platform_clarification`` scenario adapter with the experiment
layer so versioned repeated trials reuse the same aggregation, review
and comparison-group machinery as the default dual-host matrix.
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

from tests.scenarios.baseline import controller_host_clarification_platform as driver
from tests.scenarios.baseline import controller_host_clarification_spec as cspec
from tests.scenarios.baseline import controller_host_experiment as experiment
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
                "kind": {"const": "controller_host_clarification"},
                "status": {"enum": ["recorded", "skipped"]},
                "gate": {"const": False},
                "source": {"enum": ["deterministic_injection", "real_model"]},
                "rubric_version": {"const": cspec.RUBRIC_VERSION},
                "scenario": {"const": cspec.SCENARIO_ID},
                "sampling": {"const": cspec.SAMPLING},
                "limits": {"const": cspec.LIMITS},
                "thresholds": {"const": cspec.THRESHOLDS},
            },
        },
        "runs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["host", "case", "source", "summary", "clarification", "usage", "stop"],
                "properties": {
                    "host": {"const": "platform"},
                    "case": {"enum": list(cspec.CLARIFICATION_CASES)},
                    "source": {"enum": ["deterministic_injection", "real_model"]},
                },
            },
        },
    },
}


async def _run_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    # The model provenance label follows the provider actually driving the
    # loop; the human responder stays the fixed simulation either way and
    # is labeled separately in each run's clarification block. The fresh
    # per-case bounds wrap after the provenance check, mirroring the
    # dual-host collector.
    source = "real_model" if isinstance(provider, LiteLLMProvider) else "deterministic_injection"
    provider = _BoundedProvider(provider)
    try:
        observation = await driver.run_clarification_case(case, provider, root)
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = {
            "task_id": None,
            "run_id": None,
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
            "clarification": {
                "parks": [],
                "park_count": 0,
                "input_revisions": [],
                "supplement_visible_in_model_input": None,
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
    summary = cspec.score_clarification_run(case, observation)
    summary["within_latency_threshold"] = (
        observation.get("latency_ms_total") is not None
        and observation["latency_ms_total"] <= cspec.THRESHOLDS["latency_ms"]
    )
    summary["within_token_threshold"] = (
        observation["usage"]["token_total"] <= cspec.THRESHOLDS["tokens"]
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
        "clarification": observation.get("clarification") or {},
    }


async def collect(
    *,
    provider: Any | None = None,
    model: str | None = None,
    hosts: tuple[str, ...] = cspec.HOSTS,
    cases: tuple[str, ...] = cspec.CLARIFICATION_CASES,
    root: Path | None = None,
    case_pacing_seconds: float = 0.0,
) -> dict[str, Any]:
    """Collect the paired clarification matrix and return the validated report."""

    if not hosts or tuple(hosts) != cspec.HOSTS:
        raise ValueError("the clarification baseline runs on the platform host only")
    if not cases or len(set(cases)) != len(cases) or any(case not in cspec.CLARIFICATION_CASES for case in cases):
        raise ValueError("cases must be a nonempty unique subset of the paired clarification cases")
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
        source_identity["collector_files"]["tests/scenarios/baseline/run_controller_clarification_baseline.py"] = (
            spec.digest(Path(__file__).read_text(encoding="utf-8"))
        )
    report: dict[str, Any] = {
        "_meta": {
            "kind": "controller_host_clarification",
            "status": "recorded",
            "gate": False,
            "business_value": "unverified",
            "recorded_at": datetime.now(UTC).isoformat(),
            "rubric_version": cspec.RUBRIC_VERSION,
            "scenario": cspec.SCENARIO_ID,
            "source_revision": source_identity,
            "output_schema_digest": spec.digest(cspec.OUTPUT_SCHEMA),
            "scenario_digest": cspec.scenario_digest(),
            "cases": list(cases),
            "source": "deterministic_injection" if hermetic else "real_model",
            "sampling": dict(cspec.SAMPLING),
            "limits": dict(cspec.LIMITS),
            "thresholds": dict(cspec.THRESHOLDS),
            "cost_threshold_conclusion": "unverified",
            "model": "deterministic-injection" if hermetic else resolved_model,
            "model_version": "unverified",
            "tool_versions": {cspec.READ_TOOL: "synthetic-1", cspec.WRITE_TOOL: "synthetic-1"},
            "dataset_digest": spec.digest(spec.MATERIAL),
            # Three separately labeled sources: the model above, the
            # business data here, and the human responder per run's
            # clarification block.
            "dataset_source": "synthetic_inventory",
            "prompt_digest": spec.digest([cspec.SYSTEM_PROMPT, cspec.USER_PROMPT]),
            "tool_digest": spec.digest(cspec.tool_definitions()),
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
    work_root = root or Path(tempfile.mkdtemp(prefix="controller-clarification-"))
    work_root.mkdir(parents=True, exist_ok=True)
    for case in cases:
        # Free-tier rate limits need real pacing between real cases; the
        # pause is harness robustness, not part of any measurement.
        if not hermetic and case_pacing_seconds > 0:
            await asyncio.sleep(case_pacing_seconds)
        # _run_case applies fresh request/token bounds for each case.
        # A real-mode failure never falls back to
        # the deterministic provider — the failure is the record.
        case_provider = provider if provider is not None else LiteLLMProvider(resolved_model)
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
    cases: tuple[str, ...] = cspec.CLARIFICATION_CASES,
    source_mode: str = "deterministic_injection",
    model: str | None = None,
) -> dict[str, Any]:
    return cspec.build_clarification_definition(
        repetitions=repetitions, cases=cases, source_mode=source_mode, model=model
    )


def _register() -> None:
    """Register the platform_clarification scenario adapter (idempotent import)."""

    try:
        experiment.register_scenario(
            experiment.ScenarioAdapter(
                name=cspec.SCENARIO_ID,
                collect=collect,
                scenario_digest=cspec.scenario_digest,
                hosts=tuple(cspec.HOSTS),
                cases=tuple(cspec.CLARIFICATION_CASES),
                definition_schema=cspec.CLARIFICATION_DEFINITION_SCHEMA,
                report_schema=experiment.experiment_report_schema_for(cspec.CLARIFICATION_DEFINITION_SCHEMA),
                planned_source=_planned_source,
                validate_definition=_validate_definition,
                default_provider=cspec.DeterministicClarificationProvider,
                unverified=(
                    "provider price and final billing",
                    "real-model behavior when no real trials are recorded below",
                    "real human wait time (the injected responder answers immediately; no human waited)",
                    "real business samples (the synthetic inventory fixture is not production work)",
                    "business value",
                ),
                extra_run_fields=("clarification", "run_count"),
                security_rules=cspec.SECURITY_RULES,
            )
        )
    except ValueError:
        if experiment.resolve_scenario(cspec.SCENARIO_ID) is not None:  # pragma: no cover - reimport guard
            return
        raise


def _planned_source(definition: dict[str, Any], case: str) -> str:
    """Both paired cases are quality cases: in real mode the model drives
    them all; the deterministic mode measures the harness."""

    return "real_model" if definition["source_mode"] == "real_model" else "deterministic_injection"


def _validate_definition(definition: dict[str, Any]) -> None:
    if definition["source_mode"] == "real_model" and not (definition["model"] and str(definition["model"]).strip()):
        raise ValueError("real_model clarification definitions must pin a concrete model")
    if definition["source_mode"] == "deterministic_injection" and definition["model"]:
        raise ValueError("deterministic_injection clarification definitions must not pin a provider model")


_register()


def main(argv: list[str] | None = None) -> Path | None:
    """CLI entry: run the versioned repeated clarification experiment.

    The default run is the offline deterministic-injection matrix. Real
    model collection is chosen explicitly with ``--model`` (or a definition
    file pinning ``real_model``) and still requires the documented gate and
    a matching provider credential at run time; without them every trial
    is recorded as an explicit skip.
    """

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
        definition = experiment.load_definition(Path(args.definition), scenario=cspec.SCENARIO_ID)
    elif args.model is not None:
        definition = build_definition(repetitions, source_mode="real_model", model=args.model)
    else:
        definition = build_definition(repetitions)
    state_root = (
        Path(args.state_root) if args.state_root else Path(tempfile.mkdtemp(prefix="controller-clarification-exp-"))
    )
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
    print(f"clarification experiment aggregate written to {out}")
    return out


if __name__ == "__main__":
    main()
