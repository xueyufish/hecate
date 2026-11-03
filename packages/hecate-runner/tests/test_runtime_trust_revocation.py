"""Runtime artifact-trust revocation: new calls stop when the policy changes.

Covers the opt-in re-check contract: the guard freezes the admitted facts
from the startup policy object, re-reads the operator's policy file before
every new model request and real tool dispatch, refuses on publisher or
key revocation/removal, a replaced key under the same id, and an unusable
policy file, latches explicit revocation for the process, tolerates
unrelated policy edits, converges on the authorization semantics, records
correlating evidence without secret material, never re-executes decided
actions, and keeps the CLI opt-in honest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest_runner import write_profile
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hecate_durable.storage import SqlDurableStore
from hecate_runner.artifact_signing import load_trust_policy, sign_manifest
from hecate_runner.durable import DurableRuntime
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import load_profile
from hecate_runner.runtime_trust import (
    REVOCATION_CATEGORY,
    RuntimeTrustGuard,
    RuntimeTrustRevokedError,
)

PUBLISHER = "publisher:acme"
KEY_ID = "acme-2026-01"
ENVELOPE_NAME = "artifact-signature.json"
READ_ARGS = {"domain": "domain_a", "sku": "A"}


def _keypair() -> tuple[bytes, str]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def _write_policy(
    path: Path,
    public_pem: str,
    *,
    revoked: bool = False,
    revoked_key_ids: list[str] | None = None,
    key_id: str = KEY_ID,
    publisher: str = PUBLISHER,
    extra_publishers: dict | None = None,
) -> Path:
    entry: dict = {"keys": [{"key_id": key_id, "public_key": public_pem}], "revoked": revoked}
    if revoked_key_ids:
        entry["revoked_key_ids"] = revoked_key_ids
    policy = {"artifact_trust_version": "1", "publishers": {publisher: entry, **(extra_publishers or {})}}
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def _sign_profile(profile_dir: Path, private_pem: bytes) -> None:
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["publisher_ref"] = PUBLISHER
    raw["signature_ref"] = ENVELOPE_NAME
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    envelope = sign_manifest(raw, private_pem, publisher_ref=PUBLISHER, key_id=KEY_ID)
    (profile_dir / ENVELOPE_NAME).write_text(json.dumps(envelope), encoding="utf-8")


class _BusinessApi:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments)})
        return {"status": "ok", "result": f"{tool_name}-result"}


@pytest.fixture()
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


def _admitted(tmp_path: Path, *, durable: bool = False):
    """A signed, admitted profile plus the live policy path and startup policy."""

    private_pem, public_pem = _keypair()
    kwargs: dict = {}
    if durable:
        kwargs["durable"] = {"database_url": f"sqlite:///{tmp_path / 'durable.db'}", "workspace": "revocation"}
    profile_dir = write_profile(tmp_path, **kwargs)
    _sign_profile(profile_dir, private_pem)
    policy_path = _write_policy(tmp_path / "artifact-trust.json", public_pem)
    startup_policy = load_trust_policy(policy_path)
    profile = load_profile(profile_dir, artifact_trust=startup_policy)
    assert profile.artifact_admission is not None
    return profile, policy_path, startup_policy, public_pem


# -- guard unit behaviour ----------------------------------------------------


def test_guard_passes_while_policy_still_backs_the_frozen_identity(tmp_path, _shutdown_env) -> None:
    profile, policy_path, startup_policy, _public = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)
    assert guard.check() is None
    facts = guard.frozen_facts()
    assert facts["publisher_ref"] == PUBLISHER and facts["key_id"] == KEY_ID
    assert facts["manifest_sha256"] == profile.artifact_admission.manifest_sha256
    # Only a digest of the key is exposed, never the key itself.
    assert "BEGIN PUBLIC KEY" not in json.dumps(facts)


def test_guard_rejects_a_mismatched_startup_policy_object(tmp_path, _shutdown_env) -> None:
    profile, _policy_path, _startup, _public = _admitted(tmp_path)
    _other_private, other_public = _keypair()
    unrelated = load_trust_policy(
        _write_policy(tmp_path / "other.json", other_public, publisher="publisher:other", key_id="other")
    )
    with pytest.raises(RuntimeError, match="policy object admission verified against"):
        RuntimeTrustGuard(profile.artifact_admission, unrelated)


@pytest.mark.parametrize(
    "change, match",
    [
        ("revoke_publisher", "is revoked"),
        ("revoke_key", "is revoked for publisher"),
        ("remove_publisher", "no longer in the artifact trust policy"),
        ("remove_key", "no longer trusted"),
        ("replace_key", "replaced with a different public key"),
        ("delete_file", "unusable"),
        ("corrupt_file", "unusable"),
    ],
)
def test_guard_refuses_on_each_revocation_shape(tmp_path, _shutdown_env, change: str, match: str) -> None:
    profile, policy_path, startup_policy, public_pem = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)
    assert guard.check() is None

    if change == "revoke_publisher":
        _write_policy(policy_path, public_pem, revoked=True)
    elif change == "revoke_key":
        _write_policy(policy_path, public_pem, revoked_key_ids=[KEY_ID])
    elif change == "remove_publisher":
        _other_private, other_public = _keypair()
        _write_policy(policy_path, other_public, publisher="publisher:other", key_id="other")
    elif change == "remove_key":
        _write_policy(policy_path, public_pem, key_id="acme-rotated")
    elif change == "replace_key":
        _other_private, other_public = _keypair()
        _write_policy(policy_path, other_public)  # same KEY_ID, different public key
    elif change == "delete_file":
        policy_path.unlink()
    elif change == "corrupt_file":
        policy_path.write_text("{ not json", encoding="utf-8")

    refusal = guard.check()
    assert refusal is not None
    assert match in refusal.reason
    assert refusal.publisher_ref == PUBLISHER and refusal.key_id == KEY_ID
    assert refusal.artifact_sha256 == profile.artifact_admission.manifest_sha256
    evidence = refusal.evidence()
    assert evidence["error_category"] == REVOCATION_CATEGORY
    assert "BEGIN" not in json.dumps(evidence)
    with pytest.raises(RuntimeTrustRevokedError):
        guard.require()


def test_refusal_is_sticky_for_the_process(tmp_path, _shutdown_env) -> None:
    profile, policy_path, startup_policy, public_pem = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)
    _write_policy(policy_path, public_pem, revoked=True)
    first = guard.check()
    assert first is not None and "is revoked" in first.reason

    # The operator restores the policy: trust must NOT resurrect without a
    # restart and a fresh admission.
    _write_policy(policy_path, public_pem)
    second = guard.check()
    assert second is not None
    assert second.reason == first.reason


def test_unrelated_policy_edits_do_not_refuse(tmp_path, _shutdown_env) -> None:
    profile, policy_path, startup_policy, public_pem = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)
    _other_private, other_public = _keypair()
    # Add another publisher, add a second key to ours, revoke an unrelated
    # key id — none of it touches the frozen identity.
    _write_policy(
        policy_path,
        public_pem,
        extra_publishers={"publisher:other": {"keys": [{"key_id": "o1", "public_key": other_public}], "revoked": True}},
    )
    assert guard.check() is None
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["publishers"][PUBLISHER]["keys"].append({"key_id": "acme-next", "public_key": other_public})
    policy["publishers"][PUBLISHER]["revoked_key_ids"] = ["acme-next"]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    assert guard.check() is None


# -- engine integration ------------------------------------------------------


class _Harness:
    def __init__(self, tmp_path: Path, *, trust_guard, durable: bool, profile) -> None:
        self.profile = profile
        self.api = _BusinessApi()
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        self.durable = None
        self.store = None
        if durable:
            assert self.profile.config.durable is not None
            self.store = SqlDurableStore(self.profile.config.durable.database_url)
            self.store.create_schema()
            self.durable = DurableRuntime(self.store, workspace=self.profile.config.durable.workspace)
        self.engine = ExecutionEngine(
            self.profile, self.evidence, self.api, durable=self.durable, trust_guard=trust_guard
        )

    async def run(self) -> dict:
        run_id, _state = await self.engine.submit(
            "app-reader",
            {"input": {"prompt": "check"}, "tool_arguments": {"query_inventory": READ_ARGS}},
            ("domain_a",),
        )
        state = await self.engine.wait_for(run_id, timeout=30.0)
        return {"run_id": run_id, "state": state}

    def evidence_records(self, kind: str) -> list:
        return self.evidence.query(kind=kind)

    def close(self) -> None:
        if self.store is not None:
            self.store.dispose()


async def test_revocation_blocks_the_next_tool_dispatch_without_a_business_call(tmp_path, _shutdown_env) -> None:
    profile, policy_path, startup_policy, public_pem = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)
    harness = _Harness(tmp_path, trust_guard=guard, durable=False, profile=profile)
    try:
        first = await harness.run()
        assert first["state"].status == "succeeded"
        assert len(harness.api.calls) == 1

        _write_policy(policy_path, public_pem, revoked=True)
        second = await harness.run()
        # The real call count does not increase; the run converges on the
        # authorization failure semantics, never on acceptance success.
        assert len(harness.api.calls) == 1
        assert second["state"].status != "succeeded"
        assert second["state"].dispatch_denied is True
        denied = [record for record in harness.evidence_records("artifact_trust") if record.outcome == "denied"]
        assert denied, "revocation evidence must be recorded"
        payload = denied[0].detail
        assert payload["publisher_ref"] == PUBLISHER and payload["key_id"] == KEY_ID
        assert payload["artifact_sha256"] == profile.artifact_admission.manifest_sha256
        assert payload["error_category"] == REVOCATION_CATEGORY
        # Either entry point refuses first depending on the graph's schedule;
        # both must name their site and neither may leak key material.
        assert payload["call_site"].startswith(("tool:", "model:"))
        assert "BEGIN" not in json.dumps(payload)
    finally:
        harness.close()


async def test_startup_only_posture_is_unchanged_without_the_guard(tmp_path, _shutdown_env) -> None:
    profile, policy_path, _startup, public_pem = _admitted(tmp_path)
    harness = _Harness(tmp_path, trust_guard=None, durable=False, profile=profile)
    try:
        _write_policy(policy_path, public_pem, revoked=True)
        result = await harness.run()
        # Without the opt-in the policy file is not consulted while serving.
        assert result["state"].status == "succeeded"
        assert len(harness.api.calls) == 1
        assert harness.evidence_records("artifact_trust") == []
    finally:
        harness.close()


async def test_durable_decided_actions_replay_without_reexecution_after_revocation(tmp_path, _shutdown_env) -> None:
    profile, policy_path, startup_policy, public_pem = _admitted(tmp_path, durable=True)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)
    harness = _Harness(tmp_path, trust_guard=guard, durable=True, profile=profile)
    try:
        first = await harness.run()
        assert first["state"].status == "succeeded"
        assert len(harness.api.calls) == 1
        receipts_before = harness.store.list_tasks()
        assert len(receipts_before) == 1

        _write_policy(policy_path, public_pem, revoked_key_ids=[KEY_ID])
        second = await harness.run()
        assert len(harness.api.calls) == 1  # no new business call
        assert second["state"].status != "succeeded"
        # The completed task's receipt is untouched; the new task recorded
        # its own refusal rather than rewriting history.
        after = harness.store.list_tasks()
        assert len(after) == 2
        preserved = next(record for record in after if record.task_ref.id == receipts_before[0].task_ref.id)
        assert preserved.lifecycle_state == receipts_before[0].lifecycle_state
    finally:
        harness.close()


async def test_sticky_refusal_survives_policy_restore_in_the_engine(tmp_path, _shutdown_env) -> None:
    profile, policy_path, startup_policy, public_pem = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)
    harness = _Harness(tmp_path, trust_guard=guard, durable=False, profile=profile)
    try:
        _write_policy(policy_path, public_pem, revoked=True)
        await harness.run()
        _write_policy(policy_path, public_pem)  # restore
        restored = await harness.run()
        assert restored["state"].status != "succeeded"
        assert harness.api.calls == []
    finally:
        harness.close()


# -- CLI opt-in --------------------------------------------------------------


def test_main_requires_admission_for_the_runtime_recheck(tmp_path, _shutdown_env, capsys) -> None:
    from hecate_runner.__main__ import main

    profile_dir = write_profile(tmp_path)
    argv = ["--profile", str(profile_dir), "--artifact-trust-recheck", "--business-api", "http://127.0.0.1:9"]
    assert main(argv) == 2
    assert "--artifact-trust-recheck requires --artifact-trust" in capsys.readouterr().err


# -- controller loop path ----------------------------------------------------


class _CountingModelServer:
    """A real local HTTP model endpoint counting every fresh request."""

    def __init__(self, *, answer: str, on_request=None) -> None:
        self.answer = answer
        self.on_request = on_request
        self.payload = None
        self.requests: list[dict] = []
        self._server = None
        self.url = ""

    async def start(self) -> str:
        import asyncio

        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/invoke"
        return self.url

    async def stop(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader, writer) -> None:

        try:
            await reader.readline()
            headers: dict[str, str] = {}
            while True:
                header = await reader.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
                name, _, value = header.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            self.requests.append(json.loads(body.decode("utf-8")))
            if self.on_request is not None:
                self.on_request()
            payload = json.dumps(self.payload or {"content": self.answer}).encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except Exception:  # noqa: BLE001, S110 — connection-level failures end the exchange
            pass
        finally:
            writer.close()


async def test_controller_model_requests_stop_after_revocation(tmp_path, _shutdown_env, monkeypatch) -> None:
    """The controller's fresh model attempts are refused mid-loop."""

    import asyncio

    from hecate_runner.evidence import EvidenceStore

    private_pem, public_pem = _keypair()
    profile_dir = write_profile(
        tmp_path,
        durable={"database_url": f"sqlite:///{tmp_path / 'controller.db'}", "workspace": "ws-a"},
        controller_loop=True,
    )
    _sign_profile(profile_dir, private_pem)
    policy_path = _write_policy(tmp_path / "artifact-trust.json", public_pem)
    startup_policy = load_trust_policy(policy_path)

    # The endpoint must be configured BEFORE the profile is loaded: the
    # profile freezes the model backend at load time.
    model = _CountingModelServer(answer="task complete")
    await model.start()
    config_path = profile_dir / "runner.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["model"] = {"backend": "endpoint", "endpoint": model.url}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    profile = load_profile(profile_dir, artifact_trust=startup_policy)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup_policy)

    store = SqlDurableStore(config["durable"]["database_url"])
    store.create_schema()
    harness = DurableRuntime(store, workspace=config["durable"]["workspace"])
    # No budget gate on purpose: the trust re-check must not depend on a
    # budget being configured.
    engine = ExecutionEngine(
        profile,
        EvidenceStore(profile.config.evidence_dir),
        _BusinessApi(),
        durable=harness,
        trust_guard=guard,
    )
    try:
        run_id, _state = await engine.submit("app-reader", {"prompt": "check the stock"}, ("domain_a",))
        final = await asyncio.wait_for(engine.wait_for(run_id), timeout=30.0)
        assert final.status == "succeeded", (final.status, final.error)
        asked_before = len(model.requests)
        assert asked_before >= 1

        # Revoke mid-flight: the next fresh model attempt must refuse before
        # the HTTP request — the request count never grows again.
        _write_policy(policy_path, public_pem, revoked=True)
        run_id, _state = await engine.submit("app-reader", {"prompt": "check again"}, ("domain_a",))
        refused = await asyncio.wait_for(engine.wait_for(run_id), timeout=30.0)
        assert refused.status == "failed"
        assert refused.result is not None
        assert refused.result["error_category"] == "policy_denied"
        restored = engine._durable_task_state(refused.task_ref, refused.principal, refused.domains, refused.run_ref)
        assert restored.status == "failed"
        assert restored.result is not None and restored.result["error_category"] == "policy_denied"
        import copy

        broken = copy.deepcopy(store.get_task_state(refused.task_ref))
        broken.extra["result"]["artifact_trust"]["task_ref"]["id"] = "another-task"
        with monkeypatch.context() as patch:
            patch.setattr(store, "get_task_state", lambda _ref: broken)
            with pytest.raises(ValueError, match="invalid bindings"):
                harness.read_result(refused.task_ref, refused.run_ref)
        assert len(model.requests) == asked_before
        evidence = EvidenceStore(profile.config.evidence_dir)
        denied = [record for record in evidence.query(kind="artifact_trust") if record.outcome == "denied"]
        assert any(record.detail["call_site"] == "model:controller" for record in denied)
    finally:
        await engine.close()
        store.dispose()
        await model.stop()


