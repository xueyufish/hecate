"""Recorded platform trusted-field baseline: host-enforced field provenance.

Run manually with
``python -m tests.scenarios.baseline.run_controller_trusted_field_baseline``.
Every case runs the synthetic inventory surface on the real platform
dispatch under the host-declared field provenance profile pinned in the
scenario digest. The registry has no provenance logic, so each block in a
record is the production guard's verdict read back from the durable action
ledger. Collection is offline and deterministic: one pre-registered script
per case drives the model seam (compliant, guess, lucky guess,
substitution) — harness evidence for the protection, never real-model
quality. Real-model collection for this profile is out of scope until a
gated provider path is added; the definition schema admits only the
deterministic source. The module registers the ``platform_trusted_field``
scenario adapter, so repeated trials reuse the experiment layer's
aggregation and comparison groups without ever sharing a group with the
historical clarification baseline.
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

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import controller_host_trusted_field_platform as driver
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec
from tests.scenarios.baseline import run_controller_host_baseline as collector
from tests.scenarios.baseline.controller_host_platform import collection_error
from tests.scenarios.baseline.run_controller_host_baseline import LiteLLMProvider, _BoundedProvider

BASELINE_DIR = collector.BASELINE_DIR
UNVERIFIED = (
    "real-model behavior (only scripted stand-ins drive these records)",
    "real human wait time (the injected responder answers immediately; no human waited)",
    "provider price and final billing",
    "real business samples (the synthetic inventory fixture is not production work)",
    "execution paths other than the platform-local Controller dispatch",
    "business value",
)

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
                "profile",
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
                "kind": {"const": "controller_host_trusted_field"},
                "status": {"const": "recorded"},
                "gate": {"const": False},
                "source": {"const": "deterministic_injection"},
                "rubric_version": {"const": tspec.RUBRIC_VERSION},
                "scenario": {"const": tspec.SCENARIO_ID},
                "profile": {"const": tspec.PROFILE.pin()},
                "sampling": {"const": tspec.SAMPLING},
                "limits": {"const": tspec.LIMITS},
                "thresholds": {"const": tspec.THRESHOLDS},
            },
        },
        "runs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["host", "case", "source", "summary", "security", "guard_verdicts", "usage", "stop"],
                "properties": {
                    "host": {"const": "platform"},
                    "case": {"enum": list(tspec.CASES)},
                    "source": {"const": "deterministic_injection"},
                },
            },
        },
    },
}


def _bind(provider: Any, case: str) -> Any:
    return provider.for_case(case) if hasattr(provider, "for_case") else provider


async def _run_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    bounded = _BoundedProvider(_bind(provider, case))
    try:
        observation = await driver.run_trusted_field_case(case, bounded, root)
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = {
            "task_id": None,
            "run_id": None,
            "rounds": [],
            "tool_receipts": [],
            "tool_calls": [],
            "ledger_actions": [],
            "guard_verdicts": [],
            "final_content": None,
            "model_calls": None,
            "write_attempts": None,
            "side_effect_count": None,
            "latency_ms_total": (time.monotonic() - started) * 1000,
            "collection_error": collection_error(exc),
            "clarification": {
                "parks": [],
                "park_count": 0,
                "input_revisions": [],
                "responder": "deterministic_injection",
            },
            "diagnostics": None,
            "usage": {
                "source": "unverified",
                "token_total": None,
                "cost_amount": None,
                "currency": "USD",
                "price_version": "unverified",
            },
        }
    summary = tspec.score_trusted_field_run(case, observation)
    summary["within_latency_threshold"] = (
        observation.get("latency_ms_total") is not None
        and observation["latency_ms_total"] <= tspec.THRESHOLDS["latency_ms"]
    )
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
        "ledger_actions": observation.get("ledger_actions") or [],
        "approval": None,
        "rebuild": observation.get("rebuild") or {"performed": False},
        "collection_error": observation.get("collection_error"),
        "stop": {
            "task_status": observation.get("task_status"),
            "error_category": observation.get("error_category"),
            "error": observation.get("error"),
            "reconciliation_reason": observation.get("reconciliation_reason"),
        },
        # Security and quality stay separate fields: no quality signal is
        # ever averaged with a security verdict.
        "security": dict(summary["negatives"]),
        "summary": summary,
        "guard_verdicts": observation.get("guard_verdicts") or [],
        "profile": observation.get("profile"),
        "latency_ms_total": observation.get("latency_ms_total"),
        "model_calls": observation.get("model_calls"),
        "write_attempts": observation.get("write_attempts"),
        "side_effect_count": observation.get("side_effect_count"),
        "usage": observation.get("usage"),
        "clarification": observation.get("clarification") or {},
        "diagnostics": observation.get("diagnostics"),
    }


async def collect(
    *,
    provider: Any | None = None,
    model: str | None = None,
    hosts: tuple[str, ...] = tspec.HOSTS,
    cases: tuple[str, ...] = tspec.CASES,
    root: Path | None = None,
    case_pacing_seconds: float = 0.0,
) -> dict[str, Any]:
    """Collect the trusted-field matrix and return the validated report."""

    if not hosts or tuple(hosts) != tspec.HOSTS:
        raise ValueError("the trusted-field baseline runs on the platform host only")
    if not cases or len(set(cases)) != len(cases) or any(case not in tspec.CASES for case in cases):
        raise ValueError("cases must be a nonempty unique subset of the trusted-field cases")
    if model is not None or isinstance(provider, LiteLLMProvider):
        raise ValueError("real-model collection is out of scope for the trusted-field profile")
    provider = provider if provider is not None else tspec.ScriptedTrustedFieldProvider()
    source_identity = collector._source_identity()
    if "collector_files" in source_identity:
        source_identity["collector_files"]["tests/scenarios/baseline/run_controller_trusted_field_baseline.py"] = (
            spec.digest(Path(__file__).read_text(encoding="utf-8"))
        )
    report: dict[str, Any] = {
        "_meta": {
            "kind": "controller_host_trusted_field",
            "status": "recorded",
            "gate": False,
            "business_value": "unverified",
            "recorded_at": datetime.now(UTC).isoformat(),
            "rubric_version": tspec.RUBRIC_VERSION,
            "scenario": tspec.SCENARIO_ID,
            "profile": tspec.PROFILE.pin(),
            "source_revision": source_identity,
            "scenario_digest": tspec.scenario_digest(),
            "cases": list(cases),
            "source": "deterministic_injection",
            "model": "deterministic-injection",
            "model_scripts": {case: tspec.CASE_SCRIPTS[case][1] for case in cases},
            "sampling": dict(tspec.SAMPLING),
            "limits": dict(tspec.LIMITS),
            "thresholds": dict(tspec.THRESHOLDS),
            "dataset_source": "synthetic_inventory",
            "hosts": list(hosts),
            "collection": {"cases_attempted": 0, "cases_recorded": 0, "cases_with_collection_error": 0},
            "unverified": list(UNVERIFIED),
        },
        "runs": [],
    }
    work_root = root or Path(tempfile.mkdtemp(prefix="controller-trusted-field-"))
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


def build_definition(repetitions: int, *, cases: tuple[str, ...] = tspec.CASES) -> dict[str, Any]:
    return tspec.build_trusted_field_definition(repetitions=repetitions, cases=cases)


def _planned_source(definition: dict[str, Any], case: str) -> str:
    return "deterministic_injection"


def _validate_definition(definition: dict[str, Any]) -> None:
    expected = [case for case in tspec.ZERO_WRITE_CASES if case in definition["cases"]]
    if definition["criteria"]["security"]["zero_write_cases"] != expected:
        raise ValueError("zero_write_cases must name exactly the selected refusal cases")


def _register() -> None:
    """Register the platform_trusted_field scenario adapter (idempotent import)."""

    try:
        experiment.register_scenario(
            experiment.ScenarioAdapter(
                name=tspec.SCENARIO_ID,
                collect=collect,
                scenario_digest=tspec.scenario_digest,
                hosts=tuple(tspec.HOSTS),
                cases=tuple(tspec.CASES),
                definition_schema=tspec.TRUSTED_FIELD_DEFINITION_SCHEMA,
                report_schema=experiment.experiment_report_schema_for(tspec.TRUSTED_FIELD_DEFINITION_SCHEMA),
                planned_source=_planned_source,
                validate_definition=_validate_definition,
                default_provider=tspec.ScriptedTrustedFieldProvider,
                unverified=UNVERIFIED,
                extra_run_fields=("security", "guard_verdicts", "profile", "clarification", "run_count"),
                security_rules=tspec.SECURITY_RULES,
            )
        )
    except ValueError:
        if experiment.resolve_scenario(tspec.SCENARIO_ID) is not None:  # pragma: no cover - reimport guard
            return
        raise


_register()


def main(argv: list[str] | None = None) -> Path | None:
    """CLI entry: run the versioned repeated trusted-field experiment offline."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=None, help="repetitions per case (default: 3)")
    parser.add_argument(
        "--definition",
        default=None,
        help="path to an experiment definition JSON (default: the built-in deterministic definition)",
    )
    parser.add_argument(
        "--print-default-definition", action="store_true", help="print the default definition JSON and exit"
    )
    parser.add_argument("--state-root", default=None, help="directory for the trials' transient host work dirs")
    parser.add_argument("--trial-report-dir", default=None, help="directory for the trial records")
    parser.add_argument("--report-dir", default=None, help="directory for the aggregate record")
    args = parser.parse_args(argv)
    if args.definition is not None and args.repetitions is not None:
        parser.error("--repetitions cannot override a pinned --definition")
    repetitions = args.repetitions if args.repetitions is not None else 3
    if args.print_default_definition:
        print(json.dumps(build_definition(repetitions), indent=2, ensure_ascii=False))
        return None
    definition = (
        experiment.load_definition(Path(args.definition), scenario=tspec.SCENARIO_ID)
        if args.definition is not None
        else build_definition(repetitions)
    )
    state_root = (
        Path(args.state_root) if args.state_root else Path(tempfile.mkdtemp(prefix="controller-trusted-field-exp-"))
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
    print(f"trusted-field experiment aggregate written to {out}")
    return out


if __name__ == "__main__":
    main()
