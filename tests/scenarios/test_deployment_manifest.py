"""Deployment composition cold start over a real wheelhouse and release host.

The full offline chain with the composition bound: prepare a real
wheelhouse delivery, install it into a new venv (which writes the install
receipt), build a release-managed host with two signed artifacts, compose
the deployment manifest, and cold start the runner FROM THE INSTALLED
ENVIRONMENT with the manifest — verifying health, an authorized inventory
read, a cross-domain denial and shutdown over HTTP. The refusal side
covers a tampered composition digest, the wrong active release, a
current-policy revocation, and a drifted receipt — each refusing without
moving the pointer, after which the correct composition still boots.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.scenarios.tools import runner_harness

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
REPO_ROOT = SCRIPTS.parent
PUBLISHER = "publisher:acme"
KEY_ID = "acme-1"

pytestmark = pytest.mark.skipif(
    not runner_harness.uv_available(),
    reason="uv is not available; CI installs it and prepares the wheelhouse",
)


def _script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / name), *args],
        capture_output=True,
        text=True,
        timeout=900,
        cwd=str(REPO_ROOT),
    )


def _write_signed_artifact(tmp_root: Path, name: str, marker: str, private_pem: bytes) -> tuple[Path, str]:
    from hecate_runner.artifact_signing import canonical_manifest_bytes, sign_manifest
    from hecate_runtime.manifest import sha256_hex

    artifact = tmp_root / name
    (artifact / "files" / "agents/summary").mkdir(parents=True)
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"], "marker": marker}).encode()
    (artifact / "files/agents/summary/main.json").write_bytes(entry)
    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": "agents/summary/main.json",
        "files": [{"path": "agents/summary/main.json", "sha256": sha256_hex(entry), "size": len(entry)}],
        "publisher_ref": PUBLISHER,
        "signature_ref": "artifact-signature.json",
    }
    (artifact / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    raw = json.loads((artifact / "agent-manifest.json").read_text(encoding="utf-8"))
    envelope = sign_manifest(raw, private_pem, publisher_ref=PUBLISHER, key_id=KEY_ID)
    (artifact / "artifact-signature.json").write_text(json.dumps(envelope), encoding="utf-8")
    return artifact, sha256_hex(canonical_manifest_bytes(raw))


@pytest.fixture(scope="module")
def offline_env(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A real offline-installed environment and its delivery directory."""

    root = tmp_path_factory.mktemp("deployment-manifest")
    delivery = root / "delivery"
    result = _script("runner_env_prepare.py", "--target", str(delivery))
    assert result.returncode == 0, result.stderr[-3000:]
    digest = _read_digest(delivery)
    venv = root / "venv"
    result = _script(
        "runner_env_install.py",
        "--delivery",
        str(delivery),
        "--expected-manifest-sha256",
        digest,
        "--target",
        str(venv),
    )
    assert result.returncode == 0, result.stderr[-3000:]
    python_exe = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return python_exe, delivery


def _read_digest(delivery: Path) -> str:
    return json.loads((delivery / "environment.json").read_text(encoding="utf-8"))["manifest_sha256"]