@pytest.mark.parametrize("backend", ["graph", "controller"])
@pytest.mark.parametrize("release_fails", [False, True])
async def test_revocation_during_budget_reservation_prevents_the_model_request(
    tmp_path, _shutdown_env, monkeypatch, backend, release_fails
) -> None:
    from hecate_governance.usage import BudgetGate, MemoryBudgetLedger
    from hecate_runner.engine import RunState, _ControllerModelPort, _FanOutWorker

    profile, path, startup, public = _admitted(tmp_path)
    model = _CountingModelServer(answer="answer")
    await model.start()
    config_path = profile.directory / "runner.json"
    config = json.loads(config_path.read_text())
    config["model"] = {"backend": "endpoint", "endpoint": model.url}
    config_path.write_text(json.dumps(config))
    profile = load_profile(profile.directory, artifact_trust=startup)
    ledger = MemoryBudgetLedger(limit=10000)
    if release_fails:

        async def unavailable_release(_key):
            raise OSError("injected release failure")

        monkeypatch.setattr(ledger, "release", unavailable_release)

    class RevokingBudget(BudgetGate):
        async def ensure_budget(self, invocation_id, units):
            reservation = await super().ensure_budget(invocation_id, units)
            _write_policy(path, public, revoked=True)
            return reservation

    engine = ExecutionEngine(
        profile,
        EvidenceStore(profile.config.evidence_dir),
        _BusinessApi(),
        budget_gate=RevokingBudget(ledger),
        trust_guard=RuntimeTrustGuard(profile.artifact_admission, startup),
    )
    state = RunState("budget-race", "running", "app-reader", ("domain_a",))
    try:
        if backend == "controller":
            with pytest.raises(RuntimeTrustRevokedError):
                async for _chunk in _ControllerModelPort(engine, state).llm_invoke_structured([], {}):
                    pass
        else:
            result = await _FanOutWorker(engine, state).execute("model", {}, {"input": {"prompt": "check"}})
            assert isinstance(result.error, RuntimeTrustRevokedError)
        assert model.requests == []
        assert await ledger.remaining() == (5904 if release_fails else 10000)
        assert state.needs_reconciliation is release_fails
    finally:
        await engine.close()
        await model.stop()


