"""Repeated-trial experiments over the Controller two-host baseline.

One versioned experiment definition pins the scenario identity, host matrix,
model configuration, repetition count, resource caps, scoring version and the
pre-declared acceptance criteria behind a content digest. Every repetition
("trial") runs the single-shot collector with its own identity and a fresh
state directory, so no Task/Run identity, model-call counter or business
side-effect state is ever reused across trials. The aggregate report keeps
denominators, completion and pass rates, failure classification and latency
distributions per host and case, groups real-model and deterministic-injection
evidence separately, reports security assertions independently of quality
averages, and keeps missing cost or review measurements unknown instead of
zero-filled. Manual review records are separate immutable files keyed by the
aggregate report digest. Records whose definition digest differs never enter
the same comparison group.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import run_controller_host_baseline as collector

BASELINE_DIR = collector.BASELINE_DIR
EXPERIMENT_VERSION = "controller-host-experiment-2"
REVIEW_VERSION = "controller-host-experiment-review-1"
DEFAULT_EXPERIMENT_ID = "controller-host-repeat-baseline"
# Pre-declared per-case pass criteria: the summary key each recorded run is
# judged against. Declared once here; the definition copies it so the digest
# pins exactly what was promised before collection.
CASE_CRITERIA: dict[str, str] = {
    "normal": "delivery_compliance",
    "approval_resume": "approval_resume_compliance",
    "cancelled": "cancel_compliance",
    "budget_exhausted": "budget_compliance",
    "unknown_outcome": "unknown_stop_compliance",
}
# Security assertions are judged from the recorded counts, never averaged.
MAX_SIDE_EFFECTS_PER_CASE = 1
ZERO_WRITE_CASES = ("cancelled", "budget_exhausted", "unknown_outcome")
SOURCES = ("real_model", "deterministic_injection")
DEFAULT_SCENARIO = "controller_host_matrix"


@dataclass(frozen=True)
class ScenarioAdapter:
    """One scenario family's pinned identity, schemas and collector.

    Each adapter owns its definition schema, its single-shot collector and
    its planned evidence source, so a scenario's definition identity never
    blends with another's. The default dual-host matrix keeps its original
    schemas and digest space byte-identical; additional scenarios register
    their own adapters at import time.
    """

    name: str
    collect: Any
    scenario_digest: Any
    hosts: tuple[str, ...]
    cases: tuple[str, ...]
    definition_schema: dict[str, Any]
    report_schema: dict[str, Any]
    planned_source: Any
    validate_definition: Any
    default_provider: Any
    # The scenario's own honest unknown-evidence list for aggregate _meta.
    unverified: tuple[str, ...] = (
        "provider price and final billing",
        "real-model quality (unless real trials are recorded below)",
        "cross-process and PostgreSQL recovery (host rebuilds are in-process over sqlite)",
        "managed deployment certification",
        "business value",
    )
    extra_run_fields: tuple[str, ...] = ()
    security_rules: dict[str, tuple[str, ...]] | None = None


_SCENARIO_ADAPTERS: dict[str, ScenarioAdapter] = {}


def register_scenario(adapter: ScenarioAdapter) -> ScenarioAdapter:
    """Register a scenario adapter; re-registration of a name is refused."""

    if adapter.name in _SCENARIO_ADAPTERS:
        raise ValueError(f"scenario {adapter.name!r} is already registered")
    _SCENARIO_ADAPTERS[adapter.name] = adapter
    return adapter


def resolve_scenario(name: str | None) -> ScenarioAdapter:
    """Resolve a scenario adapter; unknown names name the registered set."""

    resolved = name or DEFAULT_SCENARIO
    if resolved == "platform_clarification" and resolved not in _SCENARIO_ADAPTERS:
        from tests.scenarios.baseline import run_controller_clarification_baseline  # noqa: F401

    if resolved == "platform_feedback" and resolved not in _SCENARIO_ADAPTERS:
        from tests.scenarios.baseline import run_controller_feedback_baseline  # noqa: F401

    if resolved not in _SCENARIO_ADAPTERS:
        raise ValueError(f"unknown scenario {resolved!r}; registered: {sorted(_SCENARIO_ADAPTERS)}")
    return _SCENARIO_ADAPTERS[resolved]


async def _collect_dual_host(**kwargs: Any) -> dict[str, Any]:
    return await collector.collect(**kwargs)


def _dual_host_planned_source(definition: dict[str, Any], case: str) -> str:
    return (
        "real_model"
        if definition["source_mode"] == "real_model" and case in spec.QUALITY_CASES
        else "deterministic_injection"
    )


def _dual_host_validate_definition(definition: dict[str, Any]) -> None:
    if definition["source_mode"] == "real_model" and not (definition["model"] and str(definition["model"]).strip()):
        raise ValueError("real_model experiments must pin a concrete model in the definition")
    if definition["source_mode"] == "deterministic_injection" and definition["model"]:
        raise ValueError("deterministic_injection experiments must not pin a provider model")
    if set(definition["hosts"]) - set(spec.HOSTS):
        raise ValueError("hosts must be a nonempty unique subset of platform,runner")
    if set(definition["cases"]) - set(spec.CASES):
        raise ValueError("cases must be a nonempty unique subset of the registered cases")


DEFINITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "id",
        "definition_version",
        "source_mode",
        "model",
        "hosts",
        "cases",
        "repetitions",
        "sampling",
        "limits",
        "thresholds",
        "scenario_digest",
        "criteria",
    ],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {"properties": {"source_mode": {"const": "real_model"}}},
            "then": {"properties": {"model": {"type": "string", "pattern": "\\S"}}},
            "else": {"properties": {"model": {"const": None}}},
        }
    ],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
        "definition_version": {"const": EXPERIMENT_VERSION},
        "source_mode": {"enum": ["deterministic_injection", "real_model"]},
        "model": {"type": ["string", "null"]},
        "hosts": {"type": "array", "items": {"enum": list(spec.HOSTS)}, "minItems": 1, "uniqueItems": True},
        "cases": {"type": "array", "items": {"enum": list(spec.CASES)}, "minItems": 1, "uniqueItems": True},
        "repetitions": {"type": "integer", "minimum": 1, "maximum": 20},
        "sampling": {"const": dict(spec.SAMPLING)},
        "limits": {"const": {"requests_per_case": spec.REQUEST_LIMIT, "seconds_per_case": 120}},
        "thresholds": {
            "const": {
                "latency_ms": spec.LATENCY_BUDGET_MS,
                "tokens": spec.TOKEN_BUDGET,
                "cost_usd": spec.COST_BUDGET_USD,
            }
        },
        "scenario_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "criteria": {
            "type": "object",
            "required": ["case_criteria", "security"],
            "additionalProperties": False,
            "properties": {
                "case_criteria": {
                    "type": "object",
                    "minProperties": 1,
                    "propertyNames": {"enum": list(spec.CASES)},
                    "additionalProperties": {
                        "enum": [
                            "delivery_compliance",
                            "approval_resume_compliance",
                            "cancel_compliance",
                            "budget_compliance",
                            "unknown_stop_compliance",
                        ]
                    },
                },
                "security": {
                    "type": "object",
                    "required": ["max_side_effects_per_case", "zero_write_cases"],
                    "additionalProperties": False,
                    "properties": {
                        "max_side_effects_per_case": {"const": MAX_SIDE_EFFECTS_PER_CASE},
                        "zero_write_cases": {"type": "array", "items": {"enum": list(spec.CASES)}, "uniqueItems": True},
                    },
                },
            },
        },
    },
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reviewer", "verdict"],
    "additionalProperties": False,
    "properties": {
        "reviewer": {"type": "string", "minLength": 1},
        "verdict": {"enum": ["agree", "disagree", "partial"]},
        "trials_reviewed": {"type": ["integer", "null"], "minimum": 1},
        "cases_reviewed": {"type": ["integer", "null"], "minimum": 1},
        # Review effort is self-reported; an unreported duration stays null.
        "duration_ms": {"type": ["integer", "null"], "minimum": 0},
        "notes": {"type": ["string", "null"]},
        "disagreements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trial_id", "reason"],
                "properties": {
                    "trial_id": {"type": "string", "minLength": 1},
                    "host": {"enum": [None, *spec.HOSTS]},
                    "case": {"enum": [None, *spec.CASES]},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}

REVIEW_RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "review_version", "target", "review", "recorded_at"],
    "additionalProperties": False,
    "properties": {
        "kind": {"const": "controller_host_experiment_review"},
        "review_version": {"const": REVIEW_VERSION},
        "target": {
            "type": "object",
            "required": ["experiment_run_id", "report_digest"],
            "additionalProperties": False,
            "properties": {
                "experiment_run_id": {"type": "string", "minLength": 1},
                "report_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        },
        "review": REVIEW_SCHEMA,
        "recorded_at": {"type": "string"},
    },
}

EXPERIMENT_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["_meta", "trials", "aggregate"],
    "properties": {
        "_meta": {
            "type": "object",
            "required": [
                "kind",
                "experiment_version",
                "status",
                "gate",
                "business_value",
                "experiment_run_id",
                "recorded_at",
                "definition",
                "definition_digest",
                "model",
                "source_revision",
                "conclusion",
                "review",
                "unverified",
            ],
            "properties": {
                "kind": {"const": "controller_host_experiment"},
                "experiment_version": {"const": EXPERIMENT_VERSION},
                "status": {"enum": ["recorded", "skipped", "failed"]},
                "gate": {"const": False},
                "business_value": {"const": "unverified"},
                "definition": DEFINITION_SCHEMA,
                "definition_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "review": {
                    "type": "object",
                    "required": ["status", "note"],
                    "properties": {
                        "status": {"const": "not_performed"},
                        "note": {"type": "string"},
                    },
                },
            },
        },
        "trials": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trial_id", "trial_index", "status", "state_dir", "runs"],
                "properties": {
                    "trial_id": {"type": "string", "minLength": 1},
                    "trial_index": {"type": "integer", "minimum": 0},
                    "status": {"enum": ["recorded", "skipped", "failed"]},
                    "runs": {"type": "array"},
                },
            },
        },
        "aggregate": {
            "type": "object",
            "required": ["denominators", "cases", "failure_ledger", "security", "by_source", "usage", "cost"],
            "properties": {
                "denominators": {
                    "type": "object",
                    "required": [
                        "repetitions_requested",
                        "trials_recorded",
                        "trials_skipped",
                        "trials_failed",
                    ],
                },
                "cost": {
                    "type": "object",
                    "required": ["status", "reason"],
                    "properties": {"status": {"const": "unknown"}},
                },
                "security": {
                    "type": "object",
                    "required": [
                        "checks_expected",
                        "checks_missing",
                        "checks_evaluable",
                        "checks_passed",
                        "checks_failed",
                        "all_passed",
                        "failures",
                    ],
                },
                "by_source": {
                    "type": "object",
                    "required": ["real_model", "deterministic_injection"],
                    "properties": {
                        "real_model": {"type": "object", "required": ["cases_recorded", "note"]},
                        "deterministic_injection": {"type": "object", "required": ["cases_recorded", "note"]},
                    },
                },
            },
        },
    },
}


def text_digest(text: str) -> str:
    """Digest the exact bytes of a written record file."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scenario_digest() -> str:
    """Digest the shared scenario inputs both hosts consume."""

    return spec.digest(
        {
            "material": spec.MATERIAL,
            "tool_schemas": spec.TOOL_SCHEMAS,
            "prompts": [spec.SYSTEM_PROMPT, spec.USER_PROMPT],
            "output_schema": spec.OUTPUT_SCHEMA,
            "expected_stops": spec.EXPECTED_STOPS,
            "receipt_id": spec.RECEIPT_ID,
            "expected_read": spec.EXPECTED_READ,
            "expected_write": spec.EXPECTED_WRITE,
            "cases": list(spec.CASES),
            "hosts": list(spec.HOSTS),
            "rubric_version": spec.RUBRIC_VERSION,
        }
    )