class _DeploymentStack:
    """Release host + trust policy + installed interpreter + HTTP helpers."""

    def __init__(self, tmp_root: Path, offline_python: Path, delivery: Path, business_port: int) -> None:
        from hecate_runner.artifact_signing import generate_keypair

        self.root = tmp_root
        self.offline_python = offline_python
        self.delivery = delivery
        private_pem, public_pem = generate_keypair()
        self.public_pem = public_pem.decode("ascii")
        self.host = tmp_root / "host"
        runner_harness._write_profile(self.host, business_api_port=business_port)
        self.policy_path = tmp_root / "artifact-trust.json"
        self._write_policy()

        self.artifact_a, self.sha_a = _write_signed_artifact(tmp_root, "artifact-a", "version-a", private_pem)
        self.artifact_b, self.sha_b = _write_signed_artifact(tmp_root, "artifact-b", "version-b", private_pem)
        for artifact in (self.artifact_a, self.artifact_b):
            bundle = self.root / f"{artifact.name}.tar.gz"
            self._release_cli("pack", "--artifact", str(artifact), "--out", str(bundle))
            self._release_cli("install", "--host", str(self.host), "--bundle", str(bundle))
        self._generation = 0

    def _release_cli(self, *args: str) -> None:
        result = subprocess.run(
            [
                str(self.offline_python),
                "-I",
                "-m",
                "hecate_runner.release",
                *args,
                "--trust-policy",
                str(self.policy_path),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-2000:]

    def _write_policy(self, *, revoked: bool = False) -> Path:  # noqa: S107 - test fixture token
        self.policy_path.write_text(
            json.dumps(
                {
                    "artifact_trust_version": "1",
                    "publishers": {
                        PUBLISHER: {"keys": [{"key_id": KEY_ID, "public_key": self.public_pem}], "revoked": revoked}
                    },
                }
            ),
            encoding="utf-8",
        )
        return self.policy_path

    def activate(self, sha: str) -> None:
        self._release_cli(
            "activate",
            "--host",
            str(self.host),
            "--release",
            sha,
            "--expected-generation",
            str(self._generation),
        )
        self._generation = json.loads(self.pointer())["generation"]

    def pointer(self) -> bytes:
        return (self.host / "active.json").read_bytes()

    def compose(self, sha: str, tmp: Path) -> tuple[Path, str]:
        # The publisher is declared here and cross-checked at cold start
        # against the policy-verified admission — a wrong value refuses.
        deployment_path = tmp / f"deployment-{sha[:8]}.json"
        receipt_path = self.offline_python.parent.parent / "hecate-install-receipt.json"
        result = subprocess.run(
            [
                str(
                    self.offline_python.with_name(
                        "hecate-runner-deployment.exe" if sys.platform == "win32" else "hecate-runner-deployment"
                    )
                ),
                "generate",
                "--artifact-sha256",
                sha,
                "--publisher-ref",
                PUBLISHER,
                "--environment-manifest",
                str(self.delivery / "environment.json"),
                "--receipt",
                str(receipt_path),
                "--out",
                str(deployment_path),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-800:]
        document = json.loads(deployment_path.read_text(encoding="utf-8"))
        return deployment_path, document["deployment_sha256"]

    def start(self, deployment_path: Path, deployment_digest: str, policy_path: Path | None = None) -> tuple:
        port_file = self.root / f"port-{deployment_path.stem}.txt"
        port_file.unlink(missing_ok=True)
        log_file = self.root / f"log-{deployment_path.stem}.txt"
        env = {
            **{key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}},
            "RUNNER_SHUTDOWN_TOKEN": "sc-shutdown-token",
            "RUNNER_PORT_FILE": str(port_file),
        }
        with log_file.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                [
                    str(self.offline_python),
                    "-I",
                    "-m",
                    "hecate_runner",
                    "--host",
                    str(self.host),
                    "--artifact-trust",
                    str(policy_path or self.policy_path),
                    "--deployment-manifest",
                    str(deployment_path),
                    "--expected-deployment-sha256",
                    deployment_digest,
                ],
                cwd=str(self.root),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        serving = ""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            text = log_file.read_text(encoding="utf-8", errors="replace")
            if "serving on" in text:
                serving = text
                break
            if process.poll() is not None:
                serving = text
                break
            time.sleep(0.2)
        if process.poll() is None and "serving on" not in serving:
            self.stop(process)
            pytest.fail(f"Runner did not start: {self.log_text(deployment_path)[-2000:]}")
        return process, serving

    def log_text(self, deployment_path: Path) -> str:
        return (self.root / f"log-{deployment_path.stem}.txt").read_text(encoding="utf-8", errors="replace")

    def stop(self, process) -> None:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory, offline_env: tuple[Path, Path]):
    offline_python, delivery = offline_env
    business, calls = runner_harness._start_business_api()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("RUNNER_SHUTDOWN_TOKEN", "sc-shutdown-token")
            root = tmp_path_factory.mktemp("deployment-host")
            yield _DeploymentStack(root, offline_python, delivery, business.server_address[1]), calls
    finally:
        runner_harness.stop_business_api(business)


def test_install_receipt_exists_in_the_offline_venv(offline_env: tuple[Path, Path]) -> None:
    offline_python, _delivery = offline_env
    receipt = offline_python.parent.parent / "hecate-install-receipt.json"
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["environment_manifest_sha256"]
    assert document["receipt_sha256"]
    assert any(key.startswith("hecate-") for key in document["packages"])