def test_unusable_policy_refuses_the_current_call_without_latching_revocation(tmp_path, _shutdown_env) -> None:
    profile, path, startup, public = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup)
    path.write_text("{broken")
    assert guard.check() is not None
    _write_policy(path, public)
    assert guard.check() is None


def test_malformed_policy_never_echoes_accidental_key_material_into_evidence(tmp_path, _shutdown_env) -> None:
    profile, path, startup, _public = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup)
    private, _ = _keypair()
    raw = json.loads(path.read_text())
    raw["publishers"][PUBLISHER]["revoked_key_ids"] = [private.decode()]
    path.write_text(json.dumps(raw))
    refusal = guard.check()
    assert refusal is not None
    assert not any(marker in json.dumps(refusal.evidence()) for marker in ("PRIVATE KEY", "BEGIN"))


def test_concurrent_success_cannot_pass_an_already_observed_revocation(tmp_path, _shutdown_env, monkeypatch) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from hecate_runner import runtime_trust

    profile, path, startup, public = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup)
    loaded = threading.Event()
    resume = threading.Event()
    original = runtime_trust.load_trust_policy

    def delayed_load(policy_path):
        if threading.current_thread() is not threading.main_thread():
            loaded.set()
            assert resume.wait(5)
            return startup
        return original(policy_path)

    monkeypatch.setattr(runtime_trust, "load_trust_policy", delayed_load)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(guard.check)
        try:
            assert loaded.wait(5)
            _write_policy(path, public, revoked=True)
            assert guard.check() is not None
        finally:
            resume.set()
        assert pending.result(timeout=5) is not None