def build_definition(
    *,
    repetitions: int,
    hosts: tuple[str, ...] = spec.HOSTS,
    cases: tuple[str, ...] = spec.CASES,
    source_mode: str = "deterministic_injection",
    model: str | None = None,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
) -> dict[str, Any]:
    """Assemble and validate a versioned experiment definition."""

    if source_mode == "real_model" and not (model and model.strip()):
        raise ValueError("real_model experiments must pin a concrete model in the definition")
    if source_mode == "deterministic_injection" and model:
        raise ValueError("deterministic_injection experiments must not pin a provider model")
    definition: dict[str, Any] = {
        "id": experiment_id,
        "definition_version": EXPERIMENT_VERSION,
        "source_mode": source_mode,
        "model": model,
        "hosts": list(hosts),
        "cases": list(cases),
        "repetitions": repetitions,
        "sampling": dict(spec.SAMPLING),
        "limits": {"requests_per_case": spec.REQUEST_LIMIT, "seconds_per_case": 120},
        "thresholds": {
            "latency_ms": spec.LATENCY_BUDGET_MS,
            "tokens": spec.TOKEN_BUDGET,
            "cost_usd": spec.COST_BUDGET_USD,
        },
        "scenario_digest": scenario_digest(),
        "criteria": {
            "case_criteria": {case: CASE_CRITERIA[case] for case in cases},
            "security": {
                "max_side_effects_per_case": MAX_SIDE_EFFECTS_PER_CASE,
                "zero_write_cases": [case for case in ZERO_WRITE_CASES if case in cases],
            },
        },
    }
    Draft202012Validator(DEFINITION_SCHEMA).validate(definition)
    return definition


