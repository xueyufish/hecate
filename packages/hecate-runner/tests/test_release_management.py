"""Release-managed host tests: offline install, cold-start switch, rollback.

Covers the real closed loop (install → activate → start the actual entry →
stop → switch → rollback), every refusal path (unsigned, revoked, tampered,
link bypass, incompatible candidates), the crash/commit boundaries, and the
guards that make switching a live or busy host impossible: the OS-level
host lock and the non-terminal durable task check.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.storage import SqlDurableStore
from hecate_runner import release as release_module
from hecate_runner.artifact_signing import canonical_manifest_bytes, load_trust_policy, sign_manifest
from hecate_runner.durable import DurableRuntime
from hecate_runner.release import (
    ACTIVE_POINTER_NAME,
    ReleaseError,
    install_release,
    list_releases,
    load_host_selection,
    read_active_selection,
)
from hecate_runner.release import (
    activate_release as _activate_release,
)
from hecate_runner.release import (
    rollback_release as _rollback_release,
)
from hecate_runtime.manifest import sha256_hex

PUBLISHER = "publisher:acme"
KEY_ID = "acme-2026-01"
ENVELOPE_NAME = "artifact-signature.json"
ENTRY_NAME = "agents/summary/main.json"


def activate_release(host, sha, policy):
    """Normal operator flow: read a generation, then submit the bounded operation."""
    active = read_active_selection(host)
    return _activate_release(host, sha, policy, expected_generation=active.generation if active else 0)


def rollback_release(host, policy):
    active = read_active_selection(host)
    return _rollback_release(host, policy, expected_generation=active.generation if active else 0)


def _keypair(tmp_path: Path) -> tuple[bytes, str]:
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


def _policy_path(tmp_path: Path, public_pem: str, *, revoked: bool = False) -> Path:
    policy = {
        "artifact_trust_version": "1",
        "publishers": {
            PUBLISHER: {
                "keys": [{"key_id": KEY_ID, "public_key": public_pem}],
                "revoked": revoked,
            }
        },
    }
    path = tmp_path / "artifact-trust.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def _policy(tmp_path: Path, public_pem: str, *, revoked: bool = False):
    """The loaded trust policy object, as the core management API expects."""

    return load_trust_policy(_policy_path(tmp_path, public_pem, revoked=revoked))


def _make_artifact(tmp_path: Path, name: str, marker: str, private_pem: bytes) -> tuple[Path, str]:
    """Build one signed artifact directory; returns (dir, manifest sha256)."""

    artifact = tmp_path / name
    (artifact / "files" / "agents/summary").mkdir(parents=True)
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"], "marker": marker}).encode()
    (artifact / "files" / ENTRY_NAME).write_bytes(entry)
    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": ENTRY_NAME,
        "files": [{"path": ENTRY_NAME, "sha256": sha256_hex(entry), "size": len(entry)}],
        "publisher_ref": PUBLISHER,
        "signature_ref": ENVELOPE_NAME,
    }
    (artifact / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    raw = json.loads((artifact / "agent-manifest.json").read_text(encoding="utf-8"))
    envelope = sign_manifest(raw, private_pem, publisher_ref=PUBLISHER, key_id=KEY_ID)
    (artifact / ENVELOPE_NAME).write_text(json.dumps(envelope), encoding="utf-8")
    return artifact, sha256_hex(canonical_manifest_bytes(raw))


def _write_host(tmp_path: Path, *, durable_url: str | None = None) -> Path:
    host = tmp_path / "host"
    host.mkdir()
    config: dict = {
        "host": "127.0.0.1",
        "port": 0,
        "evidence_dir": "evidence",
        "business_api_base": "http://127.0.0.1:59999",
        "model": {"backend": "stub"},
        "tool_allowlist": ["query_inventory"],
        "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
    }
    if durable_url is not None:
        config["durable"] = {"database_url": durable_url, "workspace": "standalone"}
    (host / "runner.json").write_text(json.dumps(config), encoding="utf-8")
    (host / "identity.json").write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "principal": "app-reader",
                        "role": "read_only",
                        "domains": ["domain_a"],
                        "credential": "file:secrets/app-reader-token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (host / "secrets").mkdir()
    (host / "secrets/app-reader-token").write_text("reader-secret-token", encoding="utf-8")
    return host


@pytest.fixture()
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


def test_install_publishes_release_and_list_reports_recorded_facts(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)

    assert install_release(host, artifact, policy) == sha
    assert (host / "releases" / sha / "files" / ENTRY_NAME).is_file()
    assert read_active_selection(host) is None

    infos = list_releases(host)
    assert [info.manifest_sha256 for info in infos] == [sha]
    assert infos[0].publisher_ref == PUBLISHER
    assert infos[0].active is False
    # Only whitelisted content is copied: no host config inside the release.
    assert not (host / "releases" / sha / "runner.json").exists()


def test_install_is_idempotent_for_identical_artifact(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)

    assert install_release(host, artifact, policy) == sha
    assert install_release(host, artifact, policy) == sha
    assert len(list((host / "releases").iterdir())) == 1


def test_install_refuses_existing_release_with_different_content(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact, policy)

    # Corrupt the published release on disk, then reinstall the same artifact.
    victim = host / "releases" / sha / "files" / ENTRY_NAME
    original = victim.read_bytes()
    victim.write_bytes(b'{"kind": "entry", "tools": ["query_inventory"], "marker": "corrupted"}')
    with pytest.raises(ReleaseError, match="different content"):
        install_release(host, artifact, policy)
    assert victim.read_bytes() != original  # the published release is untouched by the refusal


@pytest.mark.parametrize("mutate", ["unsigned", "tampered", "revoked"])
def test_install_refusals_leave_no_release_and_no_pointer(tmp_path, _shutdown_env, mutate: str) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem, revoked=mutate == "revoked")

    if mutate == "unsigned":
        (artifact / ENVELOPE_NAME).unlink()
    elif mutate == "tampered":
        tampered = b'{"kind": "entry", "tools": ["query_inventory"], "marker": "tampered"}'
        (artifact / "files" / ENTRY_NAME).write_bytes(tampered)

    with pytest.raises(ReleaseError):
        install_release(host, artifact, policy)
    assert not (host / "releases").exists() or not any((host / "releases").iterdir())
    assert not (host / ACTIVE_POINTER_NAME).exists()


def test_install_refuses_symlinked_artifact_file(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)

    outside = tmp_path / "outside-entry.json"
    outside.write_bytes(b'{"kind": "entry", "tools": ["query_inventory"], "marker": "outside"}')
    link = artifact / "files" / ENTRY_NAME
    link.unlink()
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform/account")
    with pytest.raises(ReleaseError, match="symlink"):
        install_release(host, artifact, policy)


def test_activate_switch_and_rollback_closed_loop(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)

    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)

    # No selection yet: startup refuses.
    with pytest.raises(ReleaseError, match="no active release"):
        load_host_selection(host, policy)

    activate_release(host, sha_a, policy)
    profile_a, lock = load_host_selection(host, policy)
    assert profile_a.artifact_directory.name == sha_a
    assert b"version-a" in profile_a.entry_content
    lock.release()

    activate_release(host, sha_b, policy)
    selection = read_active_selection(host)
    assert selection is not None and selection.manifest_sha256 == sha_b
    assert selection.previous == sha_a

    profile_b, lock = load_host_selection(host, policy)
    assert b"version-b" in profile_b.entry_content
    lock.release()

    rollback_release(host, policy)
    profile_again, lock = load_host_selection(host, policy)
    assert b"version-a" in profile_again.entry_content
    lock.release()

    # Rollback semantics are a swap with the recorded previous version.
    rollback_release(host, policy)
    selection = read_active_selection(host)
    assert selection is not None and selection.manifest_sha256 == sha_b


def test_rollback_refuses_without_previous_or_installed_release(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)

    with pytest.raises(ReleaseError, match="no active release"):
        rollback_release(host, policy)
    # A digest that was never installed cannot be activated either.
    with pytest.raises(ReleaseError, match="not installed"):
        activate_release(host, sha_a, policy)
    assert read_active_selection(host) is None

    # An active first release has no previous version to roll back to.
    install_release(host, artifact_a, policy)
    activate_release(host, sha_a, policy)
    with pytest.raises(ReleaseError, match="no recorded previous version"):
        rollback_release(host, policy)


def test_activation_refuses_when_current_policy_revokes_the_publisher(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)
    activate_release(host, sha_a, policy)

    revoked = _policy(tmp_path, public_pem, revoked=True)
    with pytest.raises(ReleaseError, match="current gates"):
        activate_release(host, sha_a, revoked)
    # Build a switch chain so a previous version exists, then revoke: the
    # rollback candidate is re-verified under the CURRENT policy and refused.
    activate_release(host, sha_b, policy)
    with pytest.raises(ReleaseError, match="current gates"):
        rollback_release(host, revoked)
    # The old selection is retained verbatim.
    assert read_active_selection(host).manifest_sha256 == sha_b


def test_activate_refuses_unknown_release(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    activate_release(host, sha_a, policy)

    unknown = "f" * 64
    with pytest.raises(ReleaseError, match="not installed"):
        activate_release(host, unknown, policy)
    assert read_active_selection(host).manifest_sha256 == sha_a


def test_activate_refuses_while_host_lock_is_held(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)

    # Another process holds the lock exactly the way a serving runner does.
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path\n"
            "from hecate_runner.release import HostLock\n"
            f"lock = HostLock(Path({str(host)!r}))\n"
            "lock.acquire()\n"
            "print('locked', flush=True)\n"
            "import time\n"
            "time.sleep(5)\n"
            "lock.release()\n",
        ],
        cwd=tmp_path,
        env={**os.environ, "RUNNER_SHUTDOWN_TOKEN": "shutdown-token-value"},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(50):
            if holder.stdout and holder.stdout.readline().strip() == "locked":
                break
            time.sleep(0.1)
        with pytest.raises(ReleaseError, match="host lock is held"):
            activate_release(host, sha_a, policy)
    finally:
        holder.kill()
        holder.wait()
    # After the holder releases (here: dies), activation succeeds.
    activate_release(host, sha_a, policy)


def test_concurrent_activations_serialize_to_a_valid_pointer(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    install_release(host, artifact_a, load_trust_policy(policy_path))
    install_release(host, artifact_b, load_trust_policy(policy_path))

    env = {**os.environ, "RUNNER_SHUTDOWN_TOKEN": "shutdown-token-value"}
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hecate_runner.release",
                "activate",
                "--host",
                str(host),
                "--release",
                sha,
                "--expected-generation",
                "0",
                "--trust-policy",
                str(policy_path),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for sha in (sha_a, sha_b)
    ]
    codes = [proc.wait() for proc in procs]
    # One decision wins; the competing request cannot reuse generation zero.
    assert sorted(codes) == [0, 2]
    selection = read_active_selection(host)
    assert selection.manifest_sha256 in {sha_a, sha_b}


def test_pointer_commit_failure_retains_previous_selection(tmp_path, _shutdown_env, monkeypatch) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)
    activate_release(host, sha_a, policy)

    real_replace = os.replace

    def failing_replace(src, dst, **kwargs):
        if str(dst).endswith(ACTIVE_POINTER_NAME):
            raise OSError("simulated crash at the pointer commit boundary")
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(release_module.os, "replace", failing_replace)
    with pytest.raises(ReleaseError, match="previous selection retained"):
        activate_release(host, sha_b, policy)
    # The pointer survived the simulated crash.
    assert read_active_selection(host).manifest_sha256 == sha_a
    monkeypatch.setattr(release_module.os, "replace", real_replace)
    # The retry commits cleanly.
    activate_release(host, sha_b, policy)
    assert read_active_selection(host).manifest_sha256 == sha_b


def test_staging_garbage_and_stray_files_are_never_selectable(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    activate_release(host, sha_a, policy)

    staging = host / "staging" / "half-written"
    (staging / "files").mkdir(parents=True)
    (staging / "agent-manifest.json").write_text('{"broken": true}', encoding="utf-8")
    (host / "releases" / "not-a-sha").mkdir()

    infos = list_releases(host)
    assert [info.manifest_sha256 for info in infos] == [sha_a]
    profile, lock = load_host_selection(host, policy)
    assert b"version-a" in profile.entry_content
    lock.release()


_BLOCKING_STATES = [
    TaskLifecycleState.QUEUED,
    TaskLifecycleState.RUNNING,
    TaskLifecycleState.WAITING_INPUT,
    TaskLifecycleState.WAITING_APPROVAL,
    TaskLifecycleState.RECONCILIATION_REQUIRED,
]


@pytest.mark.parametrize("state", _BLOCKING_STATES)
def test_non_terminal_durable_tasks_block_switch(tmp_path, _shutdown_env, state: TaskLifecycleState) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    database_url = f"sqlite:///{tmp_path / 'durable.db'}"
    host = _write_host(tmp_path, durable_url=database_url)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)

    # The first activation happens on a quiescent ledger: the guard passes.
    bootstrap = SqlDurableStore(database_url)
    bootstrap.create_schema()
    bootstrap.dispose()
    activate_release(host, sha_a, policy)

    # Live work appears: switching to the candidate is refused.
    store = SqlDurableStore(database_url)
    runtime = DurableRuntime(store, workspace="standalone")
    submission = runtime.submit(
        principal="app-reader", run_input={"prompt": "check"}, idempotency_key=None, domains=("domain_a",)
    )
    task_ref = submission.association.task_ref
    if state is not TaskLifecycleState.QUEUED:
        store.apply_task_state(task_ref, state)
    try:
        with pytest.raises(ReleaseError, match="non-terminal tasks"):
            activate_release(host, sha_b, policy)
        # The ledger is untouched: the same task is still there, unmodified.
        remaining = store.list_tasks({state})
        assert [record.task_ref.id for record in remaining] == [task_ref.id]
        assert remaining[0].lifecycle_state is state
        assert read_active_selection(host).manifest_sha256 == sha_a
    finally:
        store.dispose()


def test_unverifiable_durable_state_blocks_switch(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    database_path = tmp_path / "durable.db"
    host = _write_host(tmp_path, durable_url=f"sqlite:///{database_path}")
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)

    bootstrap = SqlDurableStore(f"sqlite:///{database_path}")
    bootstrap.create_schema()
    bootstrap.dispose()
    activate_release(host, sha_a, policy)

    # The ledger becomes unreadable: the switch is refused, not forced.
    database_path.write_bytes(b"this is not a sqlite database")
    with pytest.raises(ReleaseError, match="cannot be verified"):
        activate_release(host, sha_b, policy)
    assert read_active_selection(host).manifest_sha256 == sha_a


def test_main_host_entry_refusals(tmp_path, _shutdown_env, capsys) -> None:
    from hecate_runner.__main__ import main

    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)

    # --host requires --artifact-trust.
    assert main(["--host", str(host), "--business-api", "http://127.0.0.1:9"]) == 2
    assert "--host requires --artifact-trust" in capsys.readouterr().err

    # --host and --profile are mutually exclusive (argparse exits 2).
    with pytest.raises(SystemExit) as excinfo:
        main(["--host", str(host), "--profile", str(host), "--artifact-trust", str(policy_path)])
    assert excinfo.value.code == 2

    # No committed selection: refuse before any resource initialization.
    assert (
        main(["--host", str(host), "--artifact-trust", str(policy_path), "--business-api", "http://127.0.0.1:9"]) == 2
    )
    assert "no active release" in capsys.readouterr().err
    assert not (host / "evidence").exists()

    # Installed + activated, but the release content was tampered afterwards:
    # startup re-verifies and refuses.
    install_release(host, artifact_a, load_trust_policy(policy_path))
    activate_release(host, sha_a, load_trust_policy(policy_path))
    (host / "releases" / sha_a / "files" / ENTRY_NAME).write_bytes(
        b'{"kind": "entry", "tools": ["query_inventory"], "marker": "tampered"}'
    )
    assert (
        main(["--host", str(host), "--artifact-trust", str(policy_path), "--business-api", "http://127.0.0.1:9"]) == 2
    )
    assert "digest mismatch" in capsys.readouterr().err


def test_real_entry_starts_the_committed_release_and_releases_the_lock(tmp_path, _shutdown_env, monkeypatch) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    installed_policy = load_trust_policy(policy_path)
    install_release(host, artifact_a, installed_policy)
    install_release(host, artifact_b, installed_policy)
    activate_release(host, sha_a, installed_policy)

    port_file = tmp_path / "runner.port"
    env = {
        **os.environ,
        "RUNNER_SHUTDOWN_TOKEN": "shutdown-token-value",
        "RUNNER_PORT_FILE": str(port_file),
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hecate_runner",
            "--host",
            str(host),
            "--artifact-trust",
            str(policy_path),
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        serving_line = ""
        for _ in range(100):
            if server.stdout is None:
                break
            line = server.stdout.readline()
            if "serving on" in line:
                serving_line = line
                break
            if server.poll() is not None:
                break
            time.sleep(0.1)
        assert f"release={sha_a}" in serving_line, serving_line
        port = int(port_file.read_text(encoding="utf-8"))

        # The serving runner holds the host lock: switching is impossible.
        with pytest.raises(ReleaseError, match="host lock is held"):
            activate_release(host, sha_b, load_trust_policy(policy_path))

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/admin/shutdown",
            data=json.dumps({"token": "shutdown-token-value"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # The response may race the server teardown; the exit code decides.
        with contextlib.suppress(urllib.error.HTTPError, urllib.error.URLError):
            urllib.request.urlopen(request, timeout=10).read()
        assert server.wait(timeout=30) == 0

    finally:
        if server.poll() is None:
            server.kill()
            server.wait()

    # The lock died with the process: switching works again, and the run
    # data stayed in the stable host location, not inside any release.
    activate_release(host, sha_b, load_trust_policy(policy_path))
    assert read_active_selection(host).manifest_sha256 == sha_b
    assert (host / "evidence").is_dir()
    assert not (host / "releases" / sha_a / "evidence").exists()


def test_invalid_manifest_cannot_write_outside_staging(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    source_root = tmp_path / "source"
    nested = source_root / "nested"
    nested.mkdir(parents=True)
    artifact, _sha = _make_artifact(nested, "artifact", "version-a", private_pem)
    host = _write_host(tmp_path)
    original = (host / "runner.json").read_bytes()
    malicious = b"must never overwrite host config"
    (source_root / "runner.json").write_bytes(malicious)
    manifest_path = artifact / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["files"].append({"path": "../../../runner.json", "sha256": sha256_hex(malicious), "size": len(malicious)})
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReleaseError):
        install_release(host, artifact, _policy(tmp_path, public_pem))
    assert (host / "runner.json").read_bytes() == original


def test_install_accepts_nested_signature_reference(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    manifest_path = artifact / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["signature_ref"] = "signatures/current.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    (artifact / "signatures").mkdir()
    (artifact / raw["signature_ref"]).write_text(
        json.dumps(sign_manifest(raw, private_pem, publisher_ref=PUBLISHER, key_id=KEY_ID)), encoding="utf-8"
    )
    host = _write_host(tmp_path)
    sha = install_release(host, artifact, _policy(tmp_path, public_pem))
    assert (host / "releases" / sha / raw["signature_ref"]).is_file()


@pytest.mark.parametrize("operation", ["startup", "activate"])
def test_release_id_must_match_the_verified_manifest(tmp_path, _shutdown_env, operation) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)
    activate_release(host, sha_a, policy)
    # Another genuinely signed artifact must not masquerade as release A.
    for name in ("agent-manifest.json", ENVELOPE_NAME, "files/" + ENTRY_NAME):
        (host / "releases" / sha_a / name).write_bytes((host / "releases" / sha_b / name).read_bytes())
    if operation == "startup":
        with pytest.raises(ReleaseError, match="digest|identity"):
            profile, lock = load_host_selection(host, policy)
            lock.release()
    else:
        with pytest.raises(ReleaseError, match="digest|identity"):
            activate_release(host, sha_a, policy)


def test_startup_failure_releases_host_lock(tmp_path, _shutdown_env) -> None:
    from hecate_runner.__main__ import main

    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    policy = load_trust_policy(policy_path)
    install_release(host, artifact, policy)
    activate_release(host, sha, policy)
    config_path = host / "runner.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.pop("business_api_base")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    assert main(["--host", str(host), "--artifact-trust", str(policy_path)]) == 2
    with release_module.HostLock(host):
        pass


def test_stale_generation_cannot_overwrite_an_aba_selection(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)
    activate_release(host, sha_a, policy)
    old = read_active_selection(host)
    activate_release(host, sha_b, policy)
    activate_release(host, sha_a, policy)
    current = (host / ACTIVE_POINTER_NAME).read_bytes()
    with pytest.raises(ReleaseError, match="generation conflict"):
        _activate_release(host, sha_b, policy, expected_generation=old.generation)
    with pytest.raises(ReleaseError, match="generation conflict"):
        _rollback_release(host, policy, expected_generation=old.generation)
    assert (host / ACTIVE_POINTER_NAME).read_bytes() == current


@pytest.mark.parametrize("change", ["remove", "different"])
@pytest.mark.parametrize("operation", ["activate", "startup"])
def test_database_binding_cannot_be_changed_to_skip_old_work(tmp_path, _shutdown_env, change, operation) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    original_url = f"sqlite:///{tmp_path / 'original.db'}"
    other_url = f"sqlite:///{tmp_path / 'empty.db'}"
    for url in (original_url, other_url):
        bootstrap = SqlDurableStore(url)
        bootstrap.create_schema()
        bootstrap.dispose()
    host = _write_host(tmp_path, durable_url=original_url)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact, policy)
    activate_release(host, sha, policy)
    store = SqlDurableStore(original_url)
    try:
        runtime = DurableRuntime(store, workspace="standalone")
        runtime.submit(
            principal="app-reader", run_input={"prompt": "check"}, idempotency_key=None, domains=("domain_a",)
        )
        config_path = host / "runner.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if change == "remove":
            config.pop("durable")
        else:
            config["durable"]["database_url"] = other_url
        config_path.write_text(json.dumps(config), encoding="utf-8")
        before = (host / ACTIVE_POINTER_NAME).read_bytes()
        with pytest.raises(ReleaseError, match="database binding changed"):
            if operation == "activate":
                activate_release(host, sha, policy)
            else:
                profile, lock = load_host_selection(host, policy)
                lock.release()
        assert (host / ACTIVE_POINTER_NAME).read_bytes() == before
        assert len(store.list_tasks({TaskLifecycleState.QUEUED})) == 1
    finally:
        store.dispose()


@pytest.mark.parametrize("url", ["sqlite:///relative.db", "sqlite:///:memory:", "postgresql+psycopg://localhost/db"])
def test_release_mode_refuses_unverifiable_database_locations(tmp_path, _shutdown_env, url) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path, durable_url=url)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact, policy)
    with pytest.raises(ReleaseError, match="absolute local SQLite"):
        activate_release(host, sha, policy)
    assert read_active_selection(host) is None


def test_missing_ledger_is_not_created_by_activation(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    database = tmp_path / "missing.db"
    host = _write_host(tmp_path, durable_url=f"sqlite:///{database}")
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact, policy)
    with pytest.raises(ReleaseError, match="cannot be verified"):
        activate_release(host, sha, policy)
    assert not database.exists()


def test_reinstall_does_not_accept_a_corrupt_installed_signature(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact, policy)
    signature = host / "releases" / sha / ENVELOPE_NAME
    signature.write_text("{}", encoding="utf-8")
    with pytest.raises(ReleaseError, match="different content"):
        install_release(host, artifact, policy)
    assert signature.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_process_death_at_pointer_commit_preserves_a_complete_selection(tmp_path, _shutdown_env, boundary) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    policy = load_trust_policy(policy_path)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)
    activate_release(host, sha_a, policy)
    script = (
        "import os\nfrom pathlib import Path\nfrom hecate_runner import release as r\n"
        "from hecate_runner.artifact_signing import load_trust_policy\n"
        "commit = r._commit_active_selection\n"
        "def crash(*args, **kwargs):\n"
        + ("    commit(*args, **kwargs)\n" if boundary == "after" else "")
        + "    os._exit(17)\n"
        "r._commit_active_selection = crash\n"
        f"r.activate_release(Path({str(host)!r}), {sha_b!r}, "
        f"load_trust_policy(Path({str(policy_path)!r})), expected_generation=1)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 17, result.stderr
    selection = read_active_selection(host)
    assert selection.manifest_sha256 == (sha_b if boundary == "after" else sha_a)
    assert selection.generation == (2 if boundary == "after" else 1)
    with release_module.HostLock(host):
        pass


def test_process_death_during_staging_never_publishes_a_release(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    script = (
        "import os\nfrom pathlib import Path\nfrom hecate_runner import release as r\n"
        "from hecate_runner.artifact_signing import load_trust_policy\n"
        "r._verify_staged_content = lambda path: os._exit(18)\n"
        f"r.install_release(Path({str(host)!r}), Path({str(artifact)!r}), "
        f"load_trust_policy(Path({str(policy_path)!r})))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 18, result.stderr
    assert list_releases(host) == []
    assert read_active_selection(host) is None
    assert install_release(host, artifact, load_trust_policy(policy_path)) == sha


def test_successful_switch_and_rollback_leave_completed_history_unchanged(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact_a, sha_a = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    database = tmp_path / "history.db"
    database_url = f"sqlite:///{database}"
    store = SqlDurableStore(database_url)
    store.create_schema()
    host = _write_host(tmp_path, durable_url=database_url)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact_a, policy)
    install_release(host, artifact_b, policy)
    activate_release(host, sha_a, policy)
    try:
        runtime = DurableRuntime(store, workspace="standalone")
        submission = runtime.submit(
            principal="app-reader", run_input={"prompt": "check"}, idempotency_key=None, domains=("domain_a",)
        )
        store.apply_task_state(
            submission.association.task_ref,
            TaskLifecycleState.SUCCEEDED,
            extra_update={"result": {"content": "retained"}},
        )
    finally:
        store.dispose()
    before = database.read_bytes()
    activate_release(host, sha_b, policy)
    rollback_release(host, policy)
    assert database.read_bytes() == before


def test_exception_during_boot_releases_host_lock(tmp_path, _shutdown_env, monkeypatch) -> None:
    from hecate_runner import __main__ as runner_main

    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    policy = load_trust_policy(policy_path)
    install_release(host, artifact, policy)
    activate_release(host, sha, policy)

    def fail_boot(*args, **kwargs):
        raise OSError("injected bind failure")

    monkeypatch.setattr(runner_main, "_run_profile", fail_boot)
    with pytest.raises(OSError, match="injected bind failure"):
        runner_main.main(["--host", str(host), "--artifact-trust", str(policy_path)])
    with release_module.HostLock(host):
        pass


def test_composition_refusal_preserves_durable_history_and_releases_the_lock(
    tmp_path, _shutdown_env, monkeypatch
) -> None:
    from types import SimpleNamespace

    from hecate_runner.deployment import DeploymentManifest

    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact", "version-a", private_pem)
    database = tmp_path / "history.db"
    database_url = f"sqlite:///{database}"
    host = _write_host(tmp_path, durable_url=database_url)
    policy = _policy(tmp_path, public_pem)
    install_release(host, artifact, policy)
    store = SqlDurableStore(database_url)
    try:
        store.create_schema()
        activate_release(host, sha, policy)
        runtime = DurableRuntime(store, workspace="standalone")
        runtime.submit(
            principal="app-reader", run_input={"prompt": "check"}, idempotency_key=None, domains=("domain_a",)
        )
    finally:
        store.dispose()

    monkeypatch.setattr(
        release_module,
        "sys",
        SimpleNamespace(flags=SimpleNamespace(isolated=1), prefix=str(tmp_path / "venv"), base_prefix=sys.base_prefix),
    )
    pointer = host / ACTIVE_POINTER_NAME
    before = pointer.read_bytes(), database.read_bytes()
    deployment = DeploymentManifest("f" * 64, PUBLISHER, "c" * 64, "d" * 64)
    with pytest.raises(ReleaseError, match="activate the composed release"):
        load_host_selection(host, policy, deployment=deployment)
    assert (pointer.read_bytes(), database.read_bytes()) == before
    with release_module.HostLock(host):
        pass