async def test_refusal_evidence_failure_does_not_erase_the_authorization_decision(
    tmp_path, _shutdown_env, monkeypatch
) -> None:
    from hecate_runner.engine import RunState

    profile, path, startup, public = _admitted(tmp_path)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup)
    harness = _Harness(tmp_path, trust_guard=guard, durable=False, profile=profile)
    original = harness.evidence.append

    def unavailable(kind, *args, **kwargs):
        if kind == "artifact_trust":
            raise OSError("injected refusal evidence failure")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(harness.evidence, "append", unavailable)
    _write_policy(path, public, revoked=True)
    state = RunState("evidence-failure", "running", "app-reader", ("domain_a",))
    outcome = await harness.engine._dispatch_tool(state, "query_inventory", READ_ARGS)
    assert outcome["status"] == "authorization"
    assert state.dispatch_denied
    assert harness.api.calls == []


async def test_revocation_between_model_response_and_readonly_dispatch(tmp_path, _shutdown_env) -> None:
    profile, path, startup, public = _admitted(tmp_path)
    model = _CountingModelServer(answer="answer", on_request=lambda: _write_policy(path, public, revoked=True))
    await model.start()
    config_path = profile.directory / "runner.json"
    config = json.loads(config_path.read_text())
    config["model"] = {"backend": "endpoint", "endpoint": model.url}
    config_path.write_text(json.dumps(config))
    profile = load_profile(profile.directory, artifact_trust=startup)
    harness = _Harness(
        tmp_path, trust_guard=RuntimeTrustGuard(profile.artifact_admission, startup), durable=False, profile=profile
    )
    try:
        result = await harness.run()
        assert len(model.requests) == 1  # The already-issued request completes.
        assert harness.api.calls == []
        assert result["state"].status == "failed"
        denied = harness.evidence_records("artifact_trust")
        assert any(record.detail["call_site"] == "tool:query_inventory" for record in denied)
    finally:
        await harness.engine.close()
        await model.stop()