def load_definition(path: Path, *, scenario: str | None = None) -> dict[str, Any]:
    """Load and validate a definition file against its own scenario."""

    definition = json.loads(path.read_text(encoding="utf-8"))
    resolved = definition.get("scenario", DEFAULT_SCENARIO)
    if scenario is not None and resolved != scenario:
        raise ValueError(f"definition scenario {resolved!r} does not match the requested {scenario!r}")
    _validate_collection_definition(definition, resolve_scenario(resolved))
    return definition


def _validate_collection_definition(definition: dict[str, Any], adapter: ScenarioAdapter | None = None) -> None:
    resolved = resolve_scenario(definition.get("scenario") if adapter is None else adapter.name)
    if adapter is not None and definition.get("scenario", DEFAULT_SCENARIO) != adapter.name:
        raise ValueError(
            f"definition scenario {definition.get('scenario', DEFAULT_SCENARIO)!r} != adapter {adapter.name!r}"
        )
    Draft202012Validator(resolved.definition_schema).validate(definition)
    if definition["scenario_digest"] != resolved.scenario_digest():
        raise ValueError("scenario digest differs from the current inputs or scoring version; rebuild the definition")
    if set(definition["criteria"]["case_criteria"]) != set(definition["cases"]):
        raise ValueError("case criteria must cover exactly the selected cases")
    resolved.validate_definition(definition)


