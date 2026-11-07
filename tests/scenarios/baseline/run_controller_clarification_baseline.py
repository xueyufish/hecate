"""Recorded platform clarification baseline: paired clarify-vs-direct cases.

Run manually with
``python -m tests.scenarios.baseline.run_controller_clarification_baseline``.
Both paired cases run the same dataset, tool contract, prompt pair and
output schema on the real platform dispatch; they differ only in whether
the read receipt already carries the required procurement order
reference. This round is deterministic-injection only: a request without
an injected provider produces an explicit skipped record — real-model
runs, real human wait times and costs stay unknown until separately
authorized. The module registers the ``platform_clarification`` scenario
adapter with the experiment layer so versioned repeated trials reuse the
same aggregation, review and comparison-group machinery as the default
dual-host matrix.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
                "source": {"const": "deterministic_injection"},
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
                    "source": {"const": "deterministic_injection"},
                },
            },
        },
    },
}


async def _run_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    started = time.monotonic()
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
        "source": "deterministic_injection",
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
            "source": "deterministic_injection",
            "sampling": dict(cspec.SAMPLING),
            "limits": dict(cspec.LIMITS),
            "thresholds": dict(cspec.THRESHOLDS),
            "cost_threshold_conclusion": "unverified",
            "model": "deterministic-injection",
            "model_version": "unverified",
            "tool_versions": {cspec.READ_TOOL: "synthetic-1", cspec.WRITE_TOOL: "synthetic-1"},
            "dataset_digest": spec.digest(spec.MATERIAL),
            "prompt_digest": spec.digest([cspec.SYSTEM_PROMPT, cspec.USER_PROMPT]),
            "tool_digest": spec.digest(cspec.tool_definitions()),
            "hosts": list(hosts),
            "collection": {
                "cases_attempted": 0,
                "cases_recorded": 0,
                "cases_with_collection_error": 0,
            },
            "unverified": [
                "real-model behavior (deterministic injection measures the harness and the paired loop)",
                "real human wait time (the injected responder answers immediately; no human waited)",
                "provider price and final billing",
                "real business samples (the synthetic inventory fixture is not production work)",
                "business value",
            ],
        },
        "runs": [],
    }
    if provider is None:
        report["_meta"]["status"] = "skipped"
        report["_meta"]["reason"] = (
            "real-model clarification collection is deferred until authorized samples, "
            "human-in-the-loop authorization and a cost budget are provisioned"
        )
        report["_meta"]["collection"] = {"cases_attempted": 0, "cases_recorded": 0, "cases_with_collection_error": 0}
        Draft202012Validator(TRIAL_REPORT_SCHEMA).validate(report)
        return report
    work_root = root or Path(tempfile.mkdtemp(prefix="controller-clarification-"))
    work_root.mkdir(parents=True, exist_ok=True)
    for case in cases:
        report["runs"].append(await _run_case(case, provider, work_root))
    report["_meta"]["collection"] = {
        "cases_attempted": len(report["runs"]),
        "cases_recorded": len([run for run in report["runs"] if run["collection_error"] is None]),
        "cases_with_collection_error": len([run for run in report["runs"] if run["collection_error"] is not None]),
    }
    Draft202012Validator(TRIAL_REPORT_SCHEMA).validate(report)
    return report


def build_definition(repetitions: int, *, cases: tuple[str, ...] = cspec.CLARIFICATION_CASES) -> dict[str, Any]:
    return cspec.build_clarification_definition(repetitions=repetitions, cases=cases)


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
                planned_source=lambda definition, case: "deterministic_injection",
                validate_definition=_validate_definition,
                default_provider=cspec.DeterministicClarificationProvider,
                unverified=(
                    "provider price and final billing",
                    "real-model behavior (deterministic injection measures the harness and the paired loop)",
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


def _validate_definition(definition: dict[str, Any]) -> None:
    if definition["source_mode"] != "deterministic_injection":
        # Honest boundary: real-model clarification needs human-in-the-loop
        # authorization, samples and a cost budget that are not provisioned.
        raise ValueError(
            "real-model clarification experiments are deferred until authorized samples, "
            "human-in-the-loop authorization and a cost budget are provisioned"
        )


_register()


def main(argv: list[str] | None = None) -> Path | None:
    """CLI entry: run the versioned repeated clarification experiment."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3, help="how many times each paired case runs")
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
    args = parser.parse_args(argv)
    if args.print_default_definition:
        print(json.dumps(build_definition(args.repetitions), indent=2, ensure_ascii=False))
        return None

    definition = build_definition(args.repetitions)
    state_root = (
        Path(args.state_root) if args.state_root else Path(tempfile.mkdtemp(prefix="controller-clarification-exp-"))
    )
    report = asyncio.run(
        experiment.run_experiment(
            definition=definition,
            state_root=state_root,
            trial_report_dir=Path(args.trial_report_dir) if args.trial_report_dir else BASELINE_DIR,
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