async def test_actual_decided_action_replay_preserves_the_ledger_after_revocation(tmp_path, _shutdown_env) -> None:
    from hecate_durable.storage.models import ActionIntentRow, ActionOutcomeRow
    from sqlalchemy import select

    profile, path, startup, public = _admitted(tmp_path, durable=True)
    guard = RuntimeTrustGuard(profile.artifact_admission, startup)
    harness = _Harness(tmp_path, trust_guard=guard, durable=True, profile=profile)

    def ledger_rows():
        with harness.store.session_factory() as session:
            return [
                [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns}
                    for row in session.scalars(select(model)).all()
                ]
                for model in (ActionIntentRow, ActionOutcomeRow)
            ]

    try:
        first = await harness.run()
        before = ledger_rows()
        _write_policy(path, public, revoked=True)
        assert guard.check() is not None
        replay = await harness.engine._dispatch_tool(first["state"], "query_inventory", READ_ARGS)
        assert replay["status"] == "ok"
        assert len(harness.api.calls) == 1
        assert ledger_rows() == before
    finally:
        await harness.engine.close()
        harness.close()


def test_guard_initialization_failure_releases_the_host_lock(tmp_path, _shutdown_env, monkeypatch) -> None:
    from hecate_runner import __main__ as entry
    from hecate_runner import runtime_trust
    from hecate_runner.release import HostLock, activate_release, install_release

    profile, path, startup, _public = _admitted(tmp_path)
    sha = install_release(profile.directory, profile.directory, startup)
    activate_release(profile.directory, sha, startup, expected_generation=0)
    load_selection = entry.load_host_selection
    held = []

    def capture_lock(*args):
        result = load_selection(*args)
        held.append(result[1])
        return result

    def failed_guard(*args):
        raise RuntimeError("injected guard initialization failure")

    monkeypatch.setattr(entry, "load_host_selection", capture_lock)
    monkeypatch.setattr(runtime_trust, "RuntimeTrustGuard", failed_guard)
    try:
        with pytest.raises(RuntimeError, match="injected guard"):
            entry.main(["--host", str(profile.directory), "--artifact-trust", str(path), "--artifact-trust-recheck"])
        with HostLock(profile.directory):
            pass
    finally:
        for lock in held:
            lock.release()


