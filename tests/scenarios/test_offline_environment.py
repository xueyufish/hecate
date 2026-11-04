"""Offline Runner environment delivery: locked wheelhouse, verified install.

Gates the wheelhouse contract end to end: preparation builds the workspace
wheels plus the hash-pinned third-party closure and a verifiable manifest;
installation refuses a manifest digest that does not match the
operator-provided value, missing, tampered or extra wheels, an
incompatible Python/platform target, and illegal dependency sources
(URLs, VCS, editable, index options, unpinned requirements) — always
without touching the target or any existing environment. The happy path
then starts the real Runner from the offline-installed environment and
runs the SC01/SC02 acceptance surface over HTTP: health, authorized
inventory read, cross-domain denial, and shutdown.

Preparation downloads the locked wheels. Installation uses the verified
local wheel paths and ignores inherited pip configuration.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.scenarios.tools import runner_harness

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PREPARE = SCRIPTS / "runner_env_prepare.py"
INSTALL = SCRIPTS / "runner_env_install.py"

pytestmark = pytest.mark.skipif(
    not runner_harness.uv_available(),
    reason="uv is not available; CI installs it and prepares the wheelhouse",
)


def _run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(script), *args],
        capture_output=True,
        text=True,
        timeout=900,
        cwd=str(SCRIPTS.parent),
    )


def _read_manifest(delivery: Path) -> dict:
    return json.loads((delivery / "environment.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def delivery(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("offline-delivery-root") / "delivery"
    result = _run_script(PREPARE, "--target", str(target))
    assert result.returncode == 0, result.stderr[-3000:]
    return target


def _digest(delivery: Path) -> str:
    return _read_manifest(delivery)["manifest_sha256"]


def _install(delivery: Path, target: Path, digest: str | None = None) -> subprocess.CompletedProcess:
    return _run_script(
        INSTALL,
        "--delivery",
        str(delivery),
        "--expected-manifest-sha256",
        digest or _digest(delivery),
        "--target",
        str(target),
    )


# -- preparation -------------------------------------------------------------


def test_prepare_records_verifiable_facts(delivery: Path) -> None:
    manifest = _read_manifest(delivery)
    assert manifest["format_version"] == 1
    assert manifest["python_target"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert ":" in manifest["platform"]
    wheel_names = {entry["name"] for entry in manifest["wheels"]}
    assert {"hecate_runtime", "hecate_governance", "hecate_durable", "hecate_runner"} <= wheel_names
    for entry in manifest["wheels"]:
        assert len(entry["sha256"]) == 64
        assert (delivery / "wheelhouse" / entry["filename"]).is_file()
    assert manifest["source"]["git_commit"] or manifest["source"]["uv_lock_sha256"]
    assert manifest["requirements_lock_sha256"]
    assert manifest["build_tools"]["pip"].startswith("pip ")
    # Recorded honestly, not assumed absent: whichever packages ship without
    # license metadata are listed as such.
    assert isinstance(manifest["license_metadata_missing"], list)


def test_prepare_refuses_an_existing_target(delivery: Path) -> None:
    result = _run_script(PREPARE, "--target", str(delivery))
    assert result.returncode == 2
    assert "already exists" in result.stderr


# -- installation refusals ---------------------------------------------------


def test_install_refuses_a_foreign_manifest_digest(delivery: Path, tmp_path: Path) -> None:
    target = tmp_path / "venv-digest"
    result = _install(delivery, target, digest="0" * 64)
    assert result.returncode == 2
    assert "operator-provided digest" in result.stderr
    assert not target.exists()


def test_install_refuses_a_tampered_wheel_without_touching_the_target(delivery: Path, tmp_path: Path) -> None:
    wheels = sorted((delivery / "wheelhouse").glob("hecate_runner-*.whl"))
    victim = wheels[0]
    original = victim.read_bytes()
    payload = bytearray(original)
    payload[len(payload) // 2] ^= 0xFF
    victim.write_bytes(bytes(payload))
    try:
        target = tmp_path / "venv-tampered"
        result = _install(delivery, target)
        assert result.returncode == 2
        assert "does not match the manifest digest" in result.stderr
        assert not target.exists()
    finally:
        victim.write_bytes(original)


def test_install_refuses_missing_and_extra_wheels(delivery: Path, tmp_path: Path) -> None:
    wheelhouse = delivery / "wheelhouse"
    victim = sorted(wheelhouse.glob("hecate_governance-*.whl"))[0]
    saved = victim.read_bytes()
    victim.unlink()
    try:
        target = tmp_path / "venv-missing"
        result = _install(delivery, target)
        assert result.returncode == 2
        assert "missing manifest-listed wheels" in result.stderr
        assert not target.exists()
    finally:
        victim.write_bytes(saved)

    stray = wheelhouse / "extra-1.0-py3-none-any.whl"
    stray.write_bytes(saved)
    try:
        target = tmp_path / "venv-extra"
        result = _install(delivery, target)
        assert result.returncode == 2
        assert "does not list" in result.stderr
        assert not target.exists()
    finally:
        stray.unlink()


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("python_target", "3.99", "targets Python"),
        ("platform", "foreign:target", "targets platform"),
        ("python_abi", "foreign-abi", "different Python implementation or ABI"),
    ],
)
def test_install_refuses_an_incompatible_target(
    delivery: Path, tmp_path: Path, field: str, value: str, diagnostic: str
) -> None:
    work = tmp_path / "delivery-incompatible"
    shutil.copytree(delivery, work)
    manifest = _read_manifest(work)
    manifest[field] = value
    sys.path.insert(0, str(SCRIPTS))
    try:
        import runner_env_prepare as prepare_module

        manifest["manifest_sha256"] = prepare_module.manifest_digest(manifest)
    finally:
        sys.path.pop(0)
    (work / "environment.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    target = tmp_path / "venv-incompatible"
    result = _install(work, target, digest=manifest["manifest_sha256"])
    assert result.returncode == 2
    assert diagnostic in result.stderr
    assert not target.exists()


def test_install_refuses_illegal_dependency_sources(delivery: Path, tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    try:
        import runner_env_install as install_module
        import runner_env_prepare as prepare_module

        for injected, message in [
            ("-e git+https://example.example/x#egg=x\n", "URL, source option, or invalid pin"),
            ("--index-url https://example.example/simple\n", "URL, source option, or invalid pin"),
            ("requests @ https://example.example/requests.whl\n", "URL, source option, or invalid pin"),
            ("somepkg==1.0\n", "carries no sha256 hash"),
            (
                "somepkg==1.0 " + "\\" + "\n    --find-links https://example.example\n",
                "invalid sha256 continuation",
            ),
        ]:
            work = tmp_path / f"delivery-illegal-{abs(hash(injected))}"
            shutil.copytree(delivery, work)
            lock = work / "requirements.lock"
            lock.write_text(lock.read_text(encoding="utf-8") + injected, encoding="utf-8")
            manifest = _read_manifest(work)
            # A fully consistent malicious delivery: the lock digest inside
            # the manifest matches the injected lock, so the installer must
            # catch the illegal SOURCE itself, not just a digest mismatch.
            manifest["requirements_lock_sha256"] = install_module.sha256_file(lock)
            manifest["manifest_sha256"] = prepare_module.manifest_digest(manifest)
            (work / "environment.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            target = tmp_path / "venv-illegal"
            result = _install(work, target, digest=manifest["manifest_sha256"])
            assert result.returncode == 2, (injected, result.stderr[-500:])
            assert message in result.stderr
            assert not target.exists()
            # The installer's own block parser agrees with the CLI refusal.
            with pytest.raises(install_module.InstallError):
                install_module._audit_requirements_lock(lock, manifest)
    finally:
        sys.path.pop(0)


def test_install_refuses_an_existing_target(delivery: Path, tmp_path: Path) -> None:
    target = tmp_path / "venv-existing"
    target.mkdir()
    marker = target / "keep"
    marker.write_text("existing content", encoding="utf-8")
    result = _install(delivery, target)
    assert result.returncode == 2
    assert "already exists" in result.stderr
    assert target.is_dir()
    assert marker.read_text(encoding="utf-8") == "existing content"


def test_failed_install_removes_the_new_target(delivery: Path, tmp_path: Path) -> None:
    work = tmp_path / "incomplete-delivery"
    shutil.copytree(delivery, work)
    manifest = _read_manifest(work)
    missing = next(entry for entry in manifest["wheels"] if entry["name"] == "jsonschema")
    manifest["wheels"].remove(missing)
    (work / "wheelhouse" / missing["filename"]).unlink()
    sys.path.insert(0, str(SCRIPTS))
    try:
        import runner_env_prepare as prepare_module

        digest = prepare_module.manifest_digest(manifest)
    finally:
        sys.path.pop(0)
    manifest["manifest_sha256"] = digest
    (work / "environment.json").write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "venv"
    result = _install(work, target)
    assert result.returncode == 2
    assert "jsonschema" in result.stderr
    assert not target.exists()


@pytest.fixture(scope="module")
def offline_python(delivery: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Install from a standalone script with hostile pip settings present."""
    tmp_root = tmp_path_factory.mktemp("offline-env")
    target = tmp_root / "venv with spaces"
    script = shutil.copy2(INSTALL, tmp_root)
    poison = tmp_root / "untrusted-requirements.txt"
    poison.write_text("unexpected @ https://127.0.0.1:1/unexpected.whl\n", encoding="utf-8")
    config = tmp_root / "pip.ini"
    config.write_text("[install]\nrequirement = " + str(poison) + "\n", encoding="utf-8")
    redirected = tmp_root / "redirected"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(script),
            "--delivery",
            str(delivery),
            "--expected-manifest-sha256",
            _digest(delivery),
            "--target",
            str(target),
        ],
        env={
            **os.environ,
            "PIP_CONFIG_FILE": str(config),
            "PIP_REQUIREMENT": str(poison),
            "PIP_TARGET": str(redirected),
        },
        cwd=tmp_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert not redirected.exists()
    return target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def test_offline_environment_runs_the_installed_entry_point(offline_python: Path) -> None:
    workdir = offline_python.parents[2]
    for command in (
        [str(offline_python), "-I", "-m", "pip", "check"],
        [
            str(offline_python),
            "-I",
            "-c",
            "from importlib.util import find_spec; from pathlib import Path; import sys; "
            "import hecate_runner; assert Path(hecate_runner.__file__).is_relative_to(sys.prefix); "
            "assert find_spec('hecate') is None",
        ],
        [str(offline_python.with_name("hecate-runner.exe" if sys.platform == "win32" else "hecate-runner")), "--help"],
    ):
        result = subprocess.run(command, cwd=workdir, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr or result.stdout


def test_offline_runner_serves_and_shuts_down(offline_python: Path, tmp_path: Path) -> None:
    business_server, calls = runner_harness._start_business_api()
    try:
        profile_dir = tmp_path / "profile"
        runner_harness._write_profile(profile_dir, business_api_port=business_server.server_address[1])
        instance = runner_harness.start_runner_from(tmp_path, profile_dir, offline_python)
        try:
            status, body = instance.request("GET", "/healthz", token=None)
            assert status == 200 and body["ready"] is True
            status, body = instance.request("GET", "/capabilities", token=None)
            assert status == 200 and body["submit"] == "enforced"

            for domain, expected in [("domain_a", "ok"), ("domain_b", "authorization")]:
                status, submit = instance.request(
                    "POST",
                    "/runs",
                    {
                        "input": {"prompt": "check"},
                        "tool_arguments": {"query_inventory": {"domain": domain, "sku": "SKU-A1"}},
                    },
                )
                assert status == 202, submit
                run_id = submit["run_ref"].split("/")[1]
                status, run = instance.wait_run(run_id)
                assert status == 200
                assert run["status"] == ("succeeded" if expected == "ok" else "failed"), run
                status, events = instance.request("GET", f"/runs/{run_id}/events")
                assert status == 200
                outcome = [event["outcome"] for event in events["events"] if event.get("type") == "tool_result"][-1]
                assert outcome["status"] == expected
                if expected == "ok":
                    assert outcome["result"]["quantity"] == 100
                assert len(calls) == 1  # Cross-domain access is refused before dispatch.

            status, _body = instance.request("POST", "/admin/shutdown", {"token": "sc-shutdown-token"}, token=None)
            assert status in (200, 202, 204)
            assert instance.process.wait(timeout=30) == 0
        finally:
            instance.stop()
    finally:
        runner_harness.stop_business_api(business_server)