def dump_definition(definition: dict[str, Any], path: Path) -> Path:
    """Write a definition file; the file's digest is the definition digest."""

    definition_digest(definition)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(definition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def definition_digest(definition: dict[str, Any], *, scenario: str | None = None) -> str:
    """The comparison-group identity: any criteria or input change re-keys it."""

    resolved = resolve_scenario(scenario if scenario is not None else definition.get("scenario"))
    Draft202012Validator(resolved.definition_schema).validate(definition)
    return spec.digest(definition)


def _is_reported_usage(run: dict[str, Any]) -> bool:
    """A run's token total counts only when the provider reported every round."""

    usage = run.get("usage")
    return (
        isinstance(usage, dict)
        and usage.get("source") == "reported"
        and type(usage.get("token_total")) is int
        and usage["token_total"] >= 0
    )


def _distribution(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"samples": 0, "min_ms": None, "p50_ms": None, "p90_ms": None, "max_ms": None}
    ordered = sorted(samples)

    def percentile(fraction: float) -> float:
        rank = math.ceil(fraction * len(ordered))
        return ordered[min(len(ordered) - 1, max(0, rank - 1))]

    return {
        "samples": len(ordered),
        "min_ms": ordered[0],
        "p50_ms": percentile(0.50),
        "p90_ms": percentile(0.90),
        "max_ms": ordered[-1],
    }


def _security_checks(
    run: dict[str, Any], zero_write_cases: tuple[str, ...], extra_rules: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Evaluate the pre-declared security rules on one recorded run.

    Runs missing the counted fields (collection errors) are not evaluable
    and are excluded here; they stay visible in the failure ledger instead
    of being folded into either verdict.
    """

    checks: list[dict[str, Any]] = []
    side_effects = run.get("side_effect_count")
    if isinstance(side_effects, int):
        checks.append(
            {
                "rule": "max_side_effects_per_case",
                "passed": side_effects <= MAX_SIDE_EFFECTS_PER_CASE,
                "observed": side_effects,
            }
        )
    if run.get("case") in zero_write_cases:
        write_attempts = run.get("write_attempts")
        if isinstance(side_effects, int) and isinstance(write_attempts, int):
            checks.append(
                {
                    "rule": "zero_write_cases",
                    "passed": side_effects == 0 and write_attempts == 0,
                    "observed": {"side_effects": side_effects, "write_attempts": write_attempts},
                }
            )
    negatives = run.get("summary", {}).get("negatives", {})
    for rule in extra_rules:
        observed = negatives.get(rule)
        if isinstance(observed, bool):
            checks.append({"rule": rule, "passed": not observed, "observed": observed})
    if run.get("collection_error") is not None and extra_rules:
        return [check for check in checks if not check["passed"]]
    return checks


def _case_blocks(
    definition: dict[str, Any],
    trial_records: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    *,
    adapter: ScenarioAdapter | None = None,
) -> list[dict[str, Any]]:
    resolved = adapter if adapter is not None else resolve_scenario(definition.get("scenario"))
    skipped_trials = len([trial for trial in trial_records if trial["status"] == "skipped"])
    failed_trials = len([trial for trial in trial_records if trial["status"] == "failed"])
    blocks: list[dict[str, Any]] = []
    for host in definition["hosts"]:
        for case in definition["cases"]:
            runs = [
                run
                for trial in trial_records
                for run in (trial.get("runs") or [])
                if run.get("host") == host and run.get("case") == case
            ]
            recorded = [run for run in runs if run.get("collection_error") is None]
            collection_errors = [run for run in runs if run.get("collection_error") is not None]
            criterion_key = definition["criteria"]["case_criteria"][case]

            by_source: dict[str, Any] = {}
            planned_source = resolved.planned_source(definition, case)
            for source in SOURCES:
                evaluated = [run for run in recorded if run.get("source") == source]
                passed = [run for run in evaluated if run.get("summary", {}).get(criterion_key) is True]
                # Infrastructure loss must not improve the quality rate by
                # removing scheduled samples from its denominator.
                expected = (
                    definition["repetitions"]
                    if source == planned_source
                    else len([run for run in runs if run.get("source") == source])
                )
                by_source[source] = {
                    "evaluated": len(evaluated),
                    "passed": len(passed),
                    "rate": (len(passed) / expected) if evaluated and expected else None,
                }

            security_failures: list[dict[str, Any]] = []
            zero_write_cases = tuple(definition["criteria"]["security"]["zero_write_cases"])
            extra_rules = (resolved.security_rules or {}).get(case, ())
            for run in runs if extra_rules else recorded:
                for check in _security_checks(run, zero_write_cases, extra_rules):
                    if not check["passed"]:
                        failure = {
                            "host": host,
                            "case": case,
                            "rule": check["rule"],
                            "observed": check["observed"],
                        }
                        security_failures.append(failure)
                        ledger.append(
                            {
                                "trial_id": _ledger_trial_id(trial_records, runs, run),
                                "host": host,
                                "case": case,
                                "kind": "security_check_failed",
                                "detail": f"{check['rule']} observed {check['observed']}",
                            }
                        )

            criterion_failures = [run for run in recorded if run.get("summary", {}).get(criterion_key) is not True]
            for run in criterion_failures:
                ledger.append(
                    {
                        "trial_id": _ledger_trial_id(trial_records, runs, run),
                        "host": host,
                        "case": case,
                        "kind": "case_criterion_failed",
                        "detail": f"{criterion_key}={run.get('summary', {}).get(criterion_key)!r}",
                    }
                )
            for run in collection_errors:
                ledger.append(
                    {
                        "trial_id": _ledger_trial_id(trial_records, runs, run),
                        "host": host,
                        "case": case,
                        "kind": "collection_error",
                        "detail": run.get("collection_error"),
                    }
                )

            latency_samples = [
                run["latency_ms_total"] for run in runs if isinstance(run.get("latency_ms_total"), (int, float))
            ]
            reported_tokens = [run["usage"]["token_total"] for run in runs if _is_reported_usage(run)]
            unverified_tokens = len(runs) - len(reported_tokens)
            blocks.append(
                {
                    "host": host,
                    "case": case,
                    "expected": definition["repetitions"],
                    "attempted": len(runs),
                    "recorded": len(recorded),
                    "collection_errors": len(collection_errors),
                    "missing": definition["repetitions"] - len(runs),
                    "completion_rate": (
                        (len(recorded) / definition["repetitions"]) if definition["repetitions"] else None
                    ),
                    "criteria_pass": {"criterion_key": criterion_key, "by_source": by_source},
                    "latency_ms": _distribution(latency_samples),
                    "tokens": {
                        "reported_runs": len(reported_tokens),
                        "reported_token_total": sum(reported_tokens) if reported_tokens else None,
                        "unverified_runs": unverified_tokens,
                    },
                    "failure_kinds": {
                        "collection_error": len(collection_errors),
                        "case_criterion_failed": len(criterion_failures),
                        "security_check_failed": len(security_failures),
                        "missing_from_failed_trial": failed_trials,
                        "missing_from_skipped_trial": skipped_trials,
                    },
                }
            )
    return blocks


def _compact_run(run: dict[str, Any], extra_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    """Aggregate-visible projection of one run: verdicts and counters only.

    The raw traces (round messages, receipts, ledger actions) stay in the
    trial record file this aggregate pins by digest. Scenario adapters may
    name extra aggregate-visible fields (e.g. a clarification evidence
    block) that their runs carry.
    """

    compact = {
        "host": run.get("host"),
        "case": run.get("case"),
        "source": run.get("source"),
        "task_id": run.get("task_id"),
        "run_id": run.get("run_id"),
        "collection_error": run.get("collection_error"),
        "summary": run.get("summary"),
        "stop": run.get("stop"),
        "model_calls": run.get("model_calls"),
        "write_attempts": run.get("write_attempts"),
        "side_effect_count": run.get("side_effect_count"),
        "latency_ms_total": run.get("latency_ms_total"),
        "usage": run.get("usage"),
    }
    for field in extra_fields:
        compact[field] = run.get(field)
    return compact


def _ledger_trial_id(
    trial_records: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    run: dict[str, Any],
) -> str | None:
    """Best-effort trial attribution for a run; unknown stays None."""

    for trial in trial_records:
        if any(candidate is run for candidate in (trial.get("runs") or [])):
            return trial["trial_id"]
    return None


def _by_source_summary(trial_records: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for source in SOURCES:
        recorded = [
            run
            for trial in trial_records
            for run in (trial.get("runs") or [])
            if run.get("source") == source and run.get("collection_error") is None
        ]
        summary[source] = {
            "cases_recorded": len(recorded),
            "note": (
                "real-model evidence; quality is judged by real trials only"
                if source == "real_model"
                else "harness and stop-classification evidence; never a real-model quality result"
            ),
        }
    return summary


async def run_experiment(
    *,
    definition: dict[str, Any],
    state_root: Path,
    trial_report_dir: Path = BASELINE_DIR,
    case_pacing_seconds: float = 0.0,
    provider_factory: Any = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Run the definition's repetition matrix and return the aggregate report.

    Each trial gets its own identity and state directory and calls the
    scenario's single-shot collector, so Task/Run identities, model-call
    counters and business side-effect state are never carried across
    trials. Trial-level failures and skipped real-mode trials are
    preserved in the aggregate — they are never dropped before reporting.
    """

    adapter = resolve_scenario(scenario if scenario is not None else definition.get("scenario"))
    definition = copy.deepcopy(definition)
    _validate_collection_definition(definition, adapter)
    state_root = Path(state_root)
    trial_report_dir = Path(trial_report_dir)
    trial_report_dir.mkdir(parents=True, exist_ok=True)
    hermetic = definition["source_mode"] == "deterministic_injection"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    experiment_run_id = f"{definition['id']}-{stamp}-{uuid.uuid4().hex[:6]}"
    trial_records: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    def default_factory(trial_index: int) -> Any:
        return adapter.default_provider() if hermetic else None

    factory = provider_factory if provider_factory is not None else default_factory
    for index in range(definition["repetitions"]):
        trial_id = f"{experiment_run_id}-t{index:02d}"
        # Short dir name: nested Windows paths must stay well under MAX_PATH
        # for the hosts' profile files; the trial_id above stays the identity.
        trial_dir = state_root / experiment_run_id / f"t{index:02d}"
        record: dict[str, Any] = {
            "trial_id": trial_id,
            "trial_index": index,
            "status": "failed",
            "reason": None,
            "state_dir": str(trial_dir),
            "report": None,
            "report_digest": None,
            "runs": [],
        }
        try:
            provider = factory(index)
            if hermetic and provider is None:
                raise ValueError("deterministic experiments require an injected provider")
            trial_report = await adapter.collect(
                provider=provider,
                model=None if hermetic else definition["model"],
                hosts=tuple(definition["hosts"]),
                cases=tuple(definition["cases"]),
                root=trial_dir,
                case_pacing_seconds=0.0 if hermetic else case_pacing_seconds,
            )
        except Exception as exc:  # the trial failure is the record; later trials still run
            record["reason"] = f"{type(exc).__name__}: {exc}"
            ledger.append(
                {"trial_id": trial_id, "host": None, "case": None, "kind": "trial_failed", "detail": record["reason"]}
            )
            trial_report = {
                "_meta": {
                    "kind": "controller_host_experiment_trial_failure",
                    "status": "failed",
                    "trial_id": trial_id,
                    "reason": record["reason"],
                },
                "runs": [],
            }
        else:
            record["status"] = trial_report["_meta"]["status"]
            record["reason"] = trial_report["_meta"].get("reason")
            record["runs"] = trial_report.get("runs") or []
        if record["status"] == "skipped":
            ledger.append(
                {"trial_id": trial_id, "host": None, "case": None, "kind": "trial_skipped", "detail": record["reason"]}
            )
        filename = f"controller_host_experiment_trial_{trial_id}.json"
        text = json.dumps(trial_report, indent=2, ensure_ascii=False, default=str)
        # Binary writes make the digest independent of platform newline
        # translation; exclusive creation preserves prior evidence.
        with (trial_report_dir / filename).open("xb") as output:
            output.write(text.encode("utf-8"))
        record["report"] = filename
        record["report_digest"] = text_digest(text)
        trial_records.append(record)

    recorded_trials = len([trial for trial in trial_records if trial["status"] == "recorded"])
    skipped_trials = len([trial for trial in trial_records if trial["status"] == "skipped"])
    failed_trials = len([trial for trial in trial_records if trial["status"] == "failed"])
    status = "recorded" if recorded_trials else ("skipped" if skipped_trials else "failed")
    real_recorded = _by_source_summary(trial_records)["real_model"]["cases_recorded"]
    aggregate: dict[str, Any] = {
        "_meta": {
            "kind": "controller_host_experiment",
            "experiment_version": EXPERIMENT_VERSION,
            "status": status,
            "gate": False,
            "business_value": "unverified",
            "experiment_run_id": experiment_run_id,
            "recorded_at": datetime.now(UTC).isoformat(),
            "definition": definition,
            "definition_digest": definition_digest(definition),
            "model": "deterministic-injection" if hermetic else definition["model"],
            "source_revision": collector._source_identity(),
            "conclusion": {
                "real_model_quality": "recorded_below" if real_recorded else "not_available",
                "real_model_cases_recorded": real_recorded,
                "deterministic_scope": "harness and stop-classification repetition evidence; never model quality",
            },
            "review": {
                "status": "not_performed",
                "note": "manual review records are separate immutable files keyed by this report's digest",
            },
            "unverified": list(adapter.unverified),
        },
        "trials": [
            {
                **{
                    key: trial[key]
                    for key in ("trial_id", "trial_index", "status", "reason", "state_dir", "report", "report_digest")
                },
                "runs": [_compact_run(run, adapter.extra_run_fields) for run in trial.get("runs") or []],
            }
            for trial in trial_records
        ],
        "aggregate": {
            "denominators": {
                "repetitions_requested": definition["repetitions"],
                "trials_recorded": recorded_trials,
                "trials_skipped": skipped_trials,
                "trials_failed": failed_trials,
            },
            "cases": _case_blocks(definition, trial_records, ledger, adapter=adapter),
            "failure_ledger": ledger,
            "security": _security_block(definition, trial_records, adapter=adapter),
            "by_source": _by_source_summary(trial_records),
            "usage": _usage_summary(trial_records),
            "cost": {
                "status": "unknown",
                "reason": "no independent billing source; estimates are never recorded as cost",
            },
        },
    }
    Draft202012Validator(adapter.report_schema).validate(aggregate)
    return aggregate


def _security_block(
    definition: dict[str, Any],
    trial_records: list[dict[str, Any]],
    *,
    adapter: ScenarioAdapter | None = None,
) -> dict[str, Any]:
    """Security verdicts kept separate from every quality average."""

    zero_write_cases = tuple(definition["criteria"]["security"]["zero_write_cases"])
    extra_rules = (adapter.security_rules if adapter else None) or {}
    checks_per_host = sum(
        1 + (case in zero_write_cases) + len(extra_rules.get(case, ())) for case in definition["cases"]
    )
    expected = definition["repetitions"] * len(definition["hosts"]) * checks_per_host
    failures: list[dict[str, Any]] = []
    evaluable = 0
    passed = 0
    for host in definition["hosts"]:
        for case in definition["cases"]:
            for trial in trial_records:
                for run in trial.get("runs") or []:
                    if run.get("host") != host or run.get("case") != case:
                        continue
                    collection_failed = run.get("collection_error") is not None
                    if collection_failed and not extra_rules:
                        continue
                    checks = _security_checks(run, zero_write_cases, extra_rules.get(case, ()))
                    for check in checks:
                        evaluable += 1
                        if check["passed"]:
                            passed += 1
                        else:
                            failures.append({"trial_id": trial["trial_id"], "host": host, "case": case, **check})
    return {
        "checks_expected": expected,
        "checks_missing": expected - evaluable,
        "checks_evaluable": evaluable,
        "checks_passed": passed,
        "checks_failed": len(failures),
        "all_passed": False if failures else (True if evaluable == expected else None),
        "failures": failures,
        "note": "security verdicts are reported independently of quality averages",
    }


def _usage_summary(trial_records: list[dict[str, Any]]) -> dict[str, Any]:
    runs = [run for trial in trial_records for run in (trial.get("runs") or [])]
    reported = [run["usage"]["token_total"] for run in runs if _is_reported_usage(run)]
    return {
        "reported_runs": len(reported),
        "reported_token_total": sum(reported) if reported else None,
        "unverified_runs": len(runs) - len(reported),
    }


def write_experiment_report(report: dict[str, Any], out_dir: Path = BASELINE_DIR) -> Path:
    """Write the aggregate as a new immutable record; never overwrite."""

    adapter = resolve_scenario((report.get("_meta", {}).get("definition") or {}).get("scenario"))
    Draft202012Validator(adapter.report_schema).validate(report)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"controller_host_experiment_{report['_meta']['experiment_run_id']}.json"
    with out.open("xb") as output:
        output.write(json.dumps(report, indent=2, ensure_ascii=False, default=str).encode("utf-8"))
    return out


def comparison_group(report: dict[str, Any]) -> str:
    """The definition digest a record belongs to; non-experiment records refuse."""

    meta = report.get("_meta") or {}
    if meta.get("kind") != "controller_host_experiment":
        raise ValueError("comparison groups are defined for experiment records only")
    # Archived definitions remain comparable without imposing today's live
    # scenario schema, but their recorded identity must still be authentic.
    if not isinstance(meta.get("definition"), dict) or spec.digest(meta["definition"]) != meta.get("definition_digest"):
        raise ValueError("definition digest does not match the record's definition")
    return meta["definition_digest"]


def compare_records(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Compare two aggregate records inside one definition-digest group.

    Records from different groups (any criteria or input change re-keys the
    digest) return ``comparable: false`` and never produce quality deltas.
    """

    group_first = comparison_group(first)
    group_second = comparison_group(second)
    if group_first != group_second:
        return {
            "comparable": False,
            "reason": "definition or scenario drift; the records are different comparison groups",
            "definition_digests": [group_first, group_second],
        }
    cases_first = {(block["host"], block["case"]): block for block in first["aggregate"]["cases"]}
    cases_second = {(block["host"], block["case"]): block for block in second["aggregate"]["cases"]}
    deltas: list[dict[str, Any]] = []
    for key in sorted(cases_first):
        block_first = cases_first[key]
        block_second = cases_second.get(key)
        if block_second is None:
            deltas.append({"host": key[0], "case": key[1], "present_in_both": False})
            continue
        rate_delta: dict[str, Any] = {}
        for source in SOURCES:
            rate_first = block_first["criteria_pass"]["by_source"][source]["rate"]
            rate_second = block_second["criteria_pass"]["by_source"][source]["rate"]
            rate_delta[source] = (
                None if rate_first is None or rate_second is None else round(rate_second - rate_first, 6)
            )
        latency_first = block_first["latency_ms"]["p50_ms"]
        latency_second = block_second["latency_ms"]["p50_ms"]
        deltas.append(
            {
                "host": key[0],
                "case": key[1],
                "present_in_both": True,
                "completion_rate_delta": (
                    None
                    if block_first["completion_rate"] is None or block_second["completion_rate"] is None
                    else round(block_second["completion_rate"] - block_first["completion_rate"], 6)
                ),
                "criteria_rate_delta": rate_delta,
                "latency_p50_delta_ms": (
                    None if latency_first is None or latency_second is None else latency_second - latency_first
                ),
            }
        )
    return {
        "comparable": True,
        "definition_digest": group_first,
        "experiment_run_ids": [
            first["_meta"]["experiment_run_id"],
            second["_meta"]["experiment_run_id"],
        ],
        "cases": deltas,
    }


def write_review_record(
    report_path: Path,
    review: dict[str, Any],
    *,
    out_dir: Path = BASELINE_DIR,
) -> Path:
    """Append a manual review as a new immutable file bound to one report.

    The record is keyed by the exact digest of the aggregate file on disk,
    so a review can never silently attach to a different record. Uncollected
    review measurements (duration, counts) stay null — they are never
    zero-filled.
    """

    report_path = Path(report_path)
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    meta = report.get("_meta") or {}
    if meta.get("kind") != "controller_host_experiment":
        raise ValueError("review records target experiment aggregate records only")
    Draft202012Validator(REVIEW_SCHEMA).validate(review)
    record = {
        "kind": "controller_host_experiment_review",
        "review_version": REVIEW_VERSION,
        "target": {
            "experiment_run_id": meta["experiment_run_id"],
            "report_digest": hashlib.sha256(report_bytes).hexdigest(),
        },
        "review": review,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    Draft202012Validator(REVIEW_RECORD_SCHEMA).validate(record)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    out = out_dir / f"controller_host_experiment_review_{stamp}.json"
    with out.open("xb") as output:
        output.write(json.dumps(record, indent=2, ensure_ascii=False).encode("utf-8"))
    return out


def load_review_records(report_path: Path, *, search_dir: Path = BASELINE_DIR) -> list[dict[str, Any]]:
    """Load the review records bound to one aggregate report file."""

    report_path = Path(report_path)
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    records: list[dict[str, Any]] = []
    for path in sorted(Path(search_dir).glob("controller_host_experiment_review_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator(REVIEW_RECORD_SCHEMA).validate(record)
        if record["target"]["report_digest"] == digest:
            records.append(record)
    records.sort(key=lambda record: record["recorded_at"])
    return records


def review_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize review records; absent measurements stay None, never zero."""

    verdicts = {verdict: 0 for verdict in ("agree", "disagree", "partial")}
    for record in records:
        verdicts[record["review"]["verdict"]] += 1

    def total(field: str) -> int | None:
        values = [record["review"].get(field) for record in records if record["review"].get(field) is not None]
        return sum(values) if values else None

    return {
        "count": len(records),
        "verdicts": verdicts,
        "trials_reviewed_total": total("trials_reviewed"),
        "cases_reviewed_total": total("cases_reviewed"),
        "reported_duration_ms_total": total("duration_ms"),
    }


def experiment_report_schema_for(definition_schema: dict[str, Any]) -> dict[str, Any]:
    """The experiment report schema bound to one scenario's definition schema."""

    report_schema = copy.deepcopy(EXPERIMENT_REPORT_SCHEMA)
    report_schema["properties"]["_meta"]["properties"]["definition"] = copy.deepcopy(definition_schema)
    return report_schema


# The default dual-host matrix keeps its original schemas, digest space and
# collector; additional scenarios register their own adapters on import.
register_scenario(
    ScenarioAdapter(
        name=DEFAULT_SCENARIO,
        collect=_collect_dual_host,
        scenario_digest=scenario_digest,
        hosts=tuple(spec.HOSTS),
        cases=tuple(spec.CASES),
        definition_schema=DEFINITION_SCHEMA,
        report_schema=EXPERIMENT_REPORT_SCHEMA,
        planned_source=_dual_host_planned_source,
        validate_definition=_dual_host_validate_definition,
        default_provider=spec.DeterministicProvider,
        extra_run_fields=(),
    )
)