@pytest.mark.parametrize("approval", [False, True])
async def test_managed_authorization_and_prior_approval_cannot_bypass_artifact_revocation(
    tmp_path, _shutdown_env, monkeypatch, approval
) -> None:
    from hecate_runner.engine import RunState

    tool = "submit_ticket" if approval else "submit_inventory_update"
    private, public = _keypair()
    directory = write_profile(
        tmp_path,
        allowlist=[tool],
        durable={"database_url": f"sqlite:///{tmp_path / 'managed.db'}", "workspace": "ws"},
        with_approval_tool=approval,
        with_write_tool=not approval,
    )
    _sign_profile(directory, private)
    path = _write_policy(tmp_path / "artifact-trust.json", public)
    startup = load_trust_policy(path)
    profile = load_profile(directory, artifact_trust=startup)
    harness = _Harness(
        tmp_path, trust_guard=RuntimeTrustGuard(profile.artifact_admission, startup), durable=True, profile=profile
    )
    lease_checks = []

    async def valid_lease(*args, **kwargs):
        lease_checks.append(True)
        return None

    monkeypatch.setattr(harness.engine, "_lease_refusal", valid_lease)
    _write_policy(path, public, revoked=True)
    state = RunState("managed-revocation", "running", "app-reader", ("domain_a",), managed=True)
    arguments = {"ticket": "approved-ticket"} if approval else {**READ_ARGS, "quantity": 1}
    try:
        outcome = await harness.engine._dispatch_tool(
            state,
            tool,
            arguments,
            accepted_context={"invocation_id": "already-authorized", "approval_ref": "prior-approval"},
        )
        assert lease_checks == [True]
        assert outcome["status"] == "authorization"
        assert state.trust_refusal is not None
        assert harness.api.calls == []
    finally:
        await harness.engine.close()
        harness.close()