def test_composed_cold_start_serves_the_real_acceptance_surface(stack, tmp_path: Path) -> None:
    deployment, calls = stack
    deployment.activate(deployment.sha_a)
    path, digest = deployment.compose(deployment.sha_a, tmp_path)
    process, serving = deployment.start(path, digest)
    try:
        assert "serving on" in serving, serving[-800:]
        port = int((deployment.root / f"port-{path.stem}.txt").read_text(encoding="utf-8"))
        runner = runner_harness.RunnerInstance(
            deployment.root, deployment.host, deployment.offline_python, process, port
        )
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        before = len(calls)
        for domain, sku, expected in [("domain_a", "SKU-A1", "ok"), ("domain_b", "SKU-B1", "authorization")]:
            status, submit = runner.request(
                "POST",
                "/runs",
                {"input": {"prompt": "check"}, "tool_arguments": {"query_inventory": {"domain": domain, "sku": sku}}},
            )
            assert status == 202, submit
            run_id = submit["run_ref"].split("/")[1]
            status, run = runner.wait_run(run_id)
            assert status == 200
            assert run["status"] == ("succeeded" if expected == "ok" else "failed"), run
            status, events = runner.request("GET", f"/runs/{run_id}/events")
            assert status == 200
            outcome = [e["outcome"] for e in events["events"] if e.get("type") == "tool_result"][-1]
            assert outcome["status"] == expected
            if expected == "ok":
                assert outcome["result"]["quantity"] == 100
            assert len(calls) == before + 1

        status, _ = runner.request("POST", "/admin/shutdown", {"token": "sc-shutdown-token"}, token=None)
        assert status in (200, 202, 204)
        assert process.wait(timeout=30) == 0
    finally:
        deployment.stop(process)


def _assert_refusal(
    stack: _DeploymentStack, path: Path, digest: str, diagnostic: str, *, policy: Path | None = None
) -> None:
    from hecate_runner.release import HostLock

    before = stack.pointer()
    process, log = stack.start(path, digest, policy_path=policy)
    try:
        assert "serving on" not in log
        assert process.wait(timeout=30) == 2, log
        assert diagnostic in log
        assert stack.pointer() == before
        with HostLock(stack.host):
            pass
    finally:
        stack.stop(process)


def test_tampered_composition_digest_refuses_without_moving_the_pointer(stack, tmp_path: Path) -> None:
    stack, _calls = stack
    stack.activate(stack.sha_a)
    deployment_path, deployment_digest = stack.compose(stack.sha_a, tmp_path)
    forged = deployment_path.with_suffix(".forged.json")
    document = json.loads(deployment_path.read_text(encoding="utf-8"))
    document["artifact"]["manifest_sha256"] = stack.sha_b
    forged.write_text(json.dumps(document), encoding="utf-8")

    _assert_refusal(stack, forged, deployment_digest, "digest does not match its own content")


def test_wrong_active_release_refuses_and_the_composed_one_still_boots(stack, tmp_path: Path) -> None:
    stack, _calls = stack
    stack.activate(stack.sha_b)  # the manifest composes A; B is now active
    deployment_path, deployment_digest = stack.compose(stack.sha_a, tmp_path)
    _assert_refusal(stack, deployment_path, deployment_digest, "activate the composed release")

    # The refusal left the pointer and lock usable: the composed release
    # boots with the correct selection.
    stack.activate(stack.sha_a)
    process, serving = stack.start(deployment_path, deployment_digest)
    assert "serving on" in serving
    stack.stop(process)


def test_policy_revocation_refuses_a_valid_composition(stack, tmp_path: Path) -> None:
    stack, _calls = stack
    stack.activate(stack.sha_a)
    path, digest = stack.compose(stack.sha_a, tmp_path)
    try:
        revoked_policy = stack._write_policy(revoked=True)
        _assert_refusal(stack, path, digest, "revoked", policy=revoked_policy)
    finally:
        stack._write_policy()


def test_composed_start_refuses_actual_installed_package_drift(stack, tmp_path: Path) -> None:
    stack, _calls = stack
    stack.activate(stack.sha_a)
    path, digest = stack.compose(stack.sha_a, tmp_path)
    result = subprocess.run(
        [str(stack.offline_python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        cwd=stack.root,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    metadata = Path(result.stdout.strip()) / "scenario_drift-1.0.dist-info"
    metadata.mkdir()
    entry = metadata / "METADATA"
    try:
        entry.write_text("Name: scenario-drift\nVersion: 1.0\n", encoding="utf-8")
        _assert_refusal(stack, path, digest, "scenario-drift")
    finally:
        entry.unlink(missing_ok=True)
        metadata.rmdir()
    process, log = stack.start(path, digest)
    try:
        assert "serving on" in log
    finally:
        stack.stop(process)
