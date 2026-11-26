"""Installation and explicit startup of signed Runner artifacts."""

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
from hecate_runner.artifact_signing import canonical_manifest_bytes, load_trust_policy, sign_manifest
from hecate_runner.release import HostLock, ReleaseError, install_release, list_releases
from hecate_runtime.manifest import sha256_hex

PUBLISHER = "publisher:acme"
KEY_ID = "acme-2026-01"
ENVELOPE_NAME = "artifact-signature.json"
ENTRY_NAME = "agents/summary/main.json"


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

    infos = list_releases(host)
    assert [info.manifest_sha256 for info in infos] == [sha]
    assert infos[0].publisher_ref == PUBLISHER
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
def test_install_refusals_leave_no_release(tmp_path, _shutdown_env, mutate: str) -> None:
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
    assert install_release(host, artifact, load_trust_policy(policy_path)) == sha


def test_runner_starts_the_explicit_artifact_with_current_trust(tmp_path, _shutdown_env, monkeypatch, capsys) -> None:
    from hecate_runner import __main__ as entry

    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    install_release(host, artifact, load_trust_policy(policy_path))
    installed = host / "releases" / sha
    args = ["--profile", str(host), "--artifact", str(installed)]

    started = []

    def capture(profile, _args, *, trust_guard=None):
        started.append(profile)
        return 0

    monkeypatch.setattr(entry, "_run_profile", capture)
    assert entry.main(args) == 2
    assert "--artifact requires --artifact-trust" in capsys.readouterr().err

    trust_args = ["--artifact-trust", str(policy_path)]
    signed_args = [*args, *trust_args]
    assert entry.main(signed_args) == 0
    assert len(started) == 1
    profile = started[0]
    assert profile.directory == host.resolve()
    assert profile.artifact_directory == installed.resolve()
    assert profile.artifact_admission is not None
    assert b"version-a" in profile.entry_content

    wrong_digest = installed.with_name("0" * 64)
    installed.rename(wrong_digest)
    wrong_args = ["--profile", str(host), "--artifact", str(wrong_digest), *trust_args]
    assert entry.main(wrong_args) == 2
    assert "does not match its signed manifest digest" in capsys.readouterr().err
    wrong_digest.rename(installed)

    _policy_path(tmp_path, public_pem, revoked=True)
    assert entry.main(signed_args) == 2
    assert "revoked" in capsys.readouterr().err
    assert len(started) == 1


def test_explicit_artifact_serves_with_a_stable_profile_and_lock(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    host = _write_host(tmp_path)
    policy_path = _policy_path(tmp_path, public_pem)
    install_release(host, artifact, load_trust_policy(policy_path))
    installed = host / "releases" / sha
    port_file = tmp_path / "runner.port"

    with subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hecate_runner",
            "--profile",
            str(host),
            "--artifact",
            str(installed),
            "--artifact-trust",
            str(policy_path),
        ],
        cwd=tmp_path,
        env={**os.environ, "RUNNER_PORT_FILE": str(port_file)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as server:
        try:
            deadline = time.monotonic() + 60
            while not port_file.exists() and server.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert port_file.is_file(), "Runner did not publish its listening port"

            with pytest.raises(ReleaseError, match="host lock is held"), HostLock(host):
                pytest.fail("a serving Runner must own the profile lock")

            shutdown = urllib.request.Request(
                f"http://127.0.0.1:{int(port_file.read_text(encoding='utf-8'))}/admin/shutdown",
                data=json.dumps({"token": "shutdown-token-value"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # The server can close the connection while sending its shutdown response.
            with contextlib.suppress(urllib.error.URLError):
                urllib.request.urlopen(shutdown, timeout=10).close()
            stdout, stderr = server.communicate(timeout=30)
            assert server.returncode == 0, stderr
            assert f"artifact={sha}" in stdout
        finally:
            if server.poll() is None:
                server.kill()
                server.wait(timeout=30)

    with HostLock(host):
        assert (host / "evidence").is_dir()
    assert not (installed / "evidence").exists()