async def test_waiting_controller_rechecks_before_a_new_model_call_after_wake(tmp_path, _shutdown_env) -> None:
    import asyncio

    private, public = _keypair()
    model = _CountingModelServer(answer="unused")
    model.payload = {
        "content": "",
        "tool_calls": [
            {
                "id": "first",
                "type": "function",
                "function": {"name": "query_inventory", "arguments": json.dumps(READ_ARGS)},
            }
        ],
    }
    await model.start()
    directory = write_profile(
        tmp_path,
        controller_loop=True,
        durable={"database_url": f"sqlite:///{tmp_path / 'waiting.db'}", "workspace": "ws"},
        model_backend={"backend": "endpoint", "endpoint": model.url},
    )
    _sign_profile(directory, private)
    path = _write_policy(tmp_path / "artifact-trust.json", public)
    startup = load_trust_policy(path)
    profile = load_profile(directory, artifact_trust=startup)
    store = SqlDurableStore(profile.config.durable.database_url)
    store.create_schema()
    durable = DurableRuntime(store, workspace="ws")
    calls = []

    async def require_input(*args):
        calls.append(args)
        return {"status": "input_required", "contract": {"fields": ["region"]}}

    engine = ExecutionEngine(
        profile,
        EvidenceStore(profile.config.evidence_dir),
        require_input,
        durable=durable,
        trust_guard=RuntimeTrustGuard(profile.artifact_admission, startup),
    )
    try:
        run_id, _state = await engine.submit("app-reader", {"prompt": "check"}, ("domain_a",))
        waiting = await engine.wait_for(run_id, timeout=15)
        assert waiting.status == "waiting_input"
        assert len(calls) == len(model.requests) == 1
        task_ref, run_ref = waiting.task_ref, waiting.run_ref
        before = store.list_run_actions(run_ref)
        wait = durable.wait_of(task_ref)
        assert wait is not None
        _write_policy(path, public, revoked=True)
        receipt, reason = durable.apply_wake(
            command_id="wake-after-revocation",
            kind="provide_input",
            issuer="app-reader",
            task_ref=task_ref,
            wait_token=wait["wait_token"],
            input_payload={"region": "EU"},
            keep_run_linkage=True,
            controller_loop=True,
        )
        assert reason is None and receipt.state.value == "applied"
        resumed = engine.resume(task_ref, store.run_for_task(task_ref), {})
        assert resumed is not None
        final = None
        for _ in range(200):
            candidate = engine.get_state(resumed)
            if candidate is not None and candidate.status in {"failed", "unknown", "succeeded"}:
                final = candidate
                break
            await asyncio.sleep(0.05)
        assert final is not None
        assert final.status == "failed"
        assert final.result is not None
        assert final.result["error_category"] == "policy_denied"
        assert len(calls) == len(model.requests) == 1
        assert store.list_run_actions(run_ref) == before
    finally:
        await engine.close()
        store.dispose()
        await model.stop()
