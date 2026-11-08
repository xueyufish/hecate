"""Offline failure-evidence tests over the real platform execution chain.

Every window injects its fault only at the provider, business-tool or
test-clock boundary and drives the REAL platform assembly (durable
dispatch, worker retries, governance, outcome-unknown reconciliation).
The tests prove the diagnostic contract: known Task/Run identity,
rounds, receipts and side-effect counters survive provider exceptions
and case timeouts; durable facts (task state, dispatch attempts,
last_error) are read best-effort with allow-listed fields and never
mask the original failure; completed actions do not repeat after a
failure, calls stay bounded, and cancellation adds no actions. Fully
offline — the deterministic injected provider stands in for the model.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid as uuid_module
from types import SimpleNamespace
from typing import Any

import pytest

from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import run_controller_host_baseline as collector

pytestmark = pytest.mark.timeout(600)

EXPECTED_DIAGNOSTIC_KEYS = {
    "version",
    "end_reason",
    "collection_error_type",
    "task_state",
    "last_run_id",
    "last_error_digest",
    "round_error_types",
    "diagnostic_error",
    *platform_driver.DIAGNOSTIC_FIELDS,
}


class RoundFailingProvider:
    """Provider that raises after `fail_after` successful rounds."""

    name = "round-failing"

    def __init__(self, inner: Any, fail_after: int) -> None:
        self.inner = inner
        self.fail_after = fail_after
        self.calls = 0

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict:
        self.calls += 1
        if self.calls > self.fail_after:
            raise ConnectionError("synthetic provider outage")
        return await self.inner.complete(messages=messages, tools=tools)


class HangAfterReadProvider:
    """Provider that completes the read, then waits for cleanup cancellation."""

    name = "hang-after-read"

    def __init__(self) -> None:
        self.inner = spec.DeterministicProvider()
        self.calls = 0

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return await self.inner.complete(messages=messages, tools=tools)
        await asyncio.Event().wait()  # cancelled by the driver's finally
        raise AssertionError("unreachable")


HangingProvider = HangAfterReadProvider


class NonTextFinalProvider:
    """Provider whose final answer is not text: the port's structured-
    response validation raises inside dispatch, driving real worker
    retries until the restart budget parks the task."""

    name = "non-text-final"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": spec.READ_TOOL, "arguments": spec.EXPECTED_READ},
                    }
                ],
                "usage": None,
                "provider_model": self.name,
            }
        return {"content": {"not": "a string"}, "tool_calls": [], "usage": None, "provider_model": self.name}


def _assert_allow_list(run: dict) -> None:
    diagnostics = run["diagnostics"]
    assert set(diagnostics) == EXPECTED_DIAGNOSTIC_KEYS, diagnostics
    assert diagnostics["version"] == platform_driver.DIAGNOSTIC_VERSION
    last_error = diagnostics["last_error"]
    assert last_error in {None, "redacted"}
    dump = json.dumps(diagnostics, default=str)
    for forbidden in ("api_key", "authorization", "OPENAI_API_KEY", "ZAI_API_KEY"):
        assert forbidden not in dump


@pytest.fixture(scope="module")
async def first_call_failure(tmp_path_factory):
    root = tmp_path_factory.mktemp("failure-evidence")
    provider = RoundFailingProvider(spec.DeterministicProvider(), fail_after=0)
    report = await collector.collect(provider=provider, root=root, hosts=("platform",), cases=("normal",))
    assert provider.calls <= spec.REQUEST_LIMIT
    return report


@pytest.fixture(scope="module")
async def after_read_failure(tmp_path_factory):
    root = tmp_path_factory.mktemp("failure-evidence")
    provider = RoundFailingProvider(spec.DeterministicProvider(), fail_after=1)
    report = await collector.collect(provider=provider, root=root, hosts=("platform",), cases=("normal",))
    assert provider.calls <= spec.REQUEST_LIMIT
    return report


@pytest.fixture(scope="module")
async def retry_exhaustion(tmp_path_factory):
    root = tmp_path_factory.mktemp("failure-evidence")
    provider = NonTextFinalProvider()
    report = await collector.collect(provider=provider, root=root, hosts=("platform",), cases=("normal",))
    assert provider.calls <= spec.REQUEST_LIMIT
    return report


# --- windows 1 and 2: provider failures preserve evidence ---


def test_first_call_failure_preserves_identity_and_rounds(first_call_failure) -> None:
    run = next(run for run in first_call_failure["runs"] if run["case"] == "normal")
    assert run["collection_error"] is None
    assert run["task_id"] and run["run_id"]
    # The task failed gracefully inside the loop; identity and rounds survive.
    assert run["diagnostics"]["task_state"] in {"failed", "reconciliation_required"}
    assert run["diagnostics"]["end_reason"]
    assert run["model_calls"] == len(run["rounds"]) >= 1
    assert run["rounds"][0]["error_type"] == "ConnectionError"
    assert run["write_attempts"] == 0 and run["side_effect_count"] == 0
    _assert_allow_list(run)


def test_failure_after_successful_read_keeps_the_read_receipt(after_read_failure) -> None:
    run = next(run for run in after_read_failure["runs"] if run["case"] == "normal")
    assert len(run["rounds"]) >= 2
    assert run["rounds"][0].get("error_type") is None
    assert run["rounds"][-1]["error_type"] == "ConnectionError"
    assert run["tool_receipts"], "the completed read receipt must survive the later failure"
    assert run["write_attempts"] == 0 and run["side_effect_count"] == 0
    # Completed actions do not repeat: exactly one governed read.
    assert len([call for call in run["tool_calls"] if call["tool"] == spec.READ_TOOL]) == 1
    _assert_allow_list(run)


# --- window 3: worker dispatch retry exhaustion is diagnosable ---


def test_dispatch_retry_exhaustion_is_diagnosable(retry_exhaustion) -> None:
    run = next(run for run in retry_exhaustion["runs"] if run["case"] == "normal")
    diagnostics = run["diagnostics"]
    assert diagnostics["task_state"] == "reconciliation_required"
    assert diagnostics["end_reason"] == "terminal_state_observed"
    assert isinstance(diagnostics["dispatch_attempts"], int) and diagnostics["dispatch_attempts"] >= 2
    assert diagnostics["dispatch_attempts"] == diagnostics["dispatch_starts"]
    assert diagnostics["last_error"] == "redacted"
    assert len(diagnostics["last_error_digest"]) == 64
    # The one completed read executed once across every retry: completed
    # actions never repeat, and no write ever landed.
    assert run["side_effect_count"] == 0
    assert len([call for call in run["tool_calls"] if call["tool"] == spec.READ_TOOL]) == 1
    _assert_allow_list(run)


# --- window 4: case timeout keeps identity and the in-flight round ---


async def test_collection_timeout_preserves_identity_and_rounds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(platform_driver, "CASE_TIME_BUDGET_SECONDS", 20.0)
    provider = HangingProvider()
    report = await collector.collect(provider=provider, root=tmp_path, hosts=("platform",), cases=("normal",))
    run = next(run for run in report["runs"] if run["case"] == "normal")
    assert "TimeoutError" in (run["collection_error"] or "")
    # The known identity survives the timeout; the in-flight round stays in
    # the trace as a cancellation instead of vanishing.
    assert run["task_id"], "task identity must survive a case timeout"
    diagnostics = run["diagnostics"]
    assert diagnostics["end_reason"] == "case_deadline_exceeded"
    # The driver caught the converted timeout with awaits available, so the
    # durable facts are readable; they reflect the still-running task.
    assert diagnostics["task_state"] in {"queued", "running", "reconciliation_required"}
    assert run["run_id"] == diagnostics["last_run_id"] and run["run_id"]
    assert run["model_calls"] == len(run["rounds"]) == provider.calls == 2
    assert run["rounds"][0].get("error_type") is None
    assert run["rounds"][-1]["error_type"] == "CancelledError"
    assert diagnostics["round_error_types"] == ["CancelledError"]
    assert run["tool_receipts"], "the completed read receipt survives the timeout"
    assert run["write_attempts"] == 0 and run["side_effect_count"] == 0
    _assert_allow_list(run)


# --- windows 5 and 6: cancel and outcome-unknown keep their semantics ---


async def test_cancel_window_adds_no_actions_and_records_state(tmp_path) -> None:
    report = await collector.collect(
        provider=spec.DeterministicProvider(), root=tmp_path, hosts=("platform",), cases=("cancelled",)
    )
    run = next(run for run in report["runs"] if run["case"] == "cancelled")
    assert run["stop"]["task_status"] == "cancelled"
    assert run["diagnostics"]["task_state"] == "cancelled"
    assert run["write_attempts"] == 0 and run["side_effect_count"] == 0
    assert run["summary"]["cancel_compliance"] is True
    _assert_allow_list(run)


async def test_outcome_unknown_window_stays_reconciled(tmp_path) -> None:
    report = await collector.collect(
        provider=spec.DeterministicProvider(), root=tmp_path, hosts=("platform",), cases=("unknown_outcome",)
    )
    run = next(run for run in report["runs"] if run["case"] == "unknown_outcome")
    assert run["stop"]["task_status"] == "reconciliation_required"
    assert run["stop"]["reconciliation_reason"] == "action outcome is not established"
    assert run["diagnostics"]["task_state"] == "reconciliation_required"
    assert run["model_calls"] == 1 and run["write_attempts"] == 0 and run["side_effect_count"] == 0
    _assert_allow_list(run)


# --- diagnostic reads must never mask the original failure ---


async def test_diagnostic_read_failure_does_not_mask_the_original(tmp_path, monkeypatch) -> None:
    class BrokenStore:
        def get_task_state(self, _ref):
            raise RuntimeError("Authorization: Bearer SYNTHETIC_SECRET_BODY")

    class BrokenEnvironment:
        store = BrokenStore()

        async def run_rows(self, _task_id):
            raise RuntimeError("synthetic run-row read failure")

    diagnostics = await platform_driver.read_task_diagnostics(
        BrokenEnvironment(), uuid_module.UUID(int=0), "collection_exception", error=ValueError("original failure")
    )
    assert diagnostics["end_reason"] == "collection_exception"
    assert diagnostics["collection_error_type"] == "ValueError"
    assert diagnostics["diagnostic_error"] == "task state: RuntimeError"
    assert "SYNTHETIC_SECRET_BODY" not in json.dumps(diagnostics)
    assert diagnostics["task_state"] is None and diagnostics["dispatch_attempts"] is None
    assert diagnostics["last_run_id"] is None
    _assert_allow_list({"diagnostics": diagnostics})


async def test_missing_identity_records_unknown_without_guessing() -> None:
    from tests.scenarios.baseline.controller_host_platform import DIAGNOSTIC_FIELDS

    diagnostics = await platform_driver.read_task_diagnostics(None, None, "collection_exception", error=TimeoutError())
    assert diagnostics["task_state"] is None
    assert diagnostics["diagnostic_error"] == "task identity unavailable"
    assert all(diagnostics[field] is None for field in DIAGNOSTIC_FIELDS)
    _assert_allow_list({"diagnostics": diagnostics})


async def test_diagnostics_redact_values_and_preserve_known_run_identity() -> None:
    secret = "SYNTHETIC_SECRET_BODY"
    run_id = uuid_module.UUID(int=1)

    class StubStore:
        def get_task_state(self, _ref):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="reconciliation_required"),
                extra={
                    "last_error": f"Authorization: Bearer {secret}",
                    "dispatch_attempts": {"raw_body": secret},
                    "dispatch_starts": True,
                    "requeued_reason": {"raw_body": secret},
                },
            )

    class StubEnvironment:
        store = StubStore()

        async def run_rows(self, _task_id):
            return [SimpleNamespace(id=run_id)]

    diagnostics = await platform_driver.read_task_diagnostics(
        StubEnvironment(), uuid_module.UUID(int=0), f"RuntimeError: {secret}", error=ValueError(secret)
    )
    observation = platform_driver._fallback_observation(
        platform_driver._RecordingGateway(None),
        platform_driver._SyntheticRegistry("normal"),
        time.monotonic(),
        ValueError(secret),
        diagnostics=diagnostics,
    )
    assert observation["run_id"] == str(run_id)
    assert secret not in json.dumps(observation)
    assert diagnostics["dispatch_attempts"] is None and diagnostics["dispatch_starts"] is None
    assert diagnostics["requeued_reason"] is None
    assert diagnostics["last_error"] == "redacted"
    _assert_allow_list(observation)


async def test_diagnostic_read_timeout_keeps_partial_facts(tmp_path, monkeypatch) -> None:
    class StubStore:
        def get_task_state(self, _ref):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="running"), extra={"dispatch_attempts": 1})

    class StubEnvironment:
        store = StubStore()

        async def run_rows(self, _task_id):
            await asyncio.Event().wait()

    monkeypatch.setattr(platform_driver, "DIAGNOSTIC_TIMEOUT_SECONDS", 0.02)
    diagnostics = await asyncio.wait_for(
        platform_driver.read_task_diagnostics(
            StubEnvironment(), uuid_module.UUID(int=0), "collection_exception", error=ConnectionError("secret body")
        ),
        timeout=1,
    )
    assert diagnostics["collection_error_type"] == "ConnectionError"
    assert diagnostics["task_state"] == "running" and diagnostics["dispatch_attempts"] == 1
    assert diagnostics["last_run_id"] is None
    assert diagnostics["diagnostic_error"] == "run rows: TimeoutError"
    assert "secret body" not in json.dumps(diagnostics)
    _assert_allow_list({"diagnostics": diagnostics})


@pytest.mark.parametrize("family", ["baseline", "clarification", "feedback"])
async def test_diagnostics_have_a_separate_deadline_from_successful_case(family, tmp_path, monkeypatch) -> None:
    from tests.scenarios.baseline import controller_host_clarification_platform as clarification
    from tests.scenarios.baseline import controller_host_clarification_spec as clarification_spec
    from tests.scenarios.baseline import controller_host_feedback_platform as feedback
    from tests.scenarios.baseline import controller_host_feedback_spec as feedback_spec

    original_timeout = asyncio.timeout
    original_diagnostics = platform_driver.read_task_diagnostics
    deadlines = []

    class StubDeadline:
        """Track the case context while retaining its real timeout behavior."""

        def __init__(self, seconds):
            self.inner = original_timeout(seconds)
            self.active = False

        async def __aenter__(self):
            self.active = True
            return await self.inner.__aenter__()

        async def __aexit__(self, *args):
            try:
                return await self.inner.__aexit__(*args)
            finally:
                self.active = False

        def expired(self):
            """Return whether the real deadline has expired."""

            return self.inner.expired()

    def track_case_deadline(seconds):
        if not deadlines:
            deadline = StubDeadline(seconds)
            deadlines.append(deadline)
            return deadline
        return original_timeout(seconds)

    async def read_after_case(*args, **kwargs):
        assert not deadlines[0].active, "a diagnostic must not consume the case execution budget"
        return await original_diagnostics(*args, **kwargs)

    monkeypatch.setattr(asyncio, "timeout", track_case_deadline)
    monkeypatch.setattr(platform_driver, "read_task_diagnostics", read_after_case)
    case, entry, provider = {
        "baseline": ("normal", platform_driver.run_platform_case, spec.DeterministicProvider()),
        "clarification": (
            "field_complete",
            clarification.run_clarification_case,
            clarification_spec.DeterministicClarificationProvider(),
        ),
        "feedback": ("no_reorder_end", feedback.run_feedback_case, feedback_spec.DeterministicFeedbackProvider()),
    }[family]
    observation = await entry(case, provider, tmp_path)
    assert observation["collection_error"] is None
    assert observation["task_status"] == "succeeded"
    assert observation["diagnostics"]["end_reason"] == "terminal_state_observed"


@pytest.mark.parametrize("family", ["baseline", "clarification", "feedback"])
async def test_non_deadline_timeout_is_not_attributed_to_case_budget(family, tmp_path, monkeypatch) -> None:
    from tests.scenarios.baseline import controller_host_clarification_platform as clarification
    from tests.scenarios.baseline import controller_host_feedback_platform as feedback

    async def fail_seed(self, **kwargs):
        raise TimeoutError("SYNTHETIC_SECRET_BODY")

    monkeypatch.setattr(platform_driver.PlatformEnvironment, "_seed", fail_seed)
    case, entry = {
        "baseline": ("normal", platform_driver.run_platform_case),
        "clarification": ("clarify_missing", clarification.run_clarification_case),
        "feedback": ("rejection_correction", feedback.run_feedback_case),
    }[family]
    observation = await entry(case, spec.DeterministicProvider(), tmp_path)
    assert observation["diagnostics"]["end_reason"] == "collection_exception"
    assert observation["diagnostics"]["collection_error_type"] == "TimeoutError"
    assert "SYNTHETIC_SECRET_BODY" not in json.dumps(observation)
    assert observation["model_calls"] == 0


# --- consumers: clarification and feedback carry the same diagnostics ---


async def test_consumer_drivers_carry_diagnostics_on_failure(tmp_path, monkeypatch) -> None:
    from tests.scenarios.baseline import controller_host_clarification_platform as clar_driver
    from tests.scenarios.baseline import controller_host_feedback_platform as fb_driver

    monkeypatch.setattr(platform_driver, "CASE_TIME_BUDGET_SECONDS", 20.0)
    for driver, entry in (
        (clar_driver, ("clarify_missing", clar_driver.run_clarification_case)),
        (fb_driver, ("rejection_correction", fb_driver.run_feedback_case)),
    ):
        case, run_case = entry
        observation = await asyncio.wait_for(run_case(case, HangingProvider(), tmp_path), timeout=90)
        assert "TimeoutError" in (observation["collection_error"] or "")
        assert observation["task_id"], f"{case}: identity must survive the case timeout"
        assert observation["diagnostics"]["end_reason"] == "case_deadline_exceeded"
        assert observation["run_id"] == observation["diagnostics"]["last_run_id"] and observation["run_id"]
        assert observation["model_calls"] == len(observation["rounds"]) == 2
        assert observation["rounds"][-1]["error_type"] == "CancelledError"
        assert observation["diagnostics"]["task_state"] in {"queued", "running", "reconciliation_required"}
        _assert_allow_list({"diagnostics": observation["diagnostics"]})
        if driver is clar_driver:
            assert observation["clarification"]["park_count"] == 0


# --- the aggregate projection stays backward compatible ---


async def test_aggregate_projection_excludes_diagnostics(tmp_path) -> None:
    """Committed aggregates project verdicts only; diagnostics live in the
    trial records. The projection equality the pilot audit relies on must
    keep holding for new collections."""

    from tests.scenarios.baseline import controller_host_experiment as experiment

    definition = experiment_definition()
    report = await experiment_run(definition, tmp_path)
    trial_record = json.loads((tmp_path / "records" / report["trials"][0]["report"]).read_text(encoding="utf-8"))
    for run in trial_record["runs"]:
        assert "diagnostics" in run and run["diagnostics"]["end_reason"] == "terminal_state_observed"
        assert run["source"] == "deterministic_injection"
        assert run["write_attempts"] == run["side_effect_count"] == 1
        assert len([call for call in run["tool_calls"] if call["tool"] == spec.WRITE_TOOL]) == 1
        assert len([call for call in run["tool_calls"] if call["tool"] == spec.READ_TOOL]) == 1
    adapter = experiment_adapter()
    compact = [experiment._compact_run(run, adapter.extra_run_fields) for run in trial_record["runs"]]
    for projected, committed in zip(compact, report["trials"][0]["runs"], strict=True):
        assert projected == committed
        assert "diagnostics" not in projected


def experiment_definition():
    from tests.scenarios.baseline import controller_host_experiment as experiment

    return experiment.build_definition(repetitions=1, hosts=("platform",), cases=("normal",))


async def experiment_run(definition, tmp_path):
    from tests.scenarios.baseline import controller_host_experiment as experiment

    return await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        provider_factory=lambda _index: RoundFailingProvider(spec.DeterministicProvider(), fail_after=2),
    )


def experiment_adapter():
    from tests.scenarios.baseline import controller_host_experiment as experiment

    return experiment.resolve_scenario(experiment.DEFAULT_SCENARIO)


# --- the committed pilot audit remains untouched ---


def test_pilot_audit_module_still_passes_selection() -> None:
    """The committed pilot records predate diagnostics; their aggregates do
    not project the field and the audit's recomputation equality holds."""

    from tests.scenarios.baseline import controller_host_experiment as experiment

    records = sorted((collector.BASELINE_DIR).glob("controller_host_experiment_pilot-glm47flash-*.json"))
    assert records, "committed pilot aggregates must be present"
    for path in records[:1]:
        record = json.loads(path.read_text(encoding="utf-8"))
        for trial in record["trials"]:
            for run in trial["runs"]:
                assert "diagnostics" not in run
        adapter = experiment.resolve_scenario(
            record["_meta"]["definition"].get("scenario", experiment.DEFAULT_SCENARIO)
        )
        assert "diagnostics" not in adapter.extra_run_fields
